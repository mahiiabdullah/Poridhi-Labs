# Lab 22: Managing Celery Workers with `supervisord`

## Module 61 — Deployment and Monitoring

This lab consolidates the Flask API and Celery worker into a single container. `supervisord` runs as PID 1 and starts both processes. If either crashes, `supervisord` restarts it automatically. `supervisorctl` lets you stop, start, and restart individual programs from outside the container without entering a shell. Per-process log files are written inside the container and mounted to the host so you can tail them directly.

---


## Architecture

![Lab 22 Architecture](./images/architecture.svg)

A single `app` container runs `supervisord` as PID 1. Two child programs are supervised:

* **`web`** — Flask API on port `5000`.
* **`celery`** — Celery worker that consumes tasks from RabbitMQ.

RabbitMQ itself runs in the separate broker stack from Lab 21 on the `lab22-broker-net` Docker network.

## Concept

| Term                       | Description                                                                                                |
|----------------------------|------------------------------------------------------------------------------------------------------------|
| Flower                     | A web-based tool for monitoring and administrating Celery clusters.                                        |
| Nginx                      | A web server acting as a reverse proxy to forward client requests to internal services.                   |
| Reverse Proxy              | A server that sits in front of backend applications and intercepts external requests.                      |
| HTTP Basic Authentication  | A method for an HTTP user agent to provide a username and password when making a request.                 |

A reverse proxy acts as an intermediary for requests from clients seeking resources from servers. Instead of exposing Flower directly to the internet, Nginx intercepts incoming HTTP traffic on port 80, enforces authentication, and routes authorized requests to the internally hosted Flower service on port 5555.

---

## What You Will Build

A single `app` container where `supervisord` manages two programs: `web` (Flask) and `celery` (Celery worker). RabbitMQ runs in a separate broker stack as in Lab 21. You interact with `supervisord` using `docker exec supervisorctl`, observe automatic process recovery by deliberately killing the worker mid-task, and read per-process log files from the host filesystem.

---

## Part A — Broker Stack

### Step 1: Create the project directories

```bash
mkdir -p ~/lab-22/broker ~/lab-22/app/src ~/lab-22/app/logs
cd ~/lab-22
```

### Step 2: Confirm Docker and Compose

```bash
docker --version
docker compose version
```

![Docker and Compose versions](./images/output-1.png)

### Step 3: Start the broker stack

Write the broker compose file:

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

Bring it up:

```bash
cd ~/lab-22/broker
docker compose up -d
```

![docker compose up -d output](./images/output-2.png)

Wait for the healthy status:

```bash
docker compose ps
```

![docker compose ps showing healthy](./images/output-3.png)

---

## Part B — Application Image

### Step 4: Write `requirements.txt`

```bash
cat > ~/lab-22/app/requirements.txt << 'EOF'
flask==3.0.3
celery==5.4.0
kombu==5.3.7
supervisor==4.2.5
EOF
```

> **Note:** `supervisor` is a Python package. Installing it with pip provides both `supervisord` (the daemon) and `supervisorctl` (the control CLI).

### Step 5: Write `supervisord.conf`

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

Key settings:

| Setting                       | Effect                                                                                              |
|-------------------------------|-----------------------------------------------------------------------------------------------------|
| `nodaemon=true`               | Keeps `supervisord` in the foreground so Docker sees it as PID 1 and the container stays alive.      |
| `autorestart=true`            | Restarts the program whenever it exits for any reason.                                              |
| `startretries=5`              | Gives up after five consecutive failed start attempts and marks the program as `FATAL`.              |
| `stdout_logfile` + `stderr_logfile` | Pointer to the same file per program so both streams appear together.                        |
| `[unix_http_server]`          | Enables `supervisorctl` commands over a Unix socket without a TCP port or password.                  |

### Step 6: Write the `Dockerfile`

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

> **Note:** `supervisord` is the container entrypoint. It starts first and launches both `web` and `celery` as child processes.

### Step 7: Write `app/docker-compose.yml`

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

> **Note:** A single `app` service replaces the separate `web` and `celery` services from earlier labs. The `logs` volume mounts the container log directory onto the host so you can `tail` log files without entering the container.

### Step 8: Write `celery_worker.py`

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

### Step 9: Write `tasks.py`

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

### Step 10: Write `app.py`

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

### Step 11: Verify the file layout

```bash
find ~/lab-22 -type f | sort
```

![Final file layout under ~/lab-22](./images/output-4.png)

---

## Part C — Run, Observe, Recover

### Step 12: Build and start the container

```bash
cd ~/lab-22/app
docker compose up -d --build
```

![docker compose up -d --build output](./images/output-5.png)

