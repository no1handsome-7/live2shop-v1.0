#!/usr/bin/env bash
set -euo pipefail

BROKER="${KAFKA_BROKERS:-kafka:9092}"
KAFKA_BIN="/opt/kafka/bin/kafka-topics.sh"

create() {
  local topic="$1"
  local retention_ms="$2"
  "$KAFKA_BIN" --bootstrap-server "$BROKER" \
    --create --if-not-exists \
    --topic "$topic" --partitions 3 --replication-factor 1 \
    --config "retention.ms=${retention_ms}"
}

DAY=$((24*60*60*1000))
create live2shop.user.behavior.v1 $((7*DAY))
create live2shop.media.segment.v1 $((1*DAY))
create live2shop.content.state.v1 $((7*DAY))
create live2shop.behavior.attributed.v1 $((7*DAY))
create live2shop.interest.updated.v1 $((1*DAY))
create live2shop.rec.served.v1 $((7*DAY))
create live2shop.rec.feedback.v1 $((30*DAY))
create live2shop.dead-letter.v1 $((7*DAY))

echo "Live2Shop topics ready."
