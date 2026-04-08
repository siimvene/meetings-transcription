FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir asyncpg websockets httpx aio-pika fastapi uvicorn

COPY services/assembly/ ./

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["python", "main.py"]
