# Lab 19: RabbitMQ Durability, Management UI, and Acknowledgment Policies

**Module 60 — Flask, Celery, and RabbitMQ**

This lab upgrades the in-process threading model from Lab 18 to a distributed task queue. A Flask API publishes messages to RabbitMQ. A separate Celery worker consumes them. The lab covers durable queues, late acks, and a web UI for inspecting the broker.

## Architecture

<p align="center"><img src="./images/architecture.svg" alt="Lab 19 Architecture"></p>

## What You Will Build

A Flask API that publishes a Celery task to RabbitMQ. A Celery worker picks the task off the queue and runs it. The RabbitMQ Management UI runs on port 15672 so you can watch queues and messages in the browser. Queues survive a broker restart because they are declared as durable. Tasks that the worker has already started are not lost if the worker crashes mid-execution because acks are sent late.

## Step 1: Create the project directory

```bash
mkdir ~/lab-19 && cd ~/lab-19
mkdir -p app
```

## Step 2: Confirm Docker and Docker Compose are installed

```bash
docker --version
docker compose version
```

## Step 3: Create the requirements file

Run this command to create `requirements.txt`:

```bash
cat > requirements.txt << 'EOF'
flask==3.0.3
celery==5.4.0
kombu==5.3.7
EOF
```

## Step 4: Create the Dockerfile

Both services share the same image so the Python code and dependencies stay in sync.

```bash
cat > Dockerfile << 'EOF'
FROM python:3.12-slim

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
EOF
```

## Step 5: Create the docker-compose file

Three services run side by side: a RabbitMQ broker with the management plugin, a Flask web service, and a Celery worker service.

```bash
cat > docker-compose.yml << 'EOF'
services:
  rabbitmq:
    image: rabbitmq:3-management
    container_name: lab19-rabbitmq
    ports:
      - "5672:5672"
      - "15672:15672"
    environment:
      RABBITMQ_DEFAULT_USER: guest
      RABBITMQ_DEFAULT_PASS: guest
    healthcheck:
      test: ["CMD", "rabbitmq-diagnostics", "ping"]
      interval: 5s
      timeout: 3s
      retries: 10

  web:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: lab19-web
    command: python app.py
    ports:
      - "5000:5000"
    volumes:
      - ./app:/code
    working_dir: /code
    depends_on:
      rabbitmq:
        condition: service_healthy

  celery:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: lab19-celery
    command: celery -A celery_worker.celery worker --loglevel=info --concurrency=1
    volumes:
      - ./app:/code
    working_dir: /code
    depends_on:
      rabbitmq:
        condition: service_healthy
EOF
```

Port 5672 is the AMQP protocol port. Port 15672 is the management UI.

## Step 6: Create the Celery worker module

Run this command to create `app/celery_worker.py`:

```bash
cat > app/celery_worker.py << 'EOF'
# app/celery_worker.py
from celery import Celery

celery = Celery(
    "lab19",
    broker="amqp://guest:guest@rabbitmq:5672//",
    backend="rpc://",
)

celery.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_acks_on_failure_or_timeout=False,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)
EOF
```

Three settings matter here. `task_acks_late=True` flips ack timing. `task_reject_on_worker_lost=True` makes RabbitMQ requeue the task if the worker crashes. `worker_prefetch_multiplier=1` keeps each worker from grabbing a stack of tasks at once.

## Step 7: Create the task module

Run this command to create `app/tasks.py`:

```bash
cat > app/tasks.py << 'EOF'
# app/tasks.py
import time
from celery_worker import celery


@celery.task(name="tasks.run_background_task", bind=True, acks_late=True)
def run_background_task(self, task_id: str, duration: int = 5) -> dict:
    print(f"[Task {task_id}] started", flush=True)
    for second in range(1, duration + 1):
        time.sleep(1)
        print(f"[Task {task_id}] processing ({second}/{duration})", flush=True)
    print(f"[Task {task_id}] completed", flush=True)
    return {"task_id": task_id, "duration": duration}
EOF
```

The `@celery.task(acks_late=True)` decorator also enables late acks at the task level.

