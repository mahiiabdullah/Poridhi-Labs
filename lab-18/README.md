# Lab 18: Flask API with Background Tasks

**Module 5 — Distributed Systems Foundations**

This lab teaches the simplest possible background-task pattern using Flask and Python threading. The API accepts a request, starts work on a separate thread, and returns immediately.

## Architecture

<p align="center"><img src="https://github.com/mahiiabdullah/Poridhi-Labs/blob/main/lab-18/images/architecture.svg" alt="Lab 18 Architecture"></p>

## What You Will Build

A Flask application with one POST endpoint that starts a background task and returns a response without waiting for the task to finish.

## Installation

Install Flask inside a project-local virtual environment so the dependency stays isolated from the system Python.

### Step 1: Create the virtual environment

```bash
python -m venv venv
```

### Step 2: Activate the virtual environment

```bash
source venv/bin/activate
```

After activation, the shell prompt is prefixed with `(venv)`, for example:

```
(venv) poridhian@93b22aeb7c884c2b:~$
```

If you do not see `(venv)` in the prompt, the venv is not active. Run the `source venv/bin/activate` command again.

### Step 3: Install Flask

```bash
pip install flask
```

Confirm Flask is installed inside the active venv:

```bash
python -c "import flask; print(flask.__version__)"
```

The output should be a version string like `3.0.3`. If you see `ModuleNotFoundError: No module named 'flask'`, the venv is not active — go back to Step 2.

![](https://github.com/mahiiabdullah/Poridhi-Labs/blob/main/lab-18/images/Flask%20Install.png)

Keep the virtual environment active for the rest of the lab.

## Step 4: Create the background task file

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

## Step 5: Create the Flask application

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

## Step 6: Verify the files

```bash
ls
```

## Step 7: Start the Flask server

```bash
python app.py
```

![](https://github.com/mahiiabdullah/Poridhi-Labs/blob/main/lab-18/images/python_app_py.png)

## Step 8: Check the health endpoint

Open a second terminal in Puku CLI and run:

```bash
curl http://127.0.0.1:5000/
```

![](https://github.com/mahiiabdullah/Poridhi-Labs/blob/main/lab-18/images/Testing.png)

## Step 9: Trigger a background task

Send a POST request with a duration of 5 seconds:

```bash
curl -X POST http://127.0.0.1:5000/tasks \
  -H "Content-Type: application/json" \
  -d '{"duration": 5}'
```

![](https://github.com/mahiiabdullah/Poridhi-Labs/blob/main/lab-18/images/Duration_5.png)

The response arrives in milliseconds. The terminal from Step 7 keeps printing progress messages for the next 5 seconds.

## Step 10: Trigger three tasks in parallel

Send three requests quickly with different durations:

```bash
curl -X POST http://127.0.0.1:5000/tasks -H "Content-Type: application/json" -d '{"duration": 5}' && \
curl -X POST http://127.0.0.1:5000/tasks -H "Content-Type: application/json" -d '{"duration": 3}' && \
curl -X POST http://127.0.0.1:5000/tasks -H "Content-Type: application/json" -d '{"duration": 2}'
```

![](https://github.com/mahiiabdullah/Poridhi-Labs/blob/main/lab-18/images/output-4.png)

All three responses return immediately. The terminal shows three tasks running in parallel and finishing at different times.

## Step 11: Stop the server

Press `Ctrl+C` in the terminal running Flask.

## Next Steps

This lab establishes the API plus background-task pattern that Lab 19 extends with Celery and Redis. The replacement swaps the in-process thread for a distributed worker queue.
