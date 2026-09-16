package main

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"
	"github.com/segmentio/kafka-go"
	"google.golang.org/protobuf/proto"

	pb "github.com/live2shop/event-gateway/gen/live2shop/v1"
)

const (
	behaviorTopic = "live2shop.user.behavior.v1"
	feedbackTopic = "live2shop.rec.feedback.v1"
)

type app struct {
	kafka *kafka.Writer
	redis *redis.Client
	db    *pgxpool.Pool
}

var (
	requestCount = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "http_requests_total", Help: "HTTP requests", ConstLabels: prometheus.Labels{"service": "event-gateway"}},
		[]string{"path", "status"},
	)
	requestLatency = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{Name: "http_request_duration_seconds", Help: "HTTP request latency", ConstLabels: prometheus.Labels{"service": "event-gateway"}},
		[]string{"path"},
	)
	feedbackCount = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "recommendation_feedback_total", Help: "Recommendation feedback by action/experiment"},
		[]string{"action", "experiment_id", "variant"},
	)
)

type behaviorRequest struct {
	UserID                int64   `json:"user_id"`
	SessionID             string  `json:"session_id"`
	RoomID                int64   `json:"room_id"`
	StreamSessionID       string  `json:"stream_session_id"`
	Action                string  `json:"action"`
	EventTimeMs           int64   `json:"event_time_ms"`
	PlayheadPtsMs         *int64  `json:"playhead_pts_ms,omitempty"`
	PreviousPlayheadPtsMs *int64  `json:"previous_playhead_pts_ms,omitempty"`
	EffectiveWatchMs      *int64  `json:"effective_watch_ms,omitempty"`
	ItemID                *int64  `json:"item_id,omitempty"`
	ClientSeq             uint64  `json:"client_seq"`
	Foreground            bool    `json:"foreground"`
	PlaybackRate          float32 `json:"playback_rate"`
	ClientDiscontinuity   bool    `json:"client_detected_discontinuity"`
}

type feedbackRequest struct {
	ImpressionID      string `json:"impression_id"`
	RequestID         string `json:"request_id"`
	UserID            int64  `json:"user_id"`
	ItemID            int64  `json:"item_id"`
	Position          uint32 `json:"position"`
	Action            string `json:"action"`
	EventTimeMs       int64  `json:"event_time_ms"`
	DwellMs           *int64 `json:"dwell_ms,omitempty"`
	OrderValueCent    *int64 `json:"order_value_cent,omitempty"`
	ExperimentID      string `json:"experiment_id"`
	ExperimentVariant string `json:"experiment_variant"`
	RecModelName      string `json:"rec_model_name"`
	RecModelVersion   string `json:"rec_model_version"`
}

func main() {
	prometheus.MustRegister(requestCount, requestLatency, feedbackCount)
	ctx := context.Background()

	brokers := splitCSV(env("KAFKA_BROKERS", "kafka:9092"))
	writer := &kafka.Writer{
		Addr:                   kafka.TCP(brokers...),
		RequiredAcks:           kafka.RequireAll,
		AllowAutoTopicCreation: false,
		Balancer:               &kafka.Hash{},
		Async:                  false,
	}

	rdb, err := redisFromURL(env("REDIS_URL", "redis://redis:6379/0"))
	if err != nil {
		log.Fatal(err)
	}

	db, err := pgxpool.New(ctx, env("POSTGRES_DSN", "postgresql://live2shop:live2shop@postgres:5432/live2shop"))
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()

	a := &app{kafka: writer, redis: rdb, db: db}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", a.health)
	mux.HandleFunc("POST /api/v1/events/behavior", a.behavior)
	mux.HandleFunc("POST /api/v1/events/feedback", a.feedback)
	mux.HandleFunc("GET /api/v1/live/{room_id}/timeline", a.timeline)
	mux.HandleFunc("GET /api/v1/debug/live/{room_id}/state", a.debugLiveState)
	mux.HandleFunc("GET /api/v1/debug/users/{user_id}/interest", a.debugInterest)
	mux.Handle("/metrics", promhttp.Handler())

	addr := env("HTTP_ADDR", ":8088")
	log.Printf("event-gateway listening on %s", addr)
	if err := http.ListenAndServe(addr, withMiddleware(mux)); err != nil {
		log.Fatal(err)
	}
}

