# Lab 22: Use `supervisord` or `systemd` to Manage Celery Worker as a Background Service

**Module 61 — Deployment and Monitoring**

This lab takes the Celery worker out of the foreground `docker compose up` and turns it into a real background service. You will run the worker two ways — once under `supervisord` and once under `systemd`. Both forms auto-restart the worker on crash and bring it back up on reboot. The Flask API keeps publishing tasks; the queue keeps draining, even if no one is logged in.

## Architecture

<p align="center"><img src="./images/architecture.svg" alt="Lab 22 Architecture"></p>

## What You Will Build

A Flask API that publishes tasks to RabbitMQ. A Celery worker that is supervised — first by `supervisord`, then by `systemd` — so it restarts automatically and starts on boot. You will trigger a deliberate crash, watch the supervisor bring the worker back up, and inspect logs to confirm.

## Step 1: Create the project directory

```bash
mkdir -p ~/lab-22 && cd ~/lab-22
mkdir -p app
```

## Step 2: Create the requirements file

```bash
cat > requirements.txt << 'EOF'
flask==3.0.3
celery==5.4.0
kombu==5.3.7
redis==5.0.4
EOF
```

## Step 3: Create the Celery app

Run this command to create `app/celery_app.py`:

```bash
cat > app/celery_app.py << 'EOF'
from celery import Celery

app = Celery(
    "lab22",
    broker="redis://localhost:6379/0",
    backend="redis://localhost:6379/1",
)

@app.task(name="lab22.add")
def add(x: int, y: int) -> int:
    return x + y
EOF
```

## Step 4: Create the Flask publisher

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

## Step 5: Start RabbitMQ and Redis

```bash
docker run -d --name rabbitmq -p 5672:5672 -p 15672:15672 rabbitmq:3-management
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

Confirm both containers are up:

```bash
docker ps --format "table {{.Names}}\t{{.Status}}"
```

## Step 6: Create the worker entrypoint

```bash
cat > worker.sh << 'EOF'
#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
exec celery -A app.celery_app worker --loglevel=info --concurrency=2
EOF
chmod +x worker.sh
```

## Step 7: Install the worker dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Confirm the worker can boot in the foreground. Press `Ctrl+C` after you see `celery@hostname ready`.

```bash
./worker.sh
```

![](./images/Worker%20ready.png)

## Step 8: Install supervisord

`supervisord` is available from the standard apt repo.

```bash
sudo apt install -y supervisor
sudo systemctl enable supervisor
sudo systemctl start supervisor
```

## Step 9: Add the Celery worker program

```bash
cat | sudo tee /etc/supervisor/conf.d/lab22-celery.conf <<'EOF'
[program:lab22-celery]
command=/root/lab-22/worker.sh
directory=/root/lab-22
user=root
autostart=true
autorestart=true
startretries=5
stopasgroup=true
killasgroup=true
stdout_logfile=/var/log/supervisor/lab22-celery.out.log
stderr_logfile=/var/log/supervisor/lab22-celery.err.log
stdout_logfile_maxbytes=10MB
stderr_logfile_maxbytes=10MB
environment=PATH="/root/lab-22/.venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
EOF
```

Replace `/root/lab-22` with your actual project path (`$HOME/lab-22` for non-root users; check with `pwd`).

## Step 10: Reload and start the worker

```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl status lab22-celery
```

Expected output:

```
lab22-celery                    RUNNING   pid 12345, uptime 0:00:02
```

![](./images/Supervisor%20status%20running.png)

## Step 11: Publish a task and confirm the worker handles it

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

Expected output:

```json
{"task_id": "abcd-1234-..."}
```

Wait a moment, then fetch the result:

```bash
curl -s http://localhost:5000/result/<task_id>
```

Expected output:

```json
{"state": "SUCCESS", "value": 42}
```

## Step 12: Trigger a crash and watch the supervisor restart

Find the worker process and kill it:

```bash
sudo supervisorctl status lab22-celery
sudo kill -9 <pid>
```

Within a few seconds, `supervisorctl` shows the new PID:

```bash
sleep 2 && sudo supervisorctl status lab22-celery
```

Expected output:

```
lab22-celery                    RUNNING   pid 12399, uptime 0:00:01
```

Confirm the restart by tailing the log:

```bash
sudo tail -n 5 /var/log/supervisor/lab22-celery.err.log
```

![](./images/Worker%20restarted.png)

## Step 13: Stop supervisord and switch to systemd

```bash
sudo supervisorctl stop lab22-celery
```

## Step 14: Create the systemd unit file

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

Replace `/root/lab-22` with your actual project path, the same way you did in Step 9.

## Step 15: Enable and start the systemd unit

```bash
sudo systemctl daemon-reload
sudo systemctl enable lab22-celery.service
sudo systemctl start lab22-celery.service
sudo systemctl status lab22-celery.service --no-pager
```

Confirm the output ends with `active (running)`.

![](./images/Systemd%20status%20running.png)

## Step 16: Trigger a crash and watch systemd restart

Find the worker PID and kill it:

```bash
systemctl show -p MainPID lab22-celery.service
sudo kill -9 <pid>
sleep 4
sudo systemctl status lab22-celery.service --no-pager | head -10
```

Expected output includes a new PID and `active (running)`.

![](./images/Systemd%20restarted.png)

## Step 17: Open the Flask API in the lab UI

Open the **Load Balancer** modal in the lab UI (top-right). Run this once to find the IP to enter:

```bash
hostname -I
```

Sample output:

```
10.61.7.107 172.17.0.1 100.80.176.159 172.18.0.1
```

Use the first IP printed as `LB_IP`. Open the Load Balancer modal.

![](./images/Hostname.png)

Expose one port:

| Enter IP | Enter Port |
|----------|------------|
| `LB_IP` | `5000` (Flask API) |

The modal lists each route after you click **Expose**. Click the generated `.lb.poridhi.io` URL to open the service in a new tab.

![](./images/Expose%20port.png)

## Conclusion

You have run the same Celery worker under two different supervisors. `supervisord` is the right pick when the host already has Python and a project virtualenv in place — its config files are simple and the `supervisorctl` CLI is fast. `systemd` is the right pick when the host is a fresh VM with no extra packages — it ships with the OS, integrates with the boot sequence, and is the standard way to declare long-running services. Both forms give you auto-restart on crash and start-on-boot. Pick whichever matches the host you are deploying onto.
