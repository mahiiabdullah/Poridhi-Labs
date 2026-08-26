# Lab 21: Deploying RabbitMQ as a Dedicated Broker

**Module 61 — Deployment and Monitoring**

RabbitMQ runs in its own Docker Compose stack. Flask and Celery run in a second stack that joins the broker's network by name. Stopping the app stack leaves the broker and its messages intact.

## Architecture

<p align="center"><img src="https://github.com/poridhioss/Module_60_Message-Brokers-for-Celery/blob/main/module-61/lab-21/images/architecture.svg" alt="Lab 21 Architecture"></p>

## Concept

| Term               | Description                                                                                            |
|--------------------|--------------------------------------------------------------------------------------------------------|
| Docker Compose     | A tool for defining and running multi-container applications using a single declarative YAML file.     |
| Docker Network     | A virtual network that lets containers resolve each other by service name without hard-coded IPs.      |
| Named Volume       | A persistent storage volume managed by Docker, identified by name, that survives container restarts.   |
| Healthcheck        | A container-level probe that lets Docker report `healthy` only when the broker is actually accepting AMQP traffic. |
| AMQP               | The wire protocol RabbitMQ uses to accept and deliver messages between publishers and consumers.       |

Splitting the broker into its own stack means the broker owns its data volume and its own network, and the application stack joins that network by name. If you tear down the application stack to rebuild it, RabbitMQ and its queued messages are untouched. The healthcheck is what stops the app stack from starting Celery before the broker is ready to accept connections.

## What You Will Build

Two stacks on one host. A broker stack that owns a named Docker network and a named volume. An application stack that joins the network and reads its broker URL from the network — no IPs, no hostnames pinned to one container.

## Step 1: Create the directories

```bash
mkdir -p ~/lab-21/broker ~/lab-21/app/src
cd ~/lab-21
```

## Step 2: Confirm Docker and Compose

```bash
docker --version
docker compose version
```