func (a *app) health(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), time.Second)
	defer cancel()
	if err := a.redis.Ping(ctx).Err(); err != nil {
		writeJSON(w, 503, map[string]any{"ok": false, "redis": err.Error()})
		return
	}
	if err := a.db.Ping(ctx); err != nil {
		writeJSON(w, 503, map[string]any{"ok": false, "postgres": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true})
}

func (a *app) timeline(w http.ResponseWriter, r *http.Request) {
	roomID := r.PathValue("room_id")
	raw, err := a.redis.Get(r.Context(), "live:timeline:"+roomID).Result()
	if errors.Is(err, redis.Nil) {
		writeJSON(w, 404, map[string]any{"error": "no active stream timeline"})
		return
	}
	if err != nil {
		writeJSON(w, 503, map[string]any{"error": err.Error()})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(200)
	_, _ = w.Write([]byte(raw))
}

func (a *app) debugLiveState(w http.ResponseWriter, r *http.Request) {
	roomID := r.PathValue("room_id")
	raw, err := a.redis.Get(r.Context(), "live:content:"+roomID).Result()
	if errors.Is(err, redis.Nil) {
		writeJSON(w, 404, map[string]any{"error": "content state unavailable"})
		return
	}
	if err != nil {
		writeJSON(w, 503, map[string]any{"error": err.Error()})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(raw))
}

func (a *app) debugInterest(w http.ResponseWriter, r *http.Request) {
	userID := r.PathValue("user_id")
	raw, err := a.redis.Get(r.Context(), "user:interest:"+userID).Result()
	if errors.Is(err, redis.Nil) {
		writeJSON(w, 404, map[string]any{"error": "interest state unavailable"})
		return
	}
	if err != nil {
		writeJSON(w, 503, map[string]any{"error": err.Error()})
		return
	}
	w.Header().Set("Content-Type", "application/json")
	_, _ = w.Write([]byte(raw))
}

func (a *app) behavior(w http.ResponseWriter, r *http.Request) {
	var req behaviorRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]any{"error": "invalid json: " + err.Error()})
		return
	}
	if err := validateBehavior(req); err != nil {
		writeJSON(w, 422, map[string]any{"error": err.Error()})
		return
	}

	now := time.Now().UnixMilli()
	if req.EventTimeMs == 0 {
		req.EventTimeMs = now
	}
	if req.PlaybackRate == 0 {
		req.PlaybackRate = 1.0
	}

	eventID := newUUIDv7()
	traceID := traceIDFromRequest(r)
	action, err := parseUserAction(req.Action)
	if err != nil {
		writeJSON(w, 422, map[string]any{"error": err.Error()})
		return
	}

	msg := &pb.UserBehaviorEvent{
		Meta: &pb.EventMeta{
			EventId: eventID, SchemaVersion: 1,
			EventTimeMs: req.EventTimeMs, IngestTimeMs: now,
			TraceId: traceID, Producer: "event-gateway",
		},
		UserId: req.UserID, SessionId: req.SessionID, RoomId: req.RoomID,
		StreamSessionId: req.StreamSessionID, Action: action,
		PlayheadPtsMs:               req.PlayheadPtsMs,
		PreviousPlayheadPtsMs:       req.PreviousPlayheadPtsMs,
		EffectiveWatchMs:            req.EffectiveWatchMs,
		ItemId:                      req.ItemID,
		ClientSeq:                   req.ClientSeq,
		Foreground:                  req.Foreground,
		PlaybackRate:                req.PlaybackRate,
		ClientDetectedDiscontinuity: req.ClientDiscontinuity,
	}

	value, err := proto.Marshal(msg)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	err = a.kafka.WriteMessages(r.Context(), kafka.Message{
		Topic:   behaviorTopic,
		Key:     []byte(strconv.FormatInt(req.UserID, 10)),
		Value:   value,
		Headers: []kafka.Header{{Key: "event_id", Value: []byte(eventID)}},
		Time:    time.UnixMilli(req.EventTimeMs),
	})
	if err != nil {
		writeJSON(w, 503, map[string]any{"error": "kafka write failed: " + err.Error()})
		return
	}

	// Operational session summary. Raw event in Kafka/MinIO remains the source of truth.
	_ = a.persistSessionEvent(r.Context(), req, action)

	writeJSON(w, 202, map[string]any{
		"accepted": true,
		"event_id": eventID,
		"trace_id": traceID,
	})
}

