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