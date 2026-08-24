# Lab 22: Running the Celery Worker under `supervisord` or `systemd`

**Module 61 — Deployment and Monitoring**

This lab takes the Celery worker out of the foreground `docker compose up` and runs it under a real process supervisor. You do the lab twice — once with `supervisord` inside a single app container, and once with `systemd` on the host. Both forms auto-restart the worker on crash and bring it back up on reboot. The Flask API keeps publishing tasks; the queue keeps draining, even if no one is logged in.

## Architecture

<p align="center"><img src="./images/architecture.svg" alt="Lab 22 Architecture"></p>

## What You Will Build

A Flask API that publishes tasks to RabbitMQ. A Celery worker that is supervised — first by `supervisord` inside a container, then by `systemd` on the host — so it restarts automatically and starts on boot. You trigger a deliberate crash, watch the supervisor bring the worker back up, and inspect logs to confirm.

---

## Part A — supervisord inside a single container

The Flask API and the Celery worker share one container. `supervisord` runs as PID 1, starts both processes, and restarts either one if it exits. Logs from each process land in a separate file inside the container.

### Step 1: Create the project directories

```bash
mkdir -p ~/lab-22/app/src ~/lab-22/app/logs
mkdir -p ~/lab-22/broker
```

### Step 2: Confirm Docker and Compose

```bash
docker --version
docker compose version
```

### Step 3: Start the broker stack

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

### Step 4: Write the application requirements

```bash
cat > ~/lab-22/app/requirements.txt << 'EOF'
flask==3.0.3
celery==5.4.0
kombu==5.3.7
supervisor==4.2.5
EOF
```

### Step 5: Write `supervisord.conf`

Two programs are supervised: `web` (Flask) and `celery` (Celery worker). `nodaemon=true` keeps `supervisord` in the foreground so Docker sees it as PID 1.

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

### Step 6: Write the Dockerfile

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

### Step 7: Write the Celery worker

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

### Step 8: Write the task module

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

### Step 9: Write the Flask API

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

### Step 10: Write `docker-compose.yml` for the app

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

### Step 11: Build and start the app

```bash
cd ~/lab-22/app
docker compose up -d --build
```

### Step 12: Confirm both programs are running

```bash
docker exec lab22-app supervisorctl status
```

Both `web` and `celery` show `RUNNING` as child processes of `supervisord`.

### Step 13: Trigger a job

Find the host IP and expose port 5000 in the Load Balancer modal:

```bash
hostname -I
```

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

### Step 14: Kill the worker and watch it recover

```bash
docker exec lab22-app pkill -f "celery worker"
sleep 2
docker exec lab22-app supervisorctl status
```

The `celery` program briefly shows `STARTING`, then returns to `RUNNING` within seconds. The unacknowledged task is requeued by RabbitMQ because `acks_late=True` is set, and the restarted worker picks it up.

### Step 15: Stop the stack

```bash
cd ~/lab-22/app && docker compose down
cd ~/lab-22/broker && docker compose down -v
```

---

## Part B — systemd on the host

The Flask API runs once with `python -m app.api` in a venv. The Celery worker is registered as a `systemd` service. `systemd` brings the worker back automatically and starts it on boot. RabbitMQ stays in the broker stack from Part A.

### Step 16: Reuse the broker stack

```bash
cd ~/lab-22/broker
docker compose up -d
docker compose ps
```

`lab22-rabbitmq` shows `healthy`.

### Step 17: Create the project layout

```bash
mkdir -p ~/lab-22 && cd ~/lab-22
mkdir -p app
cat > requirements.txt << 'EOF'
flask==3.0.3
celery==5.4.0
kombu==5.3.7
EOF
```

### Step 18: Write `app/celery_app.py`