## Step 8: Create the Flask API

Run this command to create `app/app.py`:

```bash
cat > app/app.py << 'EOF'
# app/app.py
import uuid

from celery_worker import celery
from flask import Flask, jsonify, request
from tasks import run_background_task

app = Flask(__name__)


@app.route("/tasks", methods=["POST"])
def create_task():
    payload = request.get_json(silent=True) or {}
    duration = int(payload.get("duration", 5))
    task_id = uuid.uuid4().hex[:8]

    run_background_task.apply_async(args=[task_id, duration])

    return jsonify({
        "status": "accepted",
        "task_id": task_id,
        "message": "Task published to RabbitMQ",
    }), 202


@app.route("/", methods=["GET"])
def index():
    return jsonify({"service": "lab19-celery-rabbitmq", "status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
EOF
```

`apply_async` posts the task to the broker. The HTTP handler does not wait on it.

## Step 9: Verify the file layout

```bash
find ~/lab-19 -type f
```

Expected output:

```
/home/poridhian/lab-19/Dockerfile
/home/poridhian/lab-19/docker-compose.yml
/home/poridhian/lab-19/requirements.txt
/home/poridhian/lab-19/app/app.py
/home/poridhian/lab-19/app/celery_worker.py
/home/poridhian/lab-19/app/tasks.py
```

## Step 10: Build and start the stack

```bash
cd ~/lab-19
docker compose up -d --build
```

The first run downloads three images and can take a minute. Subsequent runs are instant.

## Step 11: Wait for the broker to become healthy

```bash
docker compose ps
```

`lab19-rabbitmq` shows `healthy` once the broker accepts AMQP connections.

## Step 12: Open the RabbitMQ Management UI

Open a browser and visit:

```
http://localhost:15672
```

From your host machine, replace `localhost` with the VM IP:

```
http://<VM-IP>:15672
```

Login with:

- Username: `guest`
- Password: `guest`

The dashboard shows three sections at the top: Overview, Connections, and Channels. The queue view is empty because no task has been published yet.

## Step 13: Trigger a task from curl

```bash
curl -X POST http://localhost:5000/tasks \
  -H "Content-Type: application/json" \
  -d '{"duration": 5}'
```

Expected response within a few milliseconds:

```json
{
  "status": "accepted",
  "task_id": "a1b2c3d4",
  "message": "Task published to RabbitMQ"
}
```

## Step 14: Inspect the queue in the Management UI

Refresh the Management UI. Click the **Queues** tab.

The `celery` queue appears with these columns:

- Name: `celery`
- Type: `classic`
- Durable: marked with a green **D** badge
- Messages: `0`

The **D** badge confirms the queue is durable and survives a broker restart.

## Step 15: Verify durable queues by restarting RabbitMQ

```bash
docker compose restart rabbitmq
```

Wait ten seconds, then:

```bash
docker compose ps
```

`lab19-rabbitmq` returns to `healthy`. Refresh the Management UI. The `celery` queue is still listed and still shows the **D** badge.

## Step 16: Verify late acks by killing the worker mid-task

Trigger a longer task so you have time to kill the worker:

```bash
curl -X POST http://localhost:5000/tasks \
  -H "Content-Type: application/json" \
  -d '{"duration": 30}'
```

Note the `task_id` from the response.

Within two seconds, force-kill the Celery worker:

```bash
docker kill lab19-celery
```

The container stops without graceful shutdown. RabbitMQ never received an ack for the task, so the message stays on the queue.

Restart the worker:

```bash
docker compose up -d celery
```

Watch the logs:

```bash
docker compose logs -f celery
```

You see a new `[Task a1b2c3d4] started` line followed by `processing (1/30)`. The same `task_id`, restarted from scratch. With early acks, this task would have been silently lost.

## Step 17: Stop the stack

```bash
docker compose down
```

Add `-v` to also remove the RabbitMQ volume for a clean slate:

```bash
docker compose down -v
```

## Next Steps

Lab 20 extends the same stack with automatic retries and exponential backoff so transient failures recover on their own.