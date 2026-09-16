FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY services/derived-sink/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY proto /proto
RUN mkdir -p /app/gen && \
    python -m grpc_tools.protoc -I/proto --python_out=/app/gen \
      $(find /proto -name '*.proto' | sort) && \
    find /app/gen -type d -exec sh -c 'touch "$1/__init__.py"' _ {} \;
COPY services/derived-sink/main.py .
ENV PYTHONPATH=/app/gen:/app
CMD ["python","main.py"]
