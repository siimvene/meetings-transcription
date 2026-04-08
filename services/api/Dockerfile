FROM python:3.13-slim

WORKDIR /app

RUN apt-get update && \
    apt-get install --yes --no-install-recommends curl ffmpeg && \
    rm -rf /var/cache/apt/archives /var/lib/apt/lists/*

RUN pip install --no-cache-dir fastapi uvicorn httpx websockets python-multipart aiofiles

COPY services/api/main.py ./

RUN mkdir -p /app/data/transcripts

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
