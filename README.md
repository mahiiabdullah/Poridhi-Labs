# Lab 18 — Flask API with Background Tasks

A beginner-friendly lab that shows how a Flask API can accept a request, start work in the background, and return a response **without waiting** for that work to finish.

This is the foundation for later labs that introduce Celery, Redis, and distributed tracing.

---

## Table of Contents

- [Introduction](#introduction)
- [Learning Objectives](#learning-objectives)
- [Prerequisites](#prerequisites)
- [Chapter 1 — Flask API and Background Tasks](#chapter-1--flask-api-and-background-tasks)
- [Chapter 2 — Build the Flask API](#chapter-2--build-the-flask-api)
- [Chapter 3 — Test with Postman](#chapter-3--test-with-postman)
- [Troubleshooting](#troubleshooting)
- [Next Steps](#next-steps)

---

## Introduction

When an API receives a request, the server normally processes the work, then returns a response. If the work takes several seconds, the client has to wait.

A **background task** lets the API start the work and respond immediately, while the work continues independently. This is the core idea behind async APIs, queues, and workers.

In this lab, you will build the simplest possible version of this pattern using Flask and Python's standard library.

---

## Learning Objectives

By the end of this lab, you will be able to:

- Explain what a background task is.
- Distinguish synchronous execution from background execution.
- Build a minimal Flask API with one POST endpoint.
- Start a background task using Python's standard library.
- Test the API with Postman and verify async behavior.

---

## Prerequisites

- Python 3.x installed inside the Poridhi VM.
- `pip` available.
- Postman installed (or use `curl` from the VM).

> **How this lab is organized:** All code is embedded directly in this README. You do not need to create any extra files. Just copy each code block into a Python file inside the VM and run it.

---

## Chapter 1 — Flask API and Background Tasks

### Think First

- What happens if the work an API needs to do takes several seconds?
- Should the client wait?
- What if many clients send requests at once?

### Quick Explanation

A **Flask API** handles HTTP requests. By default, the API waits for the request handler to finish, then sends the response back to the client.

A **background task** is work that runs separately from the request flow. The API can start the work and immediately respond — the work continues on its own.

```
Synchronous:   Client → API → wait 5s → API → Client
Background:    Client → API → Client    (background work continues elsewhere)
```

---

## Chapter 2 — Build the Flask API

### Step 1 — Install Flask

Run this once inside the Poridhi VM:

```bash
pip install flask
```

### Step 2 — Create the background task file

> ⚠️ **Important:** Do NOT type `python` alone — that opens the interactive REPL (`>>>`). You need to create a file, then run it.

We'll use the simplest possible method that works in any terminal — a shell heredoc. Run this single command in the VM and paste the code at the end:

```bash
cat > tasks.py << 'EOF'
```

Paste the code below, then on a new line type:

```
EOF
```

and press **Enter**. The shell will write everything between the two `EOF` markers into `tasks.py`.

```python
# tasks.py
import time


def run_background_task(task_id: str, duration: int = 5) -> None:
    """
    A simple background task that simulates work.

    It prints clear progress messages so beginners can
    observe asynchronous behavior in the terminal.
    """
    print(f"[Task {task_id}] Task started", flush=True)

    for second in range(1, duration + 1):
        time.sleep(1)
        print(f"[Task {task_id}] Task processing... ({second}/{duration})", flush=True)

    print(f"[Task {task_id}] Task completed", flush=True)
```

### Step 3 — Create the Flask application

Use the same heredoc trick for `app.py`:

```bash
cat > app.py << 'EOF'
```

Paste the following code, then on a new line type `EOF` and press **Enter**:

```python
# app.py
import uuid
from threading import Thread

from flask import Flask, jsonify, request

from tasks import run_background_task

app = Flask(__name__)


@app.route("/tasks", methods=["POST"])
def create_task():
    """
    Endpoint that receives a request, starts a background task,
    and returns a response immediately without waiting for the
    background task to finish.
    """
    # 1. Request is received here
    payload = request.get_json(silent=True) or {}
    duration = int(payload.get("duration", 5))

    # 2. Generate a task id so we can identify the background work
    task_id = uuid.uuid4().hex[:8]

    # 3. Start the background task on a separate thread.
    #    The thread runs independently of the request flow.
    thread = Thread(target=run_background_task, args=(task_id, duration))
    thread.start()

    # 4. Return the response immediately.
    #    We do NOT wait for the background task to finish.
    return jsonify(
        {
            "status": "accepted",
            "task_id": task_id,
            "message": "Task started in the background",
        }
    ), 202


@app.route("/", methods=["GET"])
def index():
    return jsonify({"service": "flask-background-tasks", "status": "ok"})


if __name__ == "__main__":
    # Run the Flask development server
    app.run(host="127.0.0.1", port=5000, debug=False)
```

### Step 4 — Verify your files

Before running, make sure both files exist in the same directory:

```bash
ls
```

You should see `app.py` and `tasks.py` listed.

### Step 5 — Run the application

Inside the VM, in the same directory as `app.py`:

```bash
python app.py
```

> ⚠️ Use `python app.py` (with the filename). Typing just `python` opens the REPL and your code won't be saved to a file.

> 💡 **Tip:** If your VM doesn't have `nano`, the heredoc method above works in any bash shell. If you'd rather use an editor and `nano` is missing, try `vi app.py` (press `i` to insert, paste, then `Esc` → `:wq` → `Enter` to save).

**📷 Screenshot 1 — Flask app running**

> Save your screenshot as `images/01-flask-running.png`

Expected terminal output:

```
 * Serving Flask app 'app'
 * Debug mode: off
WARNING: This is a development server. Do not use it in a production deployment. Use a production WSGI server instead.
 * Running on http://127.0.0.1:5000
Press CTRL+C to quit
```

### Where the code does its work

- **Request is received:** `POST /tasks` route in `app.py`.
- **Background task starts:** `Thread(target=run_background_task, ...).start()`.
- **Why the API responds immediately:** Flask returns the JSON response right after `thread.start()` — it never waits for `run_background_task` to finish. The thread runs independently in the background.

---

## Chapter 3 — Test with Postman

### Postman Setup

1. Open Postman.
2. Create a new request.
3. Set method to **POST**.
4. URL: `http://127.0.0.1:5000/tasks`.
5. Headers tab → add: `Content-Type: application/json`.
6. Body tab → raw → JSON:

   ```json
   {"duration": 5}
   ```

   (`duration` is optional — defaults to 5 seconds.)
7. Click **Send**.

**📷 Screenshot 2 — Postman POST /tasks**

> Save your screenshot as `images/02-postman-request.png`

Expected immediate response (status `202`):

```json
{
  "status": "accepted",
  "task_id": "abc12345",
  "message": "Task started in the background"
}
```

### Observe Background Execution

After sending the request, watch the Flask terminal — the progress messages appear even though Postman already got its response.

**📷 Screenshot 3 — Terminal output**

> Save your screenshot as `images/03-terminal-progress.png`

Expected terminal output:

```
[Task abc12345] Task started
[Task abc12345] Task processing... (1/5)
[Task abc12345] Task processing... (2/5)
[Task abc12345] Task processing... (3/5)
[Task abc12345] Task processing... (4/5)
[Task abc12345] Task processing... (5/5)
[Task abc12345] Task completed
```

### Multiple Requests Test

Send 3 requests quickly in Postman (different durations: 5, 3, 2). You will see all 3 tasks running in parallel.

**📷 Screenshot 4 — Multiple requests**

> Save your screenshot as `images/04-multiple-requests.png`

Expected terminal output:

```
[Task A1B2C3D4] Task started
[Task E5F6G7H8] Task started
[Task I9J0K1L2] Task started
[Task I9J0K1L2] Task processing... (1/2)
[Task E5F6G7H8] Task processing... (1/3)
[Task A1B2C3D4] Task processing... (1/5)
[Task I9J0K1L2] Task completed
[Task E5F6G7H8] Task processing... (2/3)
[Task E5F6G7H8] Task processing... (3/3)
[Task E5F6G7H8] Task completed
[Task A1B2C3D4] Task processing... (2/5)
[Task A1B2C3D4] Task processing... (3/5)
[Task A1B2C3D4] Task processing... (4/5)
[Task A1B2C3D4] Task processing... (5/5)
[Task A1B2C3D4] Task completed
```

### What happens when you send 3 requests quickly

- Each request returns `202 Accepted` immediately.
- All 3 background tasks start at the same time.
- Each task completes independently after its own duration.
- Tasks overlap in the terminal — proving they run in parallel.

---

## Troubleshooting

| Problem                          | Solution                                            |
|----------------------------------|-----------------------------------------------------|
| `ModuleNotFoundError: flask`     | Run `pip install flask`                             |
| Port already in use              | Change `port=5000` in `app.py`                      |
| Background task does not print   | Run Flask directly with `python app.py` (no reloader) |

---

## Next Steps

In later labs, you will replace this simple `threading` background task with **Celery + Redis** and add **distributed tracing** to observe how context flows between the API and the worker.
