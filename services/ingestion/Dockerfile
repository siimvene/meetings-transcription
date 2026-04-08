FROM python:3.13-slim

WORKDIR /app

RUN pip install --no-cache-dir grpcio grpcio-tools websockets httpx

# Copy proto definition and generate stubs
COPY proto/audio_ingestion.proto /proto/audio_ingestion.proto
RUN python -m grpc_tools.protoc \
    -I/proto \
    --python_out=/app \
    --grpc_python_out=/app \
    /proto/audio_ingestion.proto

COPY services/ingestion/ ./

ENV PYTHONUNBUFFERED=1

EXPOSE 50051

CMD ["python", "main.py"]
