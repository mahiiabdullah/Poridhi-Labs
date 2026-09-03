# Lab 22.1: Running Celery under `supervisord` (in-container init)

**Module 61 — Deployment and Monitoring**

This lab keeps the Celery worker and the Flask API alive inside a single container by using `supervisord` as PID 1. You kill the worker, watch `supervisord` bring it back up, and tail per-process logs from the host.

## Architecture

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.1/images/architecture-supervisord.svg" alt="Lab 22.1 supervisord architecture"></p>

The Flask API (`web`) and the Celery worker (`celery`) run as `supervisord` programs inside one container. `supervisord` is PID 1, so it owns both processes, restarts them on crash, and writes their logs to bind-mounted files. RabbitMQ runs in a separate broker stack on the same Docker network.

## Concept

| Term             | Description                                                                                  |
|------------------|----------------------------------------------------------------------------------------------|
| `supervisord`    | A process control system that runs as PID 1 inside a container and restarts child processes on exit. |
| `supervisorctl`  | The CLI client used to inspect status and control individual supervised programs.             |
| `auto-restart`   | A directive that tells `supervisord` to relaunch a program whenever it exits unexpectedly.   |
| Per-process log  | A dedicated log file that `supervisord` writes for each child program, bind-mounted to the host. |
| PID 1 reaping    | Linux behavior where the init process reaps zombie children. `supervisord` provides this in a container. |

When multiple long-running processes share a container, something must own them, restart them on crash, and reap any zombie children they leave behind. `supervisord` fills that role as the container's init system.

## What You Will Build

A single `app` container where `supervisord` manages two programs: `web` (Flask API on `:5000`) and `celery` (Celery worker). RabbitMQ runs in a separate broker stack on the `lab22-broker-net` Docker network. You kill the worker, watch `supervisord` bring it back up, and tail per-process logs from the host.

## Step 1: Create the project directories

```bash
mkdir -p ~/lab-22/app/src ~/lab-22/app/logs
mkdir -p ~/lab-22/broker
```

## Step 2: Confirm Docker and Compose

```bash
docker --version
docker compose version
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.1/images/docker%20--version.png" alt="Docker and Compose versions"></p>

## Step 3: Start the broker stack

```bash
cat > ~/lab-22/broker/docker-compose.yml << 'EOF'
services:
  rabbitmq:
    image: rabbitmq:3-management
    container_name: lab22-rabbitmq
    hostname: lab22-rabbitmq
    ports:
      - "5672:5672"
      - "15672:15672"
    environment:
      RABBITMQ_DEFAULT_USER: guest
      RABBITMQ_DEFAULT_PASS: guest
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "ping"]
      interval: 5s
      timeout: 3s
      retries: 15

volumes:
  rabbitmq_data:

networks:
  default:
    name: lab22-broker-net
    driver: bridge
EOF
```

```bash
cd ~/lab-22/broker
docker compose up -d
docker compose ps
```

`lab22-rabbitmq` shows `healthy` once it accepts AMQP.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.1/images/Step%203%20docker%20compose%20up.png" alt="Broker stack running"></p>

## Step 4: Write the application requirements

```bash
cat > ~/lab-22/app/requirements.txt << 'EOF'
flask==3.0.3
celery==5.4.0
kombu==5.3.7
setuptools>=68,<81
supervisor==4.2.5
EOF
```

`setuptools` provides `pkg_resources`, which `supervisor==4.2.5` imports at startup. `python:3.12-slim` no longer ships `setuptools`, so it has to be installed explicitly. The cap `<81` matters: `setuptools==81.0.0` removed `pkg_resources`, so without it `supervisord` exits with `ModuleNotFoundError: No module named 'pkg_resources'`.

## Step 5: Write `supervisord.conf`

```bash
cat > ~/lab-22/app/supervisord.conf << 'EOF'
[supervisord]
nodaemon=true
logfile=/app/logs/supervisord.log
pidfile=/tmp/supervisord.pid
loglevel=info

[unix_http_server]
file=/tmp/supervisor.sock

[supervisorctl]
serverurl=unix:///tmp/supervisor.sock

[rpcinterface:supervisor]
supervisor.rpcinterface_factory = supervisor.rpcinterface:make_main_rpcinterface

[program:web]
command=python /code/app.py
directory=/code
autostart=true
autorestart=true
startretries=5
stdout_logfile=/app/logs/web.log
stderr_logfile=/app/logs/web.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3

[program:celery]
command=celery -A celery_worker.celery worker --loglevel=info --concurrency=2
directory=/code
autostart=true
autorestart=true
startretries=5
stdout_logfile=/app/logs/celery.log
stderr_logfile=/app/logs/celery.log
stdout_logfile_maxbytes=10MB
stdout_logfile_backups=3
EOF
```

Two programs are supervised: `web` (Flask) and `celery` (worker). `nodaemon=true` keeps `supervisord` in the foreground so Docker sees it as PID 1.

## Step 6: Write the Dockerfile

```bash
cat > ~/lab-22/app/Dockerfile << 'EOF'
FROM python:3.12-slim

WORKDIR /code

RUN mkdir -p /app/logs

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY supervisord.conf /etc/supervisord.conf

CMD ["supervisord", "-c", "/etc/supervisord.conf"]
EOF
```

## Step 7: Write the Celery worker

```bash
cat > ~/lab-22/app/src/celery_worker.py << 'EOF'
# celery_worker.py
from celery import Celery

