FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/media-segmenter/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proto /proto
RUN mkdir -p /app/gen && \
    python -m grpc_tools.protoc -I/proto --python_out=/app/gen \
      $(find /proto -name '*.proto' | sort) && \
    find /app/gen -type d -exec sh -c 'touch "$1/__init__.py"' _ {} \;

COPY services/media-segmenter/app /app/app

ENV PYTHONPATH=/app/gen:/app
EXPOSE 8091
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8091"]