### Step 13: Confirm both programs are RUNNING

```bash
docker exec lab22-app supervisorctl status
```

Both programs show `RUNNING`. The PIDs confirm they are child processes of `supervisord` inside the container.

![supervisorctl status — web and celery both RUNNING](./images/output-6.png)

### Step 14: Expose ports via the Load Balancer modal

Find the host IP:

```bash
hostname -I
```

Use the first IP printed as `LB_IP`.

![hostname -I output](./images/output-7.png)

Open the **Load Balancer** modal in the lab UI (top-right) and expose two ports:

| Enter IP   | Enter Port                                |
|------------|-------------------------------------------|
| `LB_IP`    | `5000` (Flask API)                        |
| `LB_IP`    | `15672` (RabbitMQ Management UI)          |

The modal lists each route after you click **Expose**. Click the generated `.lb.poridhi.io` URL for `5000` to open the Flask API in a new tab — keep that URL handy as `<FLASK-LB-URL>`.

### Step 15: Trigger a job

```bash
curl -X POST <FLASK-LB-URL>/jobs \
  -H "Content-Type: application/json" \
  -d '{"duration": 6}'
```

Expected response:

```json
{
  "status": "accepted",
  "job_id": "b3c4d5e6",
  "message": "Job published; worker managed by supervisord"
}
```

![POST /jobs response](./images/output-8.png)

### Step 16: Tail the Celery log

```bash
tail -f ~/lab-22/app/logs/celery.log
```

Press `Ctrl+C` to stop tailing.

![Tail of celery.log](./images/output-9.png)

### Step 17: Tail the Flask log

```bash
tail -f ~/lab-22/app/logs/web.log
```

Press `Ctrl+C` to stop tailing.

![Tail of web.log](./images/output-10.png)

### Step 18: Trigger a long-running job

Submit a job that runs for 30 seconds so you have time to kill the worker:

```bash
curl -X POST <FLASK-LB-URL>/jobs \
  -H "Content-Type: application/json" \
  -d '{"duration": 30}'
```

Note the `job_id` from the response.

![Long-running job response](./images/output-11.png)

### Step 19: Kill the worker and watch it recover

Within a few seconds, kill the celery process inside the container:

```bash
docker exec lab22-app pkill -f "celery worker"
```

Immediately check the supervisor status:

```bash
docker exec lab22-app supervisorctl status
```

The `celery` program briefly shows `STOPPED` or `STARTING`, then returns to `RUNNING` within seconds as `supervisord` relaunches it.

![supervisorctl status after pkill](./images/output-12.png)

Confirm the restart in the log:

```bash
tail -20 ~/lab-22/app/logs/celery.log
```

The log shows the worker shutting down and then starting fresh. Because `acks_late=True` is set, RabbitMQ requeues the unacknowledged task and the restarted worker picks it up automatically.

![celery.log after restart](./images/output-13.png)

### Step 20: Control individual programs

Stop the Celery worker gracefully:

```bash
docker exec lab22-app supervisorctl stop celery
```

```bash
docker exec lab22-app supervisorctl status
```

`celery` shows `STOPPED`. The Flask API keeps running uninterrupted.

![supervisorctl status — celery STOPPED, web RUNNING](./images/output-14.png)

Start the worker again:

```bash
docker exec lab22-app supervisorctl start celery
```

```bash
docker exec lab22-app supervisorctl status
```

Both programs show `RUNNING` again.

![supervisorctl status after start celery](./images/output-15.png)

Restart all programs at once:

```bash
docker exec lab22-app supervisorctl restart all
```

```bash
docker exec lab22-app supervisorctl status
```

![supervisorctl status after restart all](./images/output-16.png)

### Step 21: Read the supervisord event log

```bash
cat ~/lab-22/app/logs/supervisord.log
```

Every start, stop, restart, and crash event is recorded with timestamps.

![supervisord.log event log](./images/output-17.png)

### Step 22: Stop all stacks

```bash
cd ~/lab-22/app && docker compose down
cd ~/lab-22/broker && docker compose down -v
```

![docker compose down output](./images/output-18.png)

---

## Conclusion

You have consolidated the Flask API and the Celery worker into a single container where `supervisord` runs as PID 1 and manages both programs. Killing the worker mid-task proved that `supervisord` brings it back within seconds, and `acks_late=True` made sure the unacknowledged task was requeued. `supervisorctl stop celery` showed that you can suspend the worker without taking down the Flask API, and the per-process log files under `~/lab-22/app/logs/` made every restart and crash event visible without entering the container.

* One image, one process supervisor, two cooperating programs.
* Per-program logs are tailable from the host.
* Restarting or stopping one program does not affect the other.

