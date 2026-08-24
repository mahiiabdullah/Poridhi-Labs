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