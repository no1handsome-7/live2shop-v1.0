FROM pytorch/pytorch:2.5.1-cuda12.4-cudnn9-runtime

RUN apt-get update && apt-get install -y --no-install-recommends protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY services/rec-model-server/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY proto /proto
RUN mkdir -p /app/gen && \
    python -m grpc_tools.protoc -I/proto --python_out=/app/gen \
      $(find /proto -name '*.proto' | sort) && \
    find /app/gen -type d -exec sh -c 'touch "$1/__init__.py"' _ {} \;

COPY ml/rec_model/live2shop_rec /app/live2shop_rec
COPY services/rec-model-server/app /app/app
COPY services/rec-model-server/index_items.py /app/index_items.py

ENV PYTHONPATH=/app/gen:/app
EXPOSE 8092
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8092"]
