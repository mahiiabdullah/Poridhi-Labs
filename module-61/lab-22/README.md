# Manage Flask + Celery Worker with `supervisord`

You will consolidate the Flask API and the Celery worker into a single container. `supervisord` runs as PID 1 and supervises both processes. If either process crashes, `supervisord` restarts it automatically. `supervisorctl` lets you stop, start, and restart individual programs from outside the container without entering a shell.

![Architecture](./images/architecture.svg)

## Concept

| Term | Definition |
| --- | --- |
| **`supervisord`** | A process control system that starts, monitors, and restarts child programs. Runs as PID 1 inside a container. |
| **`supervisorctl`** | The CLI client for `supervisord`. Used to inspect status and control individual programs. |
| **`auto-restart`** | A program directive that tells `supervisord` to relaunch a program whenever it exits with a non-zero status. |
| **Per-process log files** | Dedicated log files that `supervisord` writes for each child program, bind-mounted to the host for tailing. |
| **PID 1 reaping** | Linux behavior where the init process reaps zombie children. `supervisord` is used as PID 1 so reaping works correctly in containers. |

When multiple long-running processes share a container, something must own them, restart them on crash, and reap any zombie children they leave behind. `supervisord` fills that role by acting as the container's init system.

## Objectives

- Build a two-stack Docker Compose setup: a broker stack (RabbitMQ) and an app stack (Flask + Celery worker).
- Author a `Dockerfile` that installs `supervisord` and configures two programs: `web` and `celery`.
- Verify that `supervisord` restarts a crashed worker without operator intervention.
- Inspect per-process log files from the host filesystem.

## What You Will Build

```text
lab-22/
├── app/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── supervisord.conf
│   ├── app.py
│   └── tasks.py
├── broker/
│   └── docker-compose.yml
├── app/
│   └── docker-compose.yml
└── README.md
```

You will run RabbitMQ in a separate broker stack and connect the application stack to it via a named Docker network. `supervisord` will manage both the Flask web server and the Celery worker inside a single container.

## Step 1: Create the project directories

Run the following commands:

```bash
mkdir -p ~/lab-22/app ~/lab-22/broker
cd ~/lab-22
```

**Explanation:**

- `mkdir -p ~/lab-22/app ~/lab-22/broker`: Creates the two-stack layout (application stack and broker stack) without failing if the directories already exist.
- `cd ~/lab-22`: Moves into the lab root for the remaining steps.

## Step 2: Author the broker stack

Create `broker/docker-compose.yml` with the following contents:

```yaml
services:

  rabbitmq:
    image: rabbitmq:3-management
    container_name: lab22-rabbitmq
    hostname: rabbitmq
    restart: unless-stopped
    environment:
      RABBITMQ_DEFAULT_USER: poridhi
      RABBITMQ_DEFAULT_PASS: poridhi
    ports:
      - "5672:5672"
      - "15672:15672"
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "-q", "ping"]
      interval: 10s
      timeout: 5s
      retries: 10
    networks:
      - lab22-broker-net

volumes:
  rabbitmq_data:

networks:
  lab22-broker-net:
    name: lab22-broker-net
```

Run the following command to validate and start the broker stack:

```bash
cd ~/lab-22/broker
docker compose config
docker compose up -d
```

**Explanation:**

- `rabbitmq:3-management`: Pulls RabbitMQ with the management plugin enabled so the broker UI is reachable on `15672`.
- `healthcheck`: Lets the application stack wait until RabbitMQ actually accepts AMQP traffic before starting its workers.
- `networks.lab22-broker-net`: Declares a named network the application stack will join so RabbitMQ is reachable by service name.

## Step 3: Author the application stack

Create `app/Dockerfile` with the following contents:

```dockerfile
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY supervisord.conf /etc/supervisor/supervisord.conf
COPY app.py tasks.py ./

EXPOSE 5000

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/supervisord.conf"]
```

Create `app/requirements.txt`:

```text
flask==3.0.3
celery==5.4.0
redis==5.0.7
flower==2.0.1
gunicorn==22.0.0
```

Create `app/supervisord.conf`:

```ini
[supervisord]
nodaemon=true
logfile=/var/log/supervisor/supervisord.log
pidfile=/var/run/supervisord.pid

[program:web]
command=gunicorn --bind 0.0.0.0:5000 app:app
autostart=true
autorestart=true
stdout_logfile=/var/log/web.log
stderr_logfile=/var/log/web.err

[program:celery]
command=celery -A tasks worker --loglevel=info
autostart=true
autorestart=true
stdout_logfile=/var/log/celery.log
stderr_logfile=/var/log/celery.err
```

Create `app/app.py`:

```python
from flask import Flask, jsonify
from tasks import add

app = Flask(__name__)

@app.get("/health")
def health():
    return jsonify(status="ok")

@app.post("/add")
def add_route():
    r = add.delay(10, 20)
    return jsonify(task_id=r.id)
```

Create `app/tasks.py`:

