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
