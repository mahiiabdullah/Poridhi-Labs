# Lab 20: Configuring Celery Task Retry Behavior

**Module 60 — Flask, Celery, and RabbitMQ**

This lab adds automatic retry behavior to Celery tasks. When a task raises an exception, Celery re-runs it automatically after a delay that grows exponentially. After a configured number of attempts the task fails permanently.

## Architecture

<p align="center"><img src="./images/architecture.svg" alt="Lab 20 Architecture"></p>

## What You Will Build

A Flask API that publishes a task designed to fail. The Celery worker catches the exception, waits, retries the task, waits longer, retries again. Each retry is logged so you can see the exponential backoff in the worker logs. After `max_retries` attempts the task fails permanently.

## Step 1: Create the project directory

```bash
mkdir ~/lab-20 && cd ~/lab-20
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

```bash
cat > Dockerfile << 'EOF'
FROM python:3.12-slim

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
EOF
```

## Step 5: Create the docker-compose file

```bash
cat > docker-compose.yml << 'EOF'
services:
  rabbitmq:
    image: rabbitmq:3-management
    container_name: lab20-rabbitmq
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
    container_name: lab20-web
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
    container_name: lab20-celery
    command: celery -A celery_worker.celery worker --loglevel=info --concurrency=1
    volumes:
      - ./app:/code
    working_dir: /code
    depends_on:
      rabbitmq:
        condition: service_healthy
EOF
```

## Step 6: Create the Celery worker module

Run this command to create `app/celery_worker.py`:

```bash
cat > app/celery_worker.py << 'EOF'
# app/celery_worker.py
from celery import Celery

