FROM golang:1.23-bookworm AS builder

RUN apt-get update && apt-get install -y --no-install-recommends protobuf-compiler \
    && rm -rf /var/lib/apt/lists/*
RUN go install google.golang.org/protobuf/cmd/protoc-gen-go@v1.36.1

WORKDIR /src
COPY proto ./proto
COPY services/event-gateway ./services/event-gateway

WORKDIR /src/services/event-gateway
RUN mkdir -p gen && \
    protoc -I /src/proto \
      --go_out=gen --go_opt=paths=source_relative \
      $(find /src/proto -name '*.proto' | sort)

RUN go mod tidy
RUN CGO_ENABLED=0 go build -o /out/event-gateway .

FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=builder /out/event-gateway /event-gateway
EXPOSE 8088
ENTRYPOINT ["/event-gateway"]
