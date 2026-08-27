# Lab 22.2: Running Celery under `systemd` (host-level supervisor)

**Module 61 — Deployment and Monitoring**

This lab keeps the Celery worker alive on the host by using a `systemd` unit with `Restart=always`. The Flask API also runs on the host from a project-local `venv`, and only RabbitMQ stays in a container. You kill the worker, watch `systemd` bring it back up, and inspect the journal for the crash + restart record.

> Looking for the **in-container (`supervisord`)** path instead? See [Lab 22.1](../lab-22.1/README.md).

## Architecture

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.2/images/architecture-systemd.svg" alt="Lab 22.2 systemd architecture"></p>

Only RabbitMQ runs in a container. The Flask API and the Celery worker run as host processes from a project-local `venv`, and the worker is supervised by a `systemd` unit (`lab22-celery.service`) with `Restart=always`. The worker survives container restarts and host reboots without going through Docker at all.

## Concept

| Term              | Description                                                                                  |
|-------------------|----------------------------------------------------------------------------------------------|
| `systemd`         | The host init system that starts, supervises, and restarts services declared as unit files.  |
| Unit file         | A declarative file under `/etc/systemd/system/` that describes how a service runs.            |
| `Restart=always`  | A unit directive that tells `systemd` to relaunch the service whenever it exits, regardless of exit code. |
| `WorkingDirectory`| The unit directive that pins the working directory before `ExecStart` runs — important for project-relative paths. |
| `journalctl`      | The CLI tool that queries `systemd`'s structured log for a specific unit.                    |

When the worker runs on the host next to the broker, you need something that owns its lifecycle independent of any container. `systemd` is the standard tool for that on Linux hosts: it starts the unit on boot, restarts it on crash, and records every state change in its journal.

## What You Will Build

A `lab22-celery.service` unit that supervises the host-side Celery worker. RabbitMQ runs in a container on a dedicated `lab22-broker-net` Docker network. The Flask API runs on the host from a separate terminal so the LB URL has something to forward to. You publish a task, kill the worker, watch `systemd` restart it, and confirm the task completes.

## Step 1: Create the broker project directory

This lab keeps RabbitMQ in a container, separate from the host-side worker. The broker files live under `~/lab-22/broker/`:

```bash
mkdir -p ~/lab-22/broker
```

## Step 2: Confirm Docker and Compose

The lab uses `docker compose` (the v2 CLI plugin, not the legacy `docker-compose` binary):

```bash
docker --version
docker compose version
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.2/images/docker%20--version.png" alt="Docker and Compose versions"></p>

## Step 3: Write the broker `docker-compose.yml`

RabbitMQ runs on a dedicated bridge network so its hostname is stable for the host-side worker (`amqp://guest:guest@localhost:5672//` works because `5672` is published to the host):

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

## Step 4: Start the broker stack

```bash
cd ~/lab-22/broker
docker compose up -d
docker compose ps
```

`lab22-rabbitmq` shows `healthy` once it accepts AMQP.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.2/images/Step%203%20docker%20compose%20up.png" alt="Broker stack running for the systemd path"></p>

## Step 5: Create the systemd-only project directory

This lab lives in a sibling directory under `~/lab-22/systemd/` so the project files don't collide with anything else in `~/lab-22/`:

```bash
mkdir -p ~/lab-22/systemd/app
cd ~/lab-22/systemd
```

Every command below is relative to `~/lab-22/systemd/`.

## Step 6: Write `requirements.txt`

```bash
cat > requirements.txt << 'EOF'
flask==3.0.3
celery==5.4.0
kombu==5.3.7
EOF
```

## Step 7: Write `app/__init__.py`

```bash
touch app/__init__.py
```

This marks `app/` as a Python package so `from app.celery_app import add` works.

## Step 8: Write `app/celery_app.py`

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

The broker points at `localhost` because the broker stack published `5672:5672` to the host (Step 4), and this worker runs on the host under `systemd`.

## Step 9: Write `app/api.py`

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

## Step 10: Write `worker.sh`

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

`source .venv/bin/activate` is required because `systemd` starts the unit with a minimal `PATH` that does **not** include `~/lab-22/systemd/.venv/bin/`. Activating the venv inside the script puts `celery` on `PATH` regardless of what environment `systemd` passes. The venv must live next to `worker.sh` (i.e. inside `~/lab-22/systemd/`), not in `$HOME`.

## Step 11: Install the worker dependencies

The venv lives inside the project directory:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Confirm the worker can boot in the foreground. Press `Ctrl+C` after you see `celery@hostname ready`:

```bash
./worker.sh
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.2/images/Step%2024%20Install%20the%20worker%20dependencies.png" alt="Worker booting in the foreground and reporting ready"></p>

## Step 12: Write the systemd unit file

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

## Step 13: Enable and start the systemd unit

```bash
sudo systemctl daemon-reload
sudo systemctl enable lab22-celery.service
sudo systemctl start lab22-celery.service
sudo systemctl status lab22-celery.service --no-pager
```

The output ends with `active (running)`.

## Step 14: Expose port 5000 in the lab UI

`worker.sh` runs **only** the Celery worker (not Flask), so start the Flask API in another terminal so the LB URL has something to forward to:

```bash
cd ~/lab-22/systemd
source .venv/bin/activate
python -m app.api
```

Open the **Load Balancer** modal in the lab UI (top-right). Run this once to find the IP to enter:

```bash
hostname -I
```

Sample output:

```
10.61.7.107 172.17.0.1 100.80.176.159 172.18.0.1
```

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.2/images/hostname%20-I.png" alt="hostname -I output"></p>

Use the first IP printed as `LB_IP`.

| Enter IP  | Enter Port             |
|-----------|------------------------|
| `LB_IP`   | `5000` (Flask API)     |

Click **Expose**. Copy the generated `.lb.poridhi.io` URL — the rest of the lab uses it as `<FLASK-LB-URL>`.

<p align="center"><img src="https://raw.githubusercontent.com/mahiiabdullah/Poridhi-Labs/main/module-61/lab-22.2/images/Step%2027%20Expose%20port%205000%20in%20the%20lab%20UI.png" alt="Expose port 5000 in the lab UI for the systemd path"></p>

## Step 15: Publish a task and confirm the worker handles it

Open a **second terminal** (the Flask API must keep running from Step 14) and run:

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

## Step 16: Trigger a crash and watch systemd restart

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

The `Main PID` line must show a **new** PID and the status must end with `active (running)`. If the status shows `failed`, double-check the unit has `Restart=always` (Step 12) and run `sudo systemctl daemon-reload` before retrying.

Inspect the journal to see exactly what `systemd` recorded for the crash + restart:

```bash
sudo journalctl -u lab22-celery.service -n 40 --no-pager
```

You should see a `Celery worker` startup line from the new PID, no `exited`/`failed` state.

The worker log also keeps an append-only record (configured in the unit's `StandardOutput=append:...` directive):

```bash
tail -n 30 /var/log/lab22-celery.out.log
```

## Step 17: Stop the worker

Stop the `systemd` unit, the broker stack, and the Flask terminal:

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

If you want to fully undo Step 10's `enable`, run:

```bash
sudo systemctl disable lab22-celery.service
sudo rm /etc/systemd/system/lab22-celery.service
sudo systemctl daemon-reload
```

## Conclusion

You ran a host-side Celery worker under `systemd`, watched `systemd` restart it after a `SIGKILL`, and inspected both the journal and the append-only worker log for the crash + restart record. Pick this path when the worker runs on the host alongside the broker and needs to start on boot; for in-container supervision use [Lab 22.1](../lab-22.1/README.md).
