# Lab 22: Running the Celery Worker under `supervisord` or `systemd`

**Module 61 — Deployment and Monitoring**

This lab shows two ways to keep a Celery worker running after a crash. The first path runs both the Flask API and the worker inside one container with `supervisord` as PID 1. The second path runs only RabbitMQ in a container and supervises the worker on the host with a `systemd` unit. Pick one path and follow only its steps; both share the same broker.

## Architecture

### Supervisord path (Steps 1–16)

<p align="center"><img src="./images/architecture-supervisord.svg" alt="Lab 22 supervisord architecture"></p>

Both the Flask API and the Celery worker run as `supervisord` programs inside one container. `supervisord` is PID 1, so it owns both processes, restarts them on crash, and writes their logs to bind-mounted files.

The systemd diagram appears under its own path below, before Step 17.

## Concept

| Term           | Description                                                                                          |
|----------------|------------------------------------------------------------------------------------------------------|
| `supervisord`  | A process control system that runs as PID 1 inside a container and restarts child processes on exit. |
| `supervisorctl`| The CLI client used to inspect status and control individual supervised programs.                    |
| `auto-restart` | A directive that tells `supervisord` to relaunch a program whenever it exits unexpectedly.           |
| Per-process log | A dedicated log file that `supervisord` writes for each child program, bind-mounted to the host.     |
| PID 1 reaping | Linux behavior where the init process reaps zombie children. `supervisord` provides this in a container. |

When multiple long-running processes share a container, something must own them, restart them on crash, and reap any zombie children they leave behind. `supervisord` fills that role as the container's init system.

## What You Will Build

A single `app` container where `supervisord` manages two programs: `web` (Flask API on :5000) and `celery` (Celery worker). RabbitMQ runs in a separate broker stack as in Lab 21. You kill the worker, watch `supervisord` bring it back up, and tail per-process logs from the host.

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

## Step 12: Confirm both programs are running

```bash
docker exec lab22-app supervisorctl status
```

Both `web` and `celery` show `RUNNING` as child processes of `supervisord`.

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

## Step 15: Kill the worker and watch it recover

```bash
docker exec lab22-app supervisorctl signal KILL celery
sleep 2
docker exec lab22-app supervisorctl status
```

`supervisord` reports `celery: signalled`, briefly shows `STARTING`, then returns to `RUNNING` with a fresh PID. The unacknowledged task is requeued by RabbitMQ because `acks_late=True` is set, and the restarted worker picks it up.

`supervisorctl signal KILL celery` forwards `SIGKILL` to the worker through the supervisor's own RPC channel, so no extra tools (`pkill`, `pgrep`, `kill`) are needed inside the container.

## Step 16: Stop the stack

```bash
cd ~/lab-22/app && docker compose down
cd ~/lab-22/broker && docker compose down -v
```

### systemd path (Steps 17–30)

<p align="center"><img src="./images/architecture-systemd.svg" alt="Lab 22 systemd architecture"></p>

Only RabbitMQ runs in a container. The Flask API and the Celery worker run as host processes from a project-local `venv`, and the worker is supervised by a `systemd` unit (`lab22-celery.service`) with `Restart=always`. The worker survives container restarts and host reboots without going through Docker at all.

## Step 17: Reuse the broker stack for `systemd`

```bash
cd ~/lab-22/broker
docker compose up -d
docker compose ps
```

`lab22-rabbitmq` shows `healthy`.

## Step 18: Create the systemd-only project directory

Steps 1–16 already populated `~/lab-22/` with the supervisord stack (`docker-compose.yml`, `app/Dockerfile`, `app/src/...`). To avoid clobbering any of that, the systemd path lives in a sibling directory:

```bash
mkdir -p ~/lab-22/systemd/app
cd ~/lab-22/systemd
```

Every command below is relative to `~/lab-22/systemd/`.

## Step 19: Write `requirements.txt`

```bash
cat > requirements.txt << 'EOF'
flask==3.0.3
celery==5.4.0
kombu==5.3.7
EOF
```

## Step 20: Write `app/__init__.py`

```bash
touch app/__init__.py
```

This marks `app/` as a Python package so `from app.celery_app import add` works.

## Step 21: Write `app/celery_app.py`

```bash
cat > app/celery_app.py << 'EOF'
from celery import Celery

app = Celery(
    "lab22-systemd",
    broker="amqp://guest:guest@localhost:5672//",
    backend="rpc://",
)

app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)

@app.task(name="lab22.add")
def add(x: int, y: int) -> int:
    return x + y
EOF
```

The broker points at `localhost` because `docker-compose.yml` (Step 10) publishes `5672:5672` on the host, and this worker runs on the host under systemd.

## Step 22: Write `app/api.py`

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

## Step 23: Write `worker.sh`

```bash
cat > worker.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
exec celery -A app.celery_app worker --loglevel=info --concurrency=2
EOF
chmod +x worker.sh
```

`source .venv/bin/activate` is required because systemd starts the unit with a minimal `PATH` that does **not** include `~/lab-22/systemd/.venv/bin/`. Activating the venv inside the script puts `celery` on PATH regardless of what environment systemd passes. The venv must live next to `worker.sh` (i.e. inside `~/lab-22/systemd/`), not in `$HOME`.

## Step 24: Install the worker dependencies

