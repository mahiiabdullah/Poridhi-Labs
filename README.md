# Lab 18: Flask API with Background Tasks

**Module 5 — Distributed Systems Foundations**

This lab teaches the simplest possible background-task pattern using Flask and Python threading. The API accepts a request, starts work on a separate thread, and returns immediately.

<p align="center"><img src="./images/architecture.drawio.svg" alt="Flask API with background thread architecture"></p>

## What You Will Build

A Flask application with one POST endpoint that starts a background task and returns a response without waiting for the task to finish.

## Prerequisites

- Python 3.10 or higher.
- Flask installed via `pip install flask`.
- Postman or `curl` for testing.

## Step 1: Open the project in Puku CLI

Open the project folder in Puku CLI. Run all commands in its integrated terminal. Keep this file open in the Markdown preview while you work.

## Step 2: Create the background task file

Run this command to create `tasks.py`:

```bash
cat > tasks.py << 'EOF'
# tasks.py
import time


def run_background_task(task_id: str, duration: int = 5) -> None:
    print(f"[Task {task_id}] Task started", flush=True)
    for second in range(1, duration + 1):
        time.sleep(1)
        print(f"[Task {task_id}] Task processing... ({second}/{duration})", flush=True)
    print(f"[Task {task_id}] Task completed", flush=True)
EOF
```

## Step 3: Create the Flask application

Run this command to create `app.py`:

```bash
cat > app.py << 'EOF'
# app.py
import uuid
from threading import Thread
from flask import Flask, jsonify, request
from tasks import run_background_task

app = Flask(__name__)


@app.route("/tasks", methods=["POST"])
def create_task():
    payload = request.get_json(silent=True) or {}
    duration = int(payload.get("duration", 5))
    task_id = uuid.uuid4().hex[:8]

    thread = Thread(target=run_background_task, args=(task_id, duration))
    thread.start()

    return jsonify({
        "status": "accepted",
        "task_id": task_id,
        "message": "Task started in the background",
    }), 202


@app.route("/", methods=["GET"])
def index():
    return jsonify({"service": "flask-background-tasks", "status": "ok"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
EOF
```

## Step 4: Verify the files

```bash
ls
```

## Step 5: Start the Flask server

```bash
python app.py
```

![](./images/output-1.png)

## Step 6: Check the health endpoint

Open a second terminal in Puku CLI and run:

```bash
curl http://127.0.0.1:5000/
```

![](./images/output-2.png)

## Step 7: Trigger a background task

Send a POST request with a duration of 5 seconds:

```bash
curl -X POST http://127.0.0.1:5000/tasks \
  -H "Content-Type: application/json" \
  -d '{"duration": 5}'
```

![](./images/output-3.png)

The response arrives in milliseconds. The terminal from Step 5 keeps printing progress messages for the next 5 seconds.

## Step 8: Trigger three tasks in parallel

Send three requests quickly with different durations:

```bash
curl -X POST http://127.0.0.1:5000/tasks -H "Content-Type: application/json" -d '{"duration": 5}' && \
curl -X POST http://127.0.0.1:5000/tasks -H "Content-Type: application/json" -d '{"duration": 3}' && \
curl -X POST http://127.0.0.1:5000/tasks -H "Content-Type: application/json" -d '{"duration": 2}'
```

![](./images/output-4.png)

All three responses return immediately. The terminal shows three tasks running in parallel and finishing at different times.

## Step 9: Test the same flow with Postman

Open Postman on your host machine. Create a new POST request to `http://<VM-IP>:5000/tasks` with header `Content-Type: application/json` and body `{"duration": 5}`. Click Send and confirm the response returns in milliseconds.

<p align="center"><img src="./images/postman.png" alt="Postman POST /tasks returning 202 immediately"></p>

## Step 10: Stop the server

Press `Ctrl+C` in the terminal running Flask.

## Next Steps

This lab establishes the API plus background-task pattern that Lab 19 extends with Celery and Redis. The replacement swaps the in-process thread for a distributed worker queue.