func (a *app) feedback(w http.ResponseWriter, r *http.Request) {
	var req feedbackRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]any{"error": "invalid json: " + err.Error()})
		return
	}
	if req.ImpressionID == "" || req.RequestID == "" || req.UserID == 0 || req.ItemID == 0 {
		writeJSON(w, 422, map[string]any{"error": "impression_id, request_id, user_id and item_id are required"})
		return
	}
	action, err := parseFeedbackAction(req.Action)
	if err != nil {
		writeJSON(w, 422, map[string]any{"error": err.Error()})
		return
	}

	now := time.Now().UnixMilli()
	if req.EventTimeMs == 0 {
		req.EventTimeMs = now
	}
	eventID, traceID := newUUIDv7(), traceIDFromRequest(r)

	msg := &pb.RecommendationFeedbackEvent{
		Meta: &pb.EventMeta{
			EventId: eventID, SchemaVersion: 1,
			EventTimeMs: req.EventTimeMs, IngestTimeMs: now,
			TraceId: traceID, Producer: "event-gateway",
		},
		ImpressionId:   req.ImpressionID,
		RequestId:      req.RequestID,
		UserId:         req.UserID,
		ItemId:         req.ItemID,
		Position:       req.Position,
		Action:         action,
		DwellMs:        req.DwellMs,
		OrderValueCent: req.OrderValueCent,
		Experiment: &pb.ExperimentContext{
			ExperimentId: req.ExperimentID,
			Variant:      req.ExperimentVariant,
		},
		RecModel: &pb.ModelRef{
			Name:    req.RecModelName,
			Version: req.RecModelVersion,
		},
	}

	value, err := proto.Marshal(msg)
	if err != nil {
		writeJSON(w, 500, map[string]any{"error": err.Error()})
		return
	}
	if err := a.kafka.WriteMessages(r.Context(), kafka.Message{
		Topic:   feedbackTopic,
		Key:     []byte(strconv.FormatInt(req.UserID, 10)),
		Value:   value,
		Headers: []kafka.Header{{Key: "event_id", Value: []byte(eventID)}},
		Time:    time.UnixMilli(req.EventTimeMs),
	}); err != nil {
		writeJSON(w, 503, map[string]any{"error": "kafka write failed: " + err.Error()})
		return
	}

	// DB audit is best-effort; Kafka is the durable raw event path.
	_, _ = a.db.Exec(r.Context(), `
        INSERT INTO recommendation_feedback (
            feedback_event_id, impression_id, request_id, user_id, item_id,
            position, action, dwell_ms, order_value_cent,
            experiment_id, experiment_variant,
            rec_model_name, rec_model_version, event_time
        ) VALUES (
            $1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9,
            NULLIF($10,''), NULLIF($11,''), $12, $13, to_timestamp($14/1000.0)
        ) ON CONFLICT (feedback_event_id) DO NOTHING`,
		eventID, req.ImpressionID, req.RequestID, req.UserID, req.ItemID,
		req.Position, strings.ToUpper(req.Action), req.DwellMs, req.OrderValueCent,
		req.ExperimentID, req.ExperimentVariant, req.RecModelName, req.RecModelVersion,
		req.EventTimeMs,
	)

	feedbackCount.WithLabelValues(strings.ToUpper(req.Action), req.ExperimentID, req.ExperimentVariant).Inc()
	writeJSON(w, 202, map[string]any{"accepted": true, "event_id": eventID})
}

func (a *app) persistSessionEvent(ctx context.Context, req behaviorRequest, action pb.UserAction) error {
	switch action {
	case pb.UserAction_ENTER_ROOM:
		_, err := a.db.Exec(ctx, `
            INSERT INTO user_sessions (
              session_id, user_id, room_id, enter_at, status
            ) VALUES ($1::uuid, $2, $3, to_timestamp($4/1000.0), 'ACTIVE')
            ON CONFLICT (session_id) DO NOTHING`,
			req.SessionID, req.UserID, req.RoomID, req.EventTimeMs)
		return err
	case pb.UserAction_HEARTBEAT:
		delta := int64(0)
		if req.EffectiveWatchMs != nil {
			delta = *req.EffectiveWatchMs
		}
		_, err := a.db.Exec(ctx, `
            UPDATE user_sessions
               SET effective_watch_ms = effective_watch_ms + GREATEST($2,0),
                   updated_at = now()
             WHERE session_id = $1::uuid`,
			req.SessionID, delta)
		return err
	case pb.UserAction_LIKE:
		_, err := a.db.Exec(ctx, `UPDATE user_sessions SET like_count=like_count+1, updated_at=now() WHERE session_id=$1::uuid`, req.SessionID)
		return err
	case pb.UserAction_FOLLOW:
		_, err := a.db.Exec(ctx, `UPDATE user_sessions SET follow_count=follow_count+1, updated_at=now() WHERE session_id=$1::uuid`, req.SessionID)
		return err
	case pb.UserAction_ITEM_CLICK:
		_, err := a.db.Exec(ctx, `UPDATE user_sessions SET item_click_count=item_click_count+1, updated_at=now() WHERE session_id=$1::uuid`, req.SessionID)
		return err
	case pb.UserAction_EXIT_ROOM:
		_, err := a.db.Exec(ctx, `
            UPDATE user_sessions
               SET exit_at=to_timestamp($2/1000.0), status='CLOSED', updated_at=now()
             WHERE session_id=$1::uuid`,
			req.SessionID, req.EventTimeMs)
		return err
	default:
		return nil
	}
}