```bash
cat > app/celery_app.py << 'EOF'
from celery import Celery

app = Celery(
    "lab22-systemd",
    broker="amqp://guest:guest@localhost:5672//",
    backend="rpc://",
)

@app.task(name="lab22.add")
def add(x: int, y: int) -> int:
    return x + y
EOF
```

### Step 19: Write `app/api.py`

```bash
cat > app/api.py << 'EOF'
from flask import Flask, jsonify, request
from app.celery_app import add

api = Flask(__name__)

@api.post("/tasks")
def publish():
    data = request.get_json(force=True)
    res = add.delay(int(data["x"]), int(data["y"]))
    return jsonify({"task_id": res.id}), 202

@api.get("/result/<task_id>")
def result(task_id: str):
    from celery.result import AsyncResult
    r = AsyncResult(task_id)
    return jsonify({"state": r.state, "value": r.result})

if __name__ == "__main__":
    api.run(host="0.0.0.0", port=5000)
EOF
```

### Step 20: Write `worker.sh`

```bash
cat > worker.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec celery -A app.celery_app worker --loglevel=info --concurrency=2
EOF
chmod +x worker.sh
```

### Step 21: Install the worker dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Confirm the worker can boot in the foreground. Press `Ctrl+C` after you see `celery@hostname ready`.

```bash
./worker.sh
```

### Step 22: Write the systemd unit file

```bash
cat | sudo tee /etc/systemd/system/lab22-celery.service <<'EOF'
[Unit]
Description=Lab 22 Celery Worker
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
WorkingDirectory=/root/lab-22
ExecStart=/root/lab-22/worker.sh
Restart=always
RestartSec=3
User=root
StandardOutput=append:/var/log/lab22-celery.out.log
StandardError=append:/var/log/lab22-celery.err.log

[Install]
WantedBy=multi-user.target
EOF
```

Replace `/root/lab-22` with your actual project path (`$HOME/lab-22` for non-root users; check with `pwd`).

### Step 23: Enable and start the systemd unit

```bash
sudo systemctl daemon-reload
sudo systemctl enable lab22-celery.service
sudo systemctl start lab22-celery.service
sudo systemctl status lab22-celery.service --no-pager
```

The output ends with `active (running)`.

### Step 24: Publish a task and confirm the worker handles it

Start the Flask API in another terminal:

```bash
source .venv/bin/activate
python -m app.api
```

Publish a task:

```bash
curl -s -X POST http://localhost:5000/tasks \
  -H "Content-Type: application/json" \
  -d '{"x": 2, "y": 40}'
```

Wait a moment, then fetch the result:

```bash
curl -s http://localhost:5000/result/<task_id>
```

Expected output:

```json
{"state": "SUCCESS", "value": 42}
```

### Step 25: Trigger a crash and watch systemd restart

Find the worker PID and kill it:

```bash
systemctl show -p MainPID lab22-celery.service
sudo kill -9 <pid>
sleep 4
sudo systemctl status lab22-celery.service --no-pager | head -10
```

The output includes a new PID and `active (running)`.

### Step 26: Expose the Flask API in the lab UI

```bash
hostname -I
```

Use the first IP printed as `LB_IP`. Open the **Load Balancer** modal in the lab UI (top-right) and expose one port:

| Enter IP | Enter Port |
|----------|------------|
| `LB_IP` | `5000` (Flask API) |

Click the generated `.lb.poridhi.io` URL to open the service in a new tab.

### Step 27: Stop the worker

```bash
sudo systemctl stop lab22-celery.service
cd ~/lab-22/broker && docker compose down -v
```

## Conclusion

You have run the same Celery worker under two different supervisors. `supervisord` is the right pick when the host already has Python and a project virtualenv in place — its config files are simple and the `supervisorctl` CLI is fast. `systemd` is the right pick when the host is a fresh VM with no extra packages — it ships with the OS, integrates with the boot sequence, and is the standard way to declare long-running services. Both forms give you auto-restart on crash and start-on-boot. Pick whichever matches the host you are deploying onto.