![](https://github.com/poridhioss/Module_60_Message-Brokers-for-Celery/blob/main/module-61/lab-21/images/Docker--version.png)

## Step 3: Write the broker compose

```bash
cat > ~/lab-21/broker/docker-compose.yml << 'EOF'
services:
  rabbitmq:
    image: rabbitmq:3-management
    container_name: lab21-rabbitmq
    hostname: lab21-rabbitmq
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
    name: lab21-broker-net
    driver: bridge
EOF
```

The fixed `name: lab21-broker-net` is what the app stack joins.

## Step 4: Start the broker

```bash
cd ~/lab-21/broker
docker compose up -d
docker compose ps
```

`lab21-rabbitmq` shows `healthy` once it accepts AMQP.

![](https://github.com/poridhioss/Module_60_Message-Brokers-for-Celery/blob/main/module-61/lab-21/images/Step_4_Start%20the%20broker.png)

## Step 5: Write the app files

```bash
cat > ~/lab-21/app/requirements.txt << 'EOF'
flask==3.0.3
celery==5.4.0
kombu==5.3.7
EOF

cat > ~/lab-21/app/Dockerfile << 'EOF'
FROM python:3.12-slim
WORKDIR /code
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
EOF

cat > ~/lab-21/app/src/celery_worker.py << 'EOF'
from celery import Celery

celery = Celery(
    "lab21",
    broker="amqp://guest:guest@lab21-rabbitmq:5672//",
    backend="rpc://",
)

celery.conf.update(
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)
EOF

cat > ~/lab-21/app/src/tasks.py << 'EOF'
import time
from celery_worker import celery

@celery.task(name="tasks.process_order", bind=True, acks_late=True)
def process_order(self, order_id: str, items: int = 3) -> dict:
    print(f"[Order {order_id}] processing {items} items", flush=True)
    for i in range(1, items + 1):
        time.sleep(1)
        print(f"[Order {order_id}] item {i}/{items} done", flush=True)
    return {"order_id": order_id, "items": items, "status": "done"}
EOF

cat > ~/lab-21/app/src/app.py << 'EOF'
import uuid
from flask import Flask, jsonify, request
from tasks import process_order

app = Flask(__name__)

@app.route("/orders", methods=["POST"])
def create_order():
    payload = request.get_json(silent=True) or {}
    items = int(payload.get("items", 3))
    order_id = uuid.uuid4().hex[:8]
    process_order.apply_async(args=[order_id, items])
    return jsonify({"status": "accepted", "order_id": order_id}), 202

@app.route("/", methods=["GET"])
def index():
    return jsonify({"service": "lab21", "status": "ok"})

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
EOF
```

## Step 6: Write the app compose

```bash
cat > ~/lab-21/app/docker-compose.yml << 'EOF'
services:
  web:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: lab21-web
    command: python app.py
    ports:
      - "5000:5000"
    volumes:
      - ./src:/code
    working_dir: /code

  celery:
    build:
      context: .
      dockerfile: Dockerfile
    container_name: lab21-celery
    command: celery -A celery_worker.celery worker --loglevel=info --concurrency=2
    volumes:
      - ./src:/code
    working_dir: /code

networks:
  default:
    name: lab21-broker-net
    external: true
EOF
```

`external: true` says the network already exists. The app stack joins it without creating it.

## Step 7: Start the app stack

```bash
cd ~/lab-21/app
docker compose up -d --build
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"
```

Three containers run: `lab21-rabbitmq`, `lab21-web`, `lab21-celery`.

![](https://github.com/poridhioss/Module_60_Message-Brokers-for-Celery/blob/main/module-61/lab-21/images/Step%207%20Start%20the%20app%20stack%20(2).png)
![](https://github.com/poridhioss/Module_60_Message-Brokers-for-Celery/blob/main/module-61/lab-21/images/Step%207%20Start%20the%20app%20stack%20(1).png)

## Step 8: Expose ports in the lab UI

Find the host IP:

```bash
hostname -I
```

Use the first IP as `LB_IP`.

![](https://github.com/poridhioss/Module_60_Message-Brokers-for-Celery/blob/main/module-61/lab-21/images/Hostname.png)

| Enter IP | Enter Port |
|----------|------------|
| `LB_IP` | `5000` (Flask API) |
| `LB_IP` | `15672` (RabbitMQ UI) |

![](https://github.com/poridhioss/Module_60_Message-Brokers-for-Celery/blob/main/module-61/lab-21/images/Loadbalancer.png)

## Step 9: Trigger a task

```bash
curl -X POST <FLASK-LB-URL>/orders \
  -H "Content-Type: application/json" \
  -d '{"items": 4}'
```

Response:

```json
{"status": "accepted", "order_id": "a1b2c3d4"}
```

![](https://github.com/poridhioss/Module_60_Message-Brokers-for-Celery/blob/main/module-61/lab-21/images/Step%209%20Trigger%20a%20task.png)

## Step 10: Watch the worker

```bash
cd ~/lab-21/app
docker compose logs -f celery
```

Press `Ctrl+C` to detach.

![](https://github.com/poridhioss/Module_60_Message-Brokers-for-Celery/blob/main/module-61/lab-21/images/Step%2011%20docker%20compose%20down.png)

## Step 11: Prove broker independence

Stop the app stack:

```bash
docker compose down
```

Check the broker is still up:

```bash
cd ~/lab-21/broker
docker compose ps
```

`lab21-rabbitmq` stays `healthy`. The queue definition and volume are intact.

![](https://github.com/poridhioss/Module_60_Message-Brokers-for-Celery/blob/main/module-61/lab-21/images/Step%2011%20docker%20compose%20ps.png)

## Step 12: Restart the app stack

```bash
cd ~/lab-21/app
docker compose up -d
docker compose logs celery
```

The worker reconnects to RabbitMQ without config changes.

![](https://github.com/poridhioss/Module_60_Message-Brokers-for-Celery/blob/main/module-61/lab-21/images/Step%2012%20Restart%20the%20app%20stack.png)

## Step 13: Tear down

```bash
docker compose down
cd ~/lab-21/broker && docker compose down -v
```

`-v` drops the named volume so the next run starts clean.

![](https://github.com/poridhioss/Module_60_Message-Brokers-for-Celery/blob/main/module-61/lab-21/images/Step%2013%20Tear%20down.png)


## Next Steps

Lab 22 keeps this two-stack broker pattern but runs the Flask API and the Celery worker in one container, with `supervisord` starting both, restarting them on crash, and writing each to its own log file.