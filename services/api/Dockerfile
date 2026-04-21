FROM python:3.13-slim

WORKDIR /app

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update && \
    apt-get install --yes --no-install-recommends curl ffmpeg

RUN pip install --no-cache-dir fastapi uvicorn httpx websockets python-multipart aiofiles asyncpg PyJWT[crypto]

COPY services/api/main.py ./

RUN mkdir -p /app/data/transcripts

ENV PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
