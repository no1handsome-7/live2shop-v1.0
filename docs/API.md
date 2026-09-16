# Live2Shop HTTP API

## Behavior

`POST /api/v1/events/behavior`

Example:

```json
{
  "user_id": 10001,
  "session_id": "UUID",
  "room_id": 20001,
  "stream_session_id": "UUID",
  "action": "LIKE",
  "event_time_ms": 1789400555000,
  "playhead_pts_ms": 318420,
  "client_seq": 17,
  "foreground": true,
  "playback_rate": 1.0
}
```

Actions:

```text
ENTER_ROOM
HEARTBEAT
LIKE
FOLLOW
ITEM_CLICK
EXIT_ROOM
```

Heartbeat additionally sends:

```json
{
  "previous_playhead_pts_ms": 308500,
  "effective_watch_ms": 9860
}
```

---

## Feedback

`POST /api/v1/events/feedback`

```json
{
  "impression_id": "UUID",
  "request_id": "UUID",
  "user_id": 10001,
  "item_id": 300001,
  "position": 1,
  "action": "IMPRESSION",
  "event_time_ms": 1789400557000,
  "experiment_id": "live2shop-ranking-v1",
  "experiment_variant": "B",
  "rec_model_name": "rec-model",
  "rec_model_version": "rec-model-v1"
}
```

---

## Recommendations

`POST /api/v1/recommendations`

```json
{
  "user_id": 10001,
  "session_id": "UUID",
  "scene": "POST_LIVE",
  "trigger": "EXIT_ROOM",
  "context_room_id": 20001,
  "min_source_event_time_ms": 1789400555000,
  "limit": 20
}
```

Response includes:

```text
request_id
interest_version
interest_fresh
interest_source_watermark_ms
experiment_id / variant
model version
latency
items[]
```

Each item has a unique `impression_id`.

---

## Debug

```text
GET /api/v1/live/{room_id}/timeline
GET /api/v1/debug/live/{room_id}/state
GET /api/v1/debug/users/{user_id}/interest
```

Debug endpoints are for local demo/operations, not a public production API.

---

## Health/Metrics

```text
GET :8088/healthz    event-gateway
GET :8090/healthz    recommendation-api
GET :8091/healthz    media-segmenter
GET :8092/healthz    rec-model-server
GET :8093/healthz    content-ai

GET /metrics         Prometheus endpoints
```