```python
import time
from celery import Celery

app = Celery(
    "tasks",
    broker="amqp://poridhi:poridhi@rabbitmq:5672//",
    backend="rpc://",
)

@app.task
def add(x, y):
    time.sleep(2)
    return x + y
```

Create `app/docker-compose.yml`:

```yaml
services:

  app:
    build: .
    container_name: lab22-app
    restart: unless-stopped
    ports:
      - "5000:5000"
    volumes:
      - ./app/logs:/var/log
    networks:
      - lab22-broker-net
    depends_on:
      rabbitmq:
        condition: service_healthy

networks:
  lab22-broker-net:
    external: true
    name: lab22-broker-net
```

**Explanation:**

- `supervisord` runs as PID 1 inside the `app` container.
- `[program:web]` and `[program:celery]` define two child programs, each with `autorestart=true` so `supervisord` relaunches them on crash.
- `depends_on.condition: service_healthy` waits for the broker healthcheck before starting the worker.

## Step 4: Build and start the application stack

Run the following commands:

```bash
cd ~/lab-22/app
mkdir -p logs
docker compose up -d --build
docker compose ps
```

**Explanation:**

- `docker compose up -d --build`: Builds the `app` image from the local `Dockerfile` and starts the container in detached mode.
- `docker compose ps`: Lists the application containers and their current status.

## Step 5: Verify supervisord is managing both programs

Run the following command:

```bash
docker exec lab22-app supervisorctl status
```

**Expected output:**

```text
celery                           RUNNING   pid 12, uptime 0:00:05
web                              RUNNING   pid 11, uptime 0:00:05
```

Run the following command to inspect the worker process tree:

```bash
docker exec lab22-app ps -ef
```

**Explanation:**

- `supervisorctl status` reports each program's state. `RUNNING` means `supervisord` launched the program and is monitoring it.
- `ps -ef` shows the full process tree, with `supervisord` at PID 1 and `gunicorn`/`celery` as children.

## Step 6: Trigger the auto-restart behavior

Run the following command to kill the Celery worker process:

```bash
docker exec lab22-app supervisorctl stop celery
docker exec lab22-app supervisorctl status
```

**Expected output:**

```text
celery                           STOPPED   pid 12, uptime 0:00:30
web                              RUNNING   pid 11, uptime 0:00:30
```

Run the following command to restart it manually:

```bash
docker exec lab22-app supervisorctl start celery
docker exec lab22-app supervisorctl status
```

For the auto-restart case, kill the worker process directly and confirm `supervisord` respawns it:

```bash
WPID=$(docker exec lab22-app pgrep -f "celery -A tasks")
docker exec lab22-app kill -9 $WPID
sleep 3
docker exec lab22-app supervisorctl status
```

**Explanation:**

- `supervisorctl stop` halts a program and leaves it stopped; `supervisorctl start` resumes it.
- Killing the worker with `kill -9` simulates a hard crash. Because `autorestart=true`, `supervisord` respawns it within seconds.

## Step 7: Tail per-process log files

Run the following commands:

```bash
ls ~/lab-22/app/logs
tail -n 20 ~/lab-22/app/logs/web.log
tail -n 20 ~/lab-22/app/logs/celery.log
tail -n 20 ~/lab-22/app/logs/web.err
```

**Explanation:**

- `supervisord` writes stdout and stderr to the configured paths. The `./app/logs` bind mount makes those files visible on the host.
- `tail -n 20` shows the most recent lines so you can confirm both programs are logging as expected.

## Verification

Run the following command to confirm the Flask API is healthy:

```bash
curl -s http://localhost:5000/health
```

**Expected output:**

```text
{"status":"ok"}
```

Run the following command to submit a background task:

```bash
curl -s -X POST http://localhost:5000/add
```

**Expected output:**

```text
{"task_id":"<UUID>"}
```

Run the following command to confirm the worker executed it:

```bash
docker exec lab22-app supervisorctl status celery
docker exec lab22-app tail -n 5 /var/log/celery.log
```

**Expected output in the Celery log:**

```text
[INFO/MainProcess] Task tasks.add[<id>] succeeded in 2.01s: 30
```

| # | Call | Status | Body snippet |
| --- | --- | --- | --- |
| 1 | `curl -s http://localhost:5000/health` | SUCCESS | `{"status":"ok"}` |
| 2 | `curl -s -X POST http://localhost:5000/add` | SUCCESS | `{"task_id":"..."}` |
| 3 | `docker exec lab22-app supervisorctl status` | SUCCESS | `celery RUNNING / web RUNNING` |

## Conclusion

You built a single-container application stack where `supervisord` owns two long-running processes: a Flask API and a Celery worker. The broker was kept in its own Compose stack with a named network so the app could attach without restarting. `auto-restart` recovered the worker from a hard crash, and per-process log files were exposed on the host for tailing. `supervisord` is the simplest PID-1 replacement that gives you restart, supervision, and log routing in plain configuration.