func validateBehavior(req behaviorRequest) error {
	if req.UserID <= 0 || req.RoomID <= 0 {
		return errors.New("user_id and room_id must be positive")
	}
	if _, err := uuid.Parse(req.SessionID); err != nil {
		return errors.New("session_id must be a UUID")
	}
	if _, err := uuid.Parse(req.StreamSessionID); err != nil {
		return errors.New("stream_session_id must be a UUID")
	}
	if req.ClientSeq == 0 {
		return errors.New("client_seq must start at 1")
	}
	return nil
}

func parseUserAction(s string) (pb.UserAction, error) {
	switch strings.ToUpper(s) {
	case "ENTER_ROOM":
		return pb.UserAction_ENTER_ROOM, nil
	case "HEARTBEAT":
		return pb.UserAction_HEARTBEAT, nil
	case "LIKE":
		return pb.UserAction_LIKE, nil
	case "FOLLOW":
		return pb.UserAction_FOLLOW, nil
	case "ITEM_CLICK":
		return pb.UserAction_ITEM_CLICK, nil
	case "EXIT_ROOM":
		return pb.UserAction_EXIT_ROOM, nil
	default:
		return pb.UserAction_USER_ACTION_UNSPECIFIED, fmt.Errorf("unsupported action %q", s)
	}
}

func parseFeedbackAction(s string) (pb.FeedbackAction, error) {
	switch strings.ToUpper(s) {
	case "IMPRESSION":
		return pb.FeedbackAction_IMPRESSION, nil
	case "CLICK":
		return pb.FeedbackAction_CLICK, nil
	case "LONG_VIEW":
		return pb.FeedbackAction_LONG_VIEW, nil
	case "FAVORITE":
		return pb.FeedbackAction_FAVORITE, nil
	case "ADD_CART":
		return pb.FeedbackAction_ADD_CART, nil
	case "PURCHASE":
		return pb.FeedbackAction_PURCHASE, nil
	case "DISMISS":
		return pb.FeedbackAction_DISMISS, nil
	default:
		return pb.FeedbackAction_FEEDBACK_UNSPECIFIED, fmt.Errorf("unsupported feedback action %q", s)
	}
}

func redisFromURL(raw string) (*redis.Client, error) {
	opt, err := redis.ParseURL(raw)
	if err != nil {
		return nil, err
	}
	return redis.NewClient(opt), nil
}

func newUUIDv7() string {
	id, err := uuid.NewV7()
	if err != nil {
		return uuid.NewString()
	}
	return id.String()
}

func traceIDFromRequest(r *http.Request) string {
	if v := r.Header.Get("X-Trace-Id"); v != "" {
		return v
	}
	return newUUIDv7()
}

func splitCSV(v string) []string {
	out := []string{}
	for _, s := range strings.Split(v, ",") {
		s = strings.TrimSpace(s)
		if s != "" {
			out = append(out, s)
		}
	}
	return out
}

func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func withMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type,X-Trace-Id")
		w.Header().Set("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
		if r.Method == http.MethodOptions {
			w.WriteHeader(http.StatusNoContent)
			return
		}
		sw := &statusWriter{ResponseWriter: w, status: 200}
		next.ServeHTTP(sw, r)
		requestCount.WithLabelValues(r.URL.Path, strconv.Itoa(sw.status)).Inc()
		requestLatency.WithLabelValues(r.URL.Path).Observe(time.Since(start).Seconds())
	})
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(code int) {
	w.status = code
	w.ResponseWriter.WriteHeader(code)
}