celery = Celery(
    "lab22",
    broker="amqp://guest:guest@lab22-rabbitmq:5672//",
    backend="rpc://",
)

celery.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)
EOF
```

## Step 8: Write the task module

```bash
cat > ~/lab-22/app/src/tasks.py << 'EOF'
# tasks.py
import time
from celery_worker import celery


@celery.task(name="tasks.process_job", bind=True, acks_late=True)
def process_job(self, job_id: str, duration: int = 5) -> dict:
    print(f"[Job {job_id}] started, duration={duration}s", flush=True)
    for second in range(1, duration + 1):
        time.sleep(1)
        print(f"[Job {job_id}] tick {second}/{duration}", flush=True)
    print(f"[Job {job_id}] done", flush=True)
    return {"job_id": job_id, "duration": duration, "status": "done"}
EOF
```

## Step 9: Write the Flask API

```bash
cat > ~/lab-22/app/src/app.py << 'EOF'
# app.py
import uuid
from flask import Flask, jsonify, request
from tasks import process_job

app = Flask(__name__)


@app.route("/jobs", methods=["POST"])
def create_job():
    payload = request.get_json(silent=True) or {}
    duration = int(payload.get("duration", 5))
    job_id = uuid.uuid4().hex[:8]

    process_job.apply_async(args=[job_id, duration])

    return jsonify({
        "status": "accepted",
        "job_id": job_id,
        "message": "Job published; worker managed by supervisord",
    }), 202


@app.route("/", methods=["GET"])
def index():
    return jsonify({"service": "lab22-supervisord", "status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
EOF
```

## Step 10: Write `docker-compose.yml` for the app

```bash
cat > ~/lab-22/app/docker-compose.yml << 'EOF'
services:
  app:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: lab22-app
    ports:
      - "5000:5000"
    volumes:
      - ./src:/code
      - ./logs:/app/logs

networks:
  default:
    name: lab22-broker-net
    external: true
EOF
```

## Step 11: Build and start the app

```bash
cd ~/lab-22/app
docker compose build --no-cache
docker compose up -d
```

`--no-cache` forces a clean image build so the new `requirements.txt` from Step 4 always lands in the image layer. A plain `--build` is not enough on some hosts — BuildKit can reuse the old `pip install` layer even after `requirements.txt` changes, which leaves the image without `setuptools` and `supervisord` exits with `ModuleNotFoundError`.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.1/images/Step%2011%20Build%20and%20start%20the%20app.png" alt="App stack built and started"></p>

## Step 12: Confirm both programs are running

```bash
docker exec lab22-app supervisorctl status
```

Both `web` and `celery` show `RUNNING` as child processes of `supervisord`.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.1/images/Step%2012%20Confirm%20both%20programs%20are%20running.png" alt="supervisorctl status showing web + celery RUNNING"></p>

## Step 13: Expose port 5000 in the lab UI

Open the **Load Balancer** modal in the lab UI (top-right). Run this once to find the IP to enter:

```bash
hostname -I
```

Sample output:

```
10.61.7.107 172.17.0.1 100.80.176.159 172.18.0.1
```

Use the first IP printed as `LB_IP`.

| Enter IP  | Enter Port             |
|-----------|------------------------|
| `LB_IP`   | `5000` (Flask API)     |

Click **Expose**. Copy the generated `.lb.poridhi.io` URL — the rest of the lab uses it as `<FLASK-LB-URL>`.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.1/images/LB_IP.%20Loadbalancer.png" alt="Load Balancer modal with LB_IP entry"></p>

## Step 14: Trigger a job from the LB URL

```bash
curl -X POST <FLASK-LB-URL>/jobs \
  -H "Content-Type: application/json" \
  -d '{"duration": 6}'
```

Tail the worker log:

```bash
tail -f ~/lab-22/app/logs/celery.log
```

Press `Ctrl+C` to stop tailing.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.1/images/Step%2014%20Trigger%20a%20job%20from%20the%20LB%20URL.png" alt="Triggering a job via the LB URL"></p>

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.1/images/Step%2014%20Tail%20the%20worker%20log.png" alt="Worker log tail showing job ticks"></p>

## Step 15: Kill the worker and watch it recover

```bash
docker exec lab22-app supervisorctl signal KILL celery
sleep 2
docker exec lab22-app supervisorctl status
```

`supervisord` reports `celery: signalled`, briefly shows `STARTING`, then returns to `RUNNING` with a fresh PID. The unacknowledged task is requeued by RabbitMQ because `acks_late=True` is set, and the restarted worker picks it up.

`supervisorctl signal KILL celery` forwards `SIGKILL` to the worker through the supervisor's own RPC channel, so no extra tools (`pkill`, `pgrep`, `kill`) are needed inside the container.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.1/images/Step%2015%20Kill%20the%20worker%20and%20watch%20it%20recover.png" alt="Worker back to RUNNING with a new PID"></p>

## Step 16: Stop the stack

```bash
cd ~/lab-22/app && docker compose down
cd ~/lab-22/broker && docker compose down -v
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.1/images/Step%2016%20Stop%20the%20stack.png" alt="App and broker stacks stopped"></p>

## Conclusion

You ran both the Flask API and the Celery worker inside one container under `supervisord`, watched it bring the worker back after a `SIGKILL`, and tailed per-process logs from the host. Pick this path when the worker shares a container with the API.