The venv lives inside the project directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Confirm the worker can boot in the foreground. Press `Ctrl+C` after you see `celery@hostname ready`.

```bash
./worker.sh
```

## Step 25: Write the systemd unit file

```bash
sudo tee /etc/systemd/system/lab22-celery.service >/dev/null <<'EOF'
[Unit]
Description=Lab 22 Celery Worker
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
WorkingDirectory=/home/poridhian/lab-22/systemd
ExecStart=/home/poridhian/lab-22/systemd/worker.sh
Restart=always
RestartSec=3
User=root
StandardOutput=append:/var/log/lab22-celery.out.log
StandardError=append:/var/log/lab22-celery.err.log

[Install]
WantedBy=multi-user.target
EOF
```

The Poridhi lab runs as user `poridhian` (home `/home/poridhian`), so the unit uses `/home/poridhian/lab-22/systemd`. On a different host, replace both paths with `$HOME/lab-22/systemd` — check with `pwd`.

## Step 26: Enable and start the systemd unit


```bash
sudo systemctl daemon-reload
sudo systemctl enable lab22-celery.service
sudo systemctl start lab22-celery.service
sudo systemctl status lab22-celery.service --no-pager
```

The output ends with `active (running)`.

## Step 27: Expose port 5000 in the lab UI

The LB expose flow is identical to **Step 13** — follow it to map `LB_IP:5000` and capture `<FLASK-LB-URL>`.

The only extra step for the systemd path: `worker.sh` runs **only** the Celery worker (not Flask), so start the Flask API in another terminal so the LB URL has something to forward to:

```bash
cd ~/lab-22/systemd
source .venv/bin/activate
python -m app.api
```

(For the supervisord path this is unnecessary — `supervisord.conf` already keeps Flask running under the `[program:flask]` entry.)

## Step 28: Publish a task and confirm the worker handles it

Open a **second terminal** (the Flask API must keep running in Terminal A from Step 27) and run:

```bash
hostname -I
# first IP printed is LB_IP — enter it as <LB_IP> in Step 27's table
```

If you haven't already, follow Step 27 to expose `LB_IP:5000` in the lab UI and capture `<FLASK-LB-URL>`.

Publish a task and capture the `task_id`:

```bash
curl -s -X POST <FLASK-LB-URL>/tasks \
  -H "Content-Type: application/json" \
  -d '{"x": 2, "y": 40}'
```

Response:

```json
{"task_id": "8f3a1b9c-..."}
```

Fetch the result. **`<task_id>` is a placeholder — replace it with the real UUID** from the previous response (or bash will try to parse `<` `>` as redirection and fail with `syntax error near unexpected token 'newline'`). If the result returns `PENDING`, wait a second and retry — the worker is still computing:

```bash
curl -s <FLASK-LB-URL>/result/<task_id>
```

The robust way is to capture the id once and reuse it:

```bash
TID=$(curl -s -X POST <FLASK-LB-URL>/tasks \
  -H "Content-Type: application/json" \
  -d '{"x": 2, "y": 40}' \
  | python3 -c 'import sys,json; print(json.load(sys.stdin)["task_id"])')

curl -s <FLASK-LB-URL>/result/$TID
```

Expected:

```json
{"state": "SUCCESS", "value": 42}
```

You can also confirm the worker actually executed the task by tailing its log:

```bash
tail -n 20 /var/log/lab22-celery.out.log
```

## Step 29: Trigger a crash and watch systemd restart

Find the worker PID and kill it. In the **terminal running Flask (Terminal A)**, publish one more task first so the worker log has fresh activity to compare against after the restart:

```bash
curl -s -X POST <FLASK-LB-URL>/tasks \
  -H "Content-Type: application/json" \
  -d '{"x": 7, "y": 35}'
```

Then in **Terminal B**:

```bash
systemctl show -p MainPID lab22-celery.service
sudo kill -9 <pid>
sleep 4
sudo systemctl status lab22-celery.service --no-pager | head -10
```

The `Main PID` line must show a **new** PID and the status must end with `active (running)`. If the status shows `failed`, double-check the unit has `Restart=always` (Step 25) and run `sudo systemctl daemon-reload` before retrying.

Inspect the journal to see exactly what systemd recorded for the crash + restart:

```bash
sudo journalctl -u lab22-celery.service -n 40 --no-pager
```

You should see a `Celery worker` startup line from the new PID, no `exited`/`failed` state.

The worker log also keeps an append-only record (configured in the unit's `StandardOutput=append:...` directive):

```bash
tail -n 30 /var/log/lab22-celery.out.log
```

## Step 30: Stop the worker

Stop the systemd unit, the broker stack, and the Flask terminal:

```bash
# Terminal B
sudo systemctl stop lab22-celery.service
sudo systemctl status lab22-celery.service --no-pager
# expect: inactive (dead)

cd ~/lab-22/broker
docker compose down -v
```

Then in **Terminal A** (the one running `python -m app.api`), press **Ctrl+C** to stop Flask. Confirm no `python` process is left behind:

```bash
pgrep -fa "python -m app.api"
# expect: no output
```

If you want to fully undo Step 26's `enable`, run:

```bash
sudo systemctl disable lab22-celery.service
sudo rm /etc/systemd/system/lab22-celery.service
sudo systemctl daemon-reload
```

