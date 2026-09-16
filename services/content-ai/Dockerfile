FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg protobuf-compiler libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/content-ai/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proto /proto
RUN mkdir -p /app/gen && \
    python -m grpc_tools.protoc -I/proto --python_out=/app/gen \
      $(find /proto -name '*.proto' | sort) && \
    find /app/gen -type d -exec sh -c 'touch "$1/__init__.py"' _ {} \;

COPY ml/commerce_vlm/live2shop_commerce /app/live2shop_commerce
COPY services/content-ai/app /app/app

ENV PYTHONPATH=/app/gen:/app
EXPOSE 8093
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8093"]