celery = Celery(
    "lab20",
    broker="amqp://guest:guest@rabbitmq:5672//",
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

## Step 7: Create the task module with retry behavior

Run this command to create `app/tasks.py`:

```bash
cat > app/tasks.py << 'EOF'
# app/tasks.py
import time

from celery.utils.log import get_task_logger
from celery_worker import celery

logger = get_task_logger(__name__)


class TransientError(Exception):
    """Raised by the task to simulate a flaky downstream dependency."""


@celery.task(
    name="tasks.flaky_task",
    bind=True,
    autoretry_for=(TransientError,),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=5,
    acks_late=True,
)
def flaky_task(self, task_id: str, fail_until: int = 99) -> dict:
    attempt = self.request.retries + 1
    logger.info("[Task %s] attempt %s starting", task_id, attempt)

    time.sleep(1)

    if attempt < fail_until:
        logger.warning("[Task %s] attempt %s failed on purpose", task_id, attempt)
        raise TransientError(f"simulated failure on attempt {attempt}")

    logger.info("[Task %s] attempt %s succeeded", task_id, attempt)
    return {"task_id": task_id, "attempt": attempt, "status": "ok"}
EOF
```

The decorator arguments work together as follows:

- `autoretry_for=(TransientError,)` — only retry when this exception is raised.
- `retry_backoff=True` — double the wait between attempts.
- `retry_backoff_max=60` — cap each delay at sixty seconds.
- `retry_jitter=True` — add a small random offset to each delay.
- `max_retries=5` — give up after six total attempts.

The `fail_until` parameter controls whether the task ever succeeds. A value larger than `max_retries + 1` lets the task keep failing so you can watch the full retry sequence.

## Step 8: Create the Flask API

Run this command to create `app/app.py`:

```bash
cat > app/app.py << 'EOF'
# app/app.py
import uuid

from flask import Flask, jsonify, request
from tasks import flaky_task

app = Flask(__name__)


@app.route("/tasks", methods=["POST"])
def create_task():
    payload = request.get_json(silent=True) or {}
    fail_until = int(payload.get("fail_until", 99))
    task_id = uuid.uuid4().hex[:8]

    flaky_task.apply_async(args=[task_id, fail_until])

    return jsonify({
        "status": "accepted",
        "task_id": task_id,
        "fail_until": fail_until,
        "message": "Failing task published; will retry with exponential backoff",
    }), 202


@app.route("/", methods=["GET"])
def index():
    return jsonify({"service": "lab20-celery-retry", "status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
EOF
```

## Step 9: Verify the file layout

```bash
find ~/lab-20 -type f
```

Expected output:

```
/home/poridhian/lab-20/Dockerfile
/home/poridhian/lab-20/docker-compose.yml
/home/poridhian/lab-20/requirements.txt
/home/poridhian/lab-20/app/app.py
/home/poridhian/lab-20/app/celery_worker.py
/home/poridhian/lab-20/app/tasks.py
```

## Step 10: Build and start the stack

```bash
cd ~/lab-20
docker compose up -d --build
```

Wait for RabbitMQ to pass its health check:

```bash
docker compose ps
```

`lab20-rabbitmq` shows `healthy`.

## Step 11: Open the Load Balancer modal in the lab UI

Open the **Load Balancer** modal in the lab UI (top-right). Run this once to find the IP to enter:

```bash
hostname -I
```

Sample output:

```
10.61.7.107 172.17.0.1 100.80.176.159 172.18.0.1
```

Use the first IP printed as `LB_IP`. Open the Load Balancer modal.

![](./images/load-balancer-modal.png)

Expose one port:

| Enter IP | Enter Port |
|----------|------------|
| `LB_IP` | `5000` (Flask API) |

Click **Expose**. Copy the generated `.lb.poridhi.io` URL — the rest of the lab uses it as `<FLASK-LB-URL>`.

## Step 12: Trigger a failing task

Send a POST request with a high `fail_until` so the task never succeeds:

```bash
curl -X POST <FLASK-LB-URL>/tasks \
  -H "Content-Type: application/json" \
  -d '{"fail_until": 99}'
```

Expected response within milliseconds:

```json
{
  "status": "accepted",
  "task_id": "a1b2c3d4",
  "fail_until": 99,
  "message": "Failing task published; will retry with exponential backoff"
}
```

## Step 13: Watch the worker retry the task

Open a second terminal and follow the Celery worker logs:

```bash
cd ~/lab-20
docker compose logs -f celery
```

The logs show six attempts in total. Each line reveals the backoff in action.

Expected sequence (timings approximate):

```
[INFO] [Task a1b2c3d4] attempt 1 starting
[WARNING] [Task a1b2c3d4] attempt 1 failed on purpose
[INFO] Task ... retrying in 1s
[INFO] [Task a1b2c3d4] attempt 2 starting
[WARNING] [Task a1b2c3d4] attempt 2 failed on purpose
[INFO] Task ... retrying in 2s
[INFO] [Task a1b2c3d4] attempt 3 starting
[WARNING] [Task a1b2c3d4] attempt 3 failed on purpose
[INFO] Task ... retrying in 4s
[INFO] [Task a1b2c3d4] attempt 4 starting
[WARNING] [Task a1b2c3d4] attempt 4 failed on purpose
[INFO] Task ... retrying in 8s
[INFO] [Task a1b2c3d4] attempt 5 starting
[WARNING] [Task a1b2c3d4] attempt 5 failed on purpose
[INFO] Task ... retrying in 16s
[INFO] [Task a1b2c3d4] attempt 6 starting
[WARNING] [Task a1b2c3d4] attempt 6 failed on purpose
[ERROR] Task ... failed permanently after 5 retries
```

Notice the doubling delay: 1s, 2s, 4s, 8s, 16s.

## Step 14: Trigger a task that recovers

Make a task succeed on the third attempt by sending `fail_until: 3`:

```bash
curl -X POST <FLASK-LB-URL>/tasks \
  -H "Content-Type: application/json" \
  -d '{"fail_until": 3}'
```

Follow the logs again:

```bash
docker compose logs -f celery
```

The task fails on attempts one and two, retries, and succeeds on attempt three. The final log line reads `attempt 3 succeeded`.

## Step 15: Tune the backoff window

Open `app/tasks.py` with `cat`:

```bash
cat app/tasks.py
```

Replace the decorator block with the following lines so the demo finishes quickly:

```python
@celery.task(
    name="tasks.flaky_task",
    bind=True,
    autoretry_for=(TransientError,),
    retry_backoff=True,
    retry_backoff_max=4,
    retry_jitter=False,
    max_retries=5,
    acks_late=True,
)
```

`retry_backoff_max=4` caps each retry at four seconds. `retry_jitter=False` removes randomness so the doubling pattern is exact.

Restart the worker:

```bash
docker compose restart celery
```

Trigger the failing task again:

```bash
curl -X POST <FLASK-LB-URL>/tasks \
  -H "Content-Type: application/json" \
  -d '{"fail_until": 99}'
```

The retries now happen at 1s, 2s, 4s, 4s, 4s instead of growing without bound.

## Step 16: Stop the stack

```bash
docker compose down
```

Add `-v` to remove the RabbitMQ volume for a clean slate:

```bash
docker compose down -v
```

## Next Steps

This lab closes Module 60. The complete arc is:

- Lab 18: in-process threading inside Flask.
- Lab 19: durable queues plus late acks so the broker does not lose work.
- Lab 20: automatic retries with exponential backoff so transient failures recover on their own.