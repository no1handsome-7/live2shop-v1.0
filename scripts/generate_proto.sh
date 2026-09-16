#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p gen/go gen/python gen/java

PROTO_FILES=$(find proto -name '*.proto' | sort)

protoc -I proto \
  --go_out=gen/go \
  --go_opt=paths=source_relative \
  ${PROTO_FILES}

python -m grpc_tools.protoc -I proto \
  --python_out=gen/python \
  ${PROTO_FILES}

protoc -I proto \
  --java_out=gen/java \
  ${PROTO_FILES}

find gen/python -type d -exec sh -c 'touch "$1/__init__.py"' _ {} \;

echo "Generated protobuf bindings under gen/{go,python,java}."
