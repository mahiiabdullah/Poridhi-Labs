# Lab 18 — Flask API with Background Tasks

A beginner-friendly lab showing how a Flask API can accept a request, start work in the background, and return a response **without waiting** for that work to finish. Foundation for later labs on Celery, Redis, and distributed tracing.

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

When an API receives a request, the server normally processes the work, then returns a response. If the work takes several seconds, the client waits.

A **background task** lets the API start the work and respond immediately. The work continues independently. This is the core idea behind async APIs, queues, and workers.

In this lab you build the simplest version using Flask and Python's standard library.

---

## Learning Objectives

- Explain what a background task is.
- Distinguish synchronous execution from background execution.
- Build a minimal Flask API with one POST endpoint.
- Start a background task using Python's standard library.
- Test the API with Postman and verify async behavior.

---

## Prerequisites

- Python 3.x installed inside the Poridhi VM.
- `pip` available.
- Postman installed on your host machine.

> All code is embedded in this README. Copy each code block into a Python file inside the VM and run it.

---

## Chapter 1 — Flask API and Background Tasks

### Think First

- What happens if the work an API needs to do takes several seconds?
- Should the client wait?
- What if many clients send requests at once?

### Quick Explanation

A **Flask API** handles HTTP requests. By default, the API waits for the handler to finish, then sends the response.

A **background task** runs separately from the request flow. The API can start the work and immediately respond.

```
Synchronous:   Client → API → wait 5s → API → Client
Background:    Client → API → Client    (background work continues elsewhere)
```

---

## Chapter 2 — Build the Flask API

### Step 1 — Install Flask

```bash
pip install flask
```

### Step 2 — Create `tasks.py`

Run this in the VM:

```bash
cat > tasks.py << 'EOF'
import time


def run_background_task(task_id: str, duration: int = 5) -> None:
    print(f"[Task {task_id}] Task started", flush=True)
    for second in range(1, duration + 1):
        time.sleep(1)
        print(f"[Task {task_id}] Task processing... ({second}/{duration})", flush=True)
    print(f"[Task {task_id}] Task completed", flush=True)
EOF
```

After typing `EOF` on its own line, press **Enter** to close the file.

### Step 3 — Create `app.py`

```bash
cat > app.py << 'EOF'
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
    app.run(host="127.0.0.1", port=5000, debug=False)
EOF
```

### Step 4 — Verify files

```bash
ls
```

You should see `app.py` and `tasks.py` in the output.

### Step 5 — Run the application

```bash
python app.py
```

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
- **Why the API responds immediately:** Flask returns the JSON response right after `thread.start()` — it never waits for `run_background_task` to finish. The thread runs independently.

---

## Chapter 3 — Test with Postman

Postman is an HTTP client used to test APIs visually. It lets you choose a method, enter a URL, attach headers and a body, and see the response. It runs on your host machine, not inside the Poridhi VM.

### 3.1 — Install and Open Postman

If you don't have Postman yet:

1. Go to https://www.postman.com/downloads/
2. Download the version for your operating system (Windows / macOS / Linux).
3. Install it and open it.
4. You can skip the sign-in — Postman works without an account, though it may show a small prompt. Close any "create account" tabs.

You should see the main Postman window with a blank request tab at the top.

### 3.2 — Find the API Address (Important)

The Flask app is running inside the Poridhi VM. Your host machine cannot directly reach `127.0.0.1` of the VM — that address means "this machine" and "this machine" is your host, not the VM.

The Poridhi VM usually exposes itself to your host on a special address. From the VM terminal, run:

```bash
hostname -I
```

You'll see one or more IP addresses like `10.0.0.5` or `192.168.x.x`. Pick the first one.

> In this lab we'll assume the VM IP is `10.0.0.5`. Replace it with your actual IP everywhere it appears.

### 3.3 — Create a New Request

1. Click the **+** button at the top of Postman (or press `Ctrl+N` / `Cmd+N`).
2. A new tab opens with the title "Untitled Request".
3. You'll see a request builder with:
   - A dropdown for the HTTP method (currently says `GET`)
   - A text field for the URL
   - Tabs below: **Params**, **Authorization**, **Headers**, **Body**, **Scripts**, **Tests**

### 3.4 — Configure the Request

**Set the method:**
- Click the dropdown that says `GET` and choose **`POST`**.

**Set the URL:**
- Click the URL field and type:
  ```
  http://10.0.0.5:5000/tasks
  ```
  (Replace `10.0.0.5` with your VM IP from step 3.2.)

**Add a header:**
- Click the **Headers** tab.
- Under the **Key** column, type: `Content-Type`
- Under the **Value** column, type: `application/json`

**Add a request body:**
- Click the **Body** tab.
- Select the radio button **`raw`** (it's on the right side of the tab).
- A dropdown next to the radio buttons says `Text` by default — click it and choose **`JSON`**.
- In the large text area, type:
  ```json
  {
    "duration": 5
  }
  ```

### 3.5 — Send the Request

- Click the blue **Send** button on the right side of the URL bar.
- Postman shows the response below in the lower half of the window.
- Look at the status code on the right side of the response area — it should be **`202 Accepted`**.
- The response body will be:
  ```json
  {
    "status": "accepted",
    "task_id": "abc12345",
    "message": "Task started in the background"
  }
  ```

Notice how the response arrives **immediately**, even though the task takes 5 seconds to complete.

**📷 Screenshot 2 — Postman POST /tasks**

> Save your screenshot as `images/02-postman-request.png`

### 3.6 — Observe Background Execution

Switch to the terminal where Flask is running. You'll see progress messages from the background task, even though Postman already received its response:

```
[Task abc12345] Task started
[Task abc12345] Task processing... (1/5)
[Task abc12345] Task processing... (2/5)
[Task abc12345] Task processing... (3/5)
[Task abc12345] Task processing... (4/5)
[Task abc12345] Task processing... (5/5)
[Task abc12345] Task completed
```

**📷 Screenshot 3 — Terminal output**

> Save your screenshot as `images/03-terminal-progress.png`

### 3.7 — Multiple Requests Test

To prove background tasks truly run in parallel:

1. Stay on the same Postman request.
2. Change the body to `{"duration": 2}` and click **Send**.
3. Quickly change it to `{"duration": 3}` and click **Send** again.
4. Quickly change it to `{"duration": 5}` and click **Send** a third time.

All three responses arrive almost instantly in Postman. Switch to the Flask terminal — you'll see three tasks started almost at the same time, with their progress messages interleaved:

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

The shorter task finishes first, then the medium one, then the longest. All three were running at the same time.

**📷 Screenshot 4 — Multiple requests**

> Save your screenshot as `images/04-multiple-requests.png`

### 3.8 — Optional: Save the Request

For future use:

1. Click **Save** (top right of the request tab).
2. Name it `Flask Lab 18 - Tasks`.
3. Choose **Create Collection**, name it `Lab 18`, and save.

Saved requests keep their method, URL, headers, and body, so you don't have to re-enter them next time.

---

## Troubleshooting

| Problem                          | Solution                                          |
|----------------------------------|---------------------------------------------------|
| `ModuleNotFoundError: flask`     | Run `pip install flask`                           |
| Port already in use              | Change `port=5000` in `app.py`                    |
| Connection refused from Postman  | Use the VM IP (`hostname -I`), not `127.0.0.1`    |
| Background task does not print   | Run Flask directly with `python app.py`           |
| `nano: command not found`        | Use the heredoc `cat > file.py << 'EOF'` method   |

---

## Next Steps

In later labs, you will replace this simple `threading` background task with **Celery + Redis** and add **distributed tracing** to observe how context flows between the API and the worker.
