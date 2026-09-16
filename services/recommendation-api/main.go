package main

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promhttp"
	"github.com/redis/go-redis/v9"
	"github.com/segmentio/kafka-go"
	"google.golang.org/protobuf/proto"

	pb "github.com/live2shop/recommendation-api/gen/live2shop/v1"
)

type app struct {
	db     *pgxpool.Pool
	redis  *redis.Client
	kafka  *kafka.Writer
	recURL string
	client *http.Client
}

type recommendationRequest struct {
	RequestID            string `json:"request_id,omitempty"`
	UserID               int64  `json:"user_id"`
	SessionID            string `json:"session_id,omitempty"`
	Scene                string `json:"scene"`
	Trigger              string `json:"trigger"`
	ContextRoomID        *int64 `json:"context_room_id,omitempty"`
	MinSourceEventTimeMs *int64 `json:"min_source_event_time_ms,omitempty"`
	Limit                int    `json:"limit"`
}

type interestState struct {
	UserID            int64            `json:"user_id"`
	Version           uint64           `json:"version"`
	FusedEmbedding    []float32        `json:"fused_embedding"`
	TopInterests      []map[string]any `json:"top_interests"`
	ModelName         string           `json:"model_name"`
	ModelVersion      string           `json:"model_version"`
	EmbeddingVersion  string           `json:"embedding_version"`
	SourceWatermarkMs int64            `json:"source_watermark_ms"`
}

type recallHit struct {
	ItemID         int64
	RetrievalScore float64
	Sources        map[string]bool
}

type item struct {
	ItemID       int64    `json:"item_id"`
	Title        string   `json:"title"`
	CategoryID   *int64   `json:"category_id,omitempty"`
	CategoryPath []string `json:"category_path"`
	Brand        *string  `json:"brand,omitempty"`
	StyleTags    []string `json:"style_tags"`
	PriceCent    int64    `json:"price_cent"`
	Inventory    int64    `json:"inventory"`
	ImageURI     *string  `json:"image_uri,omitempty"`
	Popularity   float64  `json:"popularity_score"`
	Status       string   `json:"status"`
}

type scoredItem struct {
	Item       item
	Hit        recallHit
	RankScore  float64
	FinalScore float64
	Reasons    []string
}

var (
	requestCount = prometheus.NewCounterVec(
		prometheus.CounterOpts{Name: "http_requests_total", Help: "HTTP requests", ConstLabels: prometheus.Labels{"service": "recommendation-api"}},
		[]string{"path", "status"},
	)
	requestLatency = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{Name: "http_request_duration_seconds", Help: "Request latency", ConstLabels: prometheus.Labels{"service": "recommendation-api"}},
		[]string{"path"},
	)
	recallSize = prometheus.NewHistogramVec(
		prometheus.HistogramOpts{Name: "recommendation_recall_size", Help: "Recall candidates"},
		[]string{"source"},
	)
)

func main() {
	prometheus.MustRegister(requestCount, requestLatency, recallSize)
	ctx := context.Background()

	db, err := pgxpool.New(ctx, env("POSTGRES_DSN", "postgresql://live2shop:live2shop@postgres:5432/live2shop"))
	if err != nil {
		log.Fatal(err)
	}
	rdb, err := redisFromURL(env("REDIS_URL", "redis://redis:6379/0"))
	if err != nil {
		log.Fatal(err)
	}

	brokers := splitCSV(env("KAFKA_BROKERS", "kafka:9092"))
	writer := &kafka.Writer{
		Addr:                   kafka.TCP(brokers...),
		RequiredAcks:           kafka.RequireAll,
		Balancer:               &kafka.Hash{},
		AllowAutoTopicCreation: false,
	}

	a := &app{
		db: db, redis: rdb, kafka: writer,
		recURL: strings.TrimRight(env("REC_MODEL_SERVER", "http://rec-model-server:8092"), "/"),
		client: &http.Client{Timeout: 1500 * time.Millisecond},
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", a.health)
	mux.HandleFunc("POST /api/v1/recommendations", a.recommend)
	mux.Handle("/metrics", promhttp.Handler())

	addr := env("HTTP_ADDR", ":8090")
	log.Printf("recommendation-api listening on %s", addr)
	log.Fatal(http.ListenAndServe(addr, withMiddleware(mux)))
}

func (a *app) health(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), time.Second)
	defer cancel()
	if err := a.db.Ping(ctx); err != nil {
		writeJSON(w, 503, map[string]any{"ok": false, "postgres": err.Error()})
		return
	}
	if err := a.redis.Ping(ctx).Err(); err != nil {
		writeJSON(w, 503, map[string]any{"ok": false, "redis": err.Error()})
		return
	}
	writeJSON(w, 200, map[string]any{"ok": true})
}

func (a *app) recommend(w http.ResponseWriter, r *http.Request) {
	start := time.Now()
	var req recommendationRequest
	if err := json.NewDecoder(http.MaxBytesReader(w, r.Body, 1<<20)).Decode(&req); err != nil {
		writeJSON(w, 400, map[string]any{"error": "invalid json"})
		return
	}
	if req.UserID <= 0 {
		writeJSON(w, 422, map[string]any{"error": "user_id is required"})
		return
	}
	if req.Limit <= 0 || req.Limit > 100 {
		req.Limit = 20
	}
	if req.RequestID == "" {
		req.RequestID = newUUID()
	}
	if req.Scene == "" {
		req.Scene = "POST_LIVE"
	}
	if req.Trigger == "" {
		req.Trigger = "EXIT_ROOM"
	}

	variant := experimentVariant(req.UserID)
	experimentID := "live2shop-ranking-v1"

	interest, cold, err := a.loadInterestFresh(r.Context(), req.UserID, req.MinSourceEventTimeMs, 500*time.Millisecond)
	if err != nil {
		writeJSON(w, 503, map[string]any{"error": "interest read failed: " + err.Error()})
		return
	}
	interestFresh := req.MinSourceEventTimeMs == nil || cold || interest.SourceWatermarkMs >= *req.MinSourceEventTimeMs

	merged := map[int64]*recallHit{}

	if !cold && interest.EmbeddingVersion == "commerce-space-v1" && len(interest.FusedEmbedding) == 128 {
		n := 300
		if variant == "B" {
			n = 350
		}
		semantic, err := a.semanticRecall(r.Context(), interest.FusedEmbedding, n)
		if err != nil {
			log.Printf("semantic recall degraded: %v", err)
		} else {
			recallSize.WithLabelValues("semantic").Observe(float64(len(semantic)))
			for _, h := range semantic {
				mergeHit(merged, h)
			}
		}
	}

	tags := topTags(interest)
	category, err := a.categoryRecall(r.Context(), tags, 100)
	if err != nil {
		log.Printf("category recall: %v", err)
	}
	recallSize.WithLabelValues("category").Observe(float64(len(category)))
	for _, h := range category {
		mergeHit(merged, h)
	}

	hot, err := a.hotRecall(r.Context(), 100)
	if err != nil {
		log.Printf("hot recall: %v", err)
	}
	recallSize.WithLabelValues("hot").Observe(float64(len(hot)))
	for _, h := range hot {
		mergeHit(merged, h)
	}

	ids := make([]int64, 0, len(merged))
	for id := range merged {
		ids = append(ids, id)
	}
	items, err := a.fetchItems(r.Context(), ids)
	if err != nil {
		writeJSON(w, 503, map[string]any{"error": "catalog read failed: " + err.Error()})
		return
	}

	candidates := make([]scoredItem, 0, len(items))
	rankIDs := make([]int64, 0, len(items))
	rankFeatures := make([][]float64, 0, len(items))
	for _, it := range items {
		if it.Status != "ACTIVE" || it.Inventory <= 0 {
			continue
		}
		h := *merged[it.ItemID]
		categoryMatch := tagMatch(tags, it.StyleTags)
		_ = categoryMatch
		// R0 trains ranking on user/item representation. External business
		// features stay zero in v1 until enough Live2Shop feedback exists to
		// fit them without domain shift. Inventory/diversity are handled by
		// deterministic re-ranking below.
		features := []float64{0, 0, 0, 0}
		rankIDs = append(rankIDs, it.ItemID)
		rankFeatures = append(rankFeatures, features)
		candidates = append(candidates, scoredItem{Item: it, Hit: h})
	}

	rankMap := map[int64]float64{}
	if !cold && interest.EmbeddingVersion == "commerce-space-v1" && len(interest.FusedEmbedding) == 128 && len(rankIDs) > 0 {
		rm, err := a.rank(r.Context(), interest.FusedEmbedding, rankIDs, rankFeatures)
		if err != nil {
			log.Printf("ranker degraded: %v", err)
		} else {
			rankMap = rm
		}
	}

	for i := range candidates {
		rs, ok := rankMap[candidates[i].Item.ItemID]
		if !ok {
			// Deterministic fallback uses recall + popularity, not random ranking.
			rs = 0.7*normalizeCos(candidates[i].Hit.RetrievalScore) + 0.3*math.Tanh(candidates[i].Item.Popularity/10)
		}
		candidates[i].RankScore = rs
		candidates[i].FinalScore = 0.90*rs + 0.10*normalizeCos(candidates[i].Hit.RetrievalScore)
		candidates[i].Reasons = reasonCodes(candidates[i], tags)
	}
	sort.Slice(candidates, func(i, j int) bool { return candidates[i].FinalScore > candidates[j].FinalScore })

	final := diversityRerank(candidates, req.Limit, 5)
	latencyMs := time.Since(start).Milliseconds()

	if err := a.persistAndPublish(r.Context(), req, interest, experimentID, variant, final, latencyMs); err != nil {
		writeJSON(w, 503, map[string]any{"error": "audit log failed: " + err.Error()})
		return
	}

	outItems := make([]map[string]any, 0, len(final))
	for idx, s := range final {
		outItems = append(outItems, map[string]any{
			"impression_id": s.Reasons[len(s.Reasons)-1], // internal marker replaced below
			"item_id":       s.Item.ItemID,
			"title":         s.Item.Title,
			"category_path": s.Item.CategoryPath,
			"brand":         s.Item.Brand,
			"style_tags":    s.Item.StyleTags,
			"price_cent":    s.Item.PriceCent,
			"image_uri":     s.Item.ImageURI,
			"rank":          idx + 1,
			"score":         s.FinalScore,
			"reason_codes":  s.Reasons[:len(s.Reasons)-1],
		})
	}

	writeJSON(w, 200, map[string]any{
		"request_id":                   req.RequestID,
		"interest_version":             interest.Version,
		"cold_start":                   cold,
		"interest_fresh":               interestFresh,
		"interest_source_watermark_ms": interest.SourceWatermarkMs,
		"experiment_id":                experimentID,
		"experiment_variant":           variant,
		"rec_model_name":               "rec-model",
		"rec_model_version":            interest.ModelVersion,
		"latency_ms":                   latencyMs,
		"items":                        outItems,
	})
}

func (a *app) loadInterest(ctx context.Context, userID int64) (interestState, bool, error) {
	raw, err := a.redis.Get(ctx, fmt.Sprintf("user:interest:%d", userID)).Result()
	if errors.Is(err, redis.Nil) {
		return interestState{}, true, nil
	}
	if err != nil {
		return interestState{}, false, err
	}
	var s interestState
	if err := json.Unmarshal([]byte(raw), &s); err != nil {
		return s, false, err
	}
	return s, false, nil
}

func (a *app) loadInterestFresh(
	ctx context.Context,
	userID int64,
	minSource *int64,
	maxWait time.Duration,
) (interestState, bool, error) {
	deadline := time.Now().Add(maxWait)
	for {
		state, cold, err := a.loadInterest(ctx, userID)
		if err != nil || cold || minSource == nil || state.SourceWatermarkMs >= *minSource {
			return state, cold, err
		}
		if time.Now().After(deadline) {
			return state, cold, nil
		}
		select {
		case <-ctx.Done():
			return state, cold, ctx.Err()
		case <-time.After(25 * time.Millisecond):
		}
	}
}

func (a *app) semanticRecall(ctx context.Context, emb []float32, topK int) ([]recallHit, error) {
	body, _ := json.Marshal(map[string]any{"embedding": emb, "top_k": topK})
	req, _ := http.NewRequestWithContext(ctx, "POST", a.recURL+"/v1/semantic-recall", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	resp, err := a.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		b, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("semantic %s %s", resp.Status, string(b))
	}
	var x struct {
		Items []struct {
			ItemID int64   `json:"item_id"`
			Score  float64 `json:"score"`
		} `json:"items"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&x); err != nil {
		return nil, err
	}
	out := make([]recallHit, 0, len(x.Items))
	for _, v := range x.Items {
		out = append(out, recallHit{ItemID: v.ItemID, RetrievalScore: v.Score, Sources: map[string]bool{"SEMANTIC_ANN": true}})
	}
	return out, nil
}

func (a *app) categoryRecall(ctx context.Context, tags []string, limit int) ([]recallHit, error) {
	if len(tags) == 0 {
		return nil, nil
	}
	rows, err := a.db.Query(ctx, `
        SELECT item_id
          FROM items
         WHERE status='ACTIVE' AND inventory>0
           AND style_tags && $1::text[]
         ORDER BY popularity_score DESC
         LIMIT $2`, tags, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []recallHit{}
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		out = append(out, recallHit{ItemID: id, Sources: map[string]bool{"CATEGORY": true}})
	}
	return out, rows.Err()
}

func (a *app) hotRecall(ctx context.Context, limit int) ([]recallHit, error) {
	rows, err := a.db.Query(ctx, `
      SELECT item_id FROM items
       WHERE status='ACTIVE' AND inventory>0
       ORDER BY popularity_score DESC, item_id
       LIMIT $1`, limit)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []recallHit{}
	for rows.Next() {
		var id int64
		if err := rows.Scan(&id); err != nil {
			return nil, err
		}
		out = append(out, recallHit{ItemID: id, Sources: map[string]bool{"POPULAR": true}})
	}
	return out, rows.Err()
}

func (a *app) fetchItems(ctx context.Context, ids []int64) ([]item, error) {
	if len(ids) == 0 {
		return nil, nil
	}
	rows, err := a.db.Query(ctx, `
      SELECT item_id,title,category_id,category_path,brand,style_tags,price_cent,
             inventory,image_uri,popularity_score,status
        FROM items WHERE item_id = ANY($1::bigint[])`, ids)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := []item{}
	for rows.Next() {
		var x item
		if err := rows.Scan(&x.ItemID, &x.Title, &x.CategoryID, &x.CategoryPath, &x.Brand, &x.StyleTags, &x.PriceCent, &x.Inventory, &x.ImageURI, &x.Popularity, &x.Status); err != nil {
			return nil, err
		}
		out = append(out, x)
	}
	return out, rows.Err()
}

func (a *app) rank(ctx context.Context, user []float32, ids []int64, features [][]float64) (map[int64]float64, error) {
	body, _ := json.Marshal(map[string]any{"user_embedding": user, "item_ids": ids, "business_features": features})
	req, _ := http.NewRequestWithContext(ctx, "POST", a.recURL+"/v1/rank", bytes.NewReader(body))
	req.Header.Set("Content-Type", "application/json")
	resp, err := a.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode/100 != 2 {
		return nil, fmt.Errorf("rank returned %s", resp.Status)
	}
	var x struct {
		Items []struct {
			ItemID int64   `json:"item_id"`
			Score  float64 `json:"score"`
		} `json:"items"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&x); err != nil {
		return nil, err
	}
	m := map[int64]float64{}
	for _, v := range x.Items {
		m[v.ItemID] = v.Score
	}
	return m, nil
}

func (a *app) persistAndPublish(
	ctx context.Context, req recommendationRequest, interest interestState,
	experimentID, variant string, items []scoredItem, latencyMs int64,
) error {
	tx, err := a.db.Begin(ctx)
	if err != nil {
		return err
	}
	defer func() { _ = tx.Rollback(ctx) }()

	modelVersion := interest.ModelVersion
	if modelVersion == "" {
		modelVersion = "cold-start"
	}
	_, err = tx.Exec(ctx, `
      INSERT INTO recommendation_requests(
        request_id,user_id,session_id,scene,trigger_type,context_room_id,
        interest_version,experiment_id,experiment_variant,
        rec_model_name,rec_model_version,rec_model_alias,latency_ms
      ) VALUES($1::uuid,$2,NULLIF($3,'')::uuid,$4,$5,$6,$7,$8,$9,'rec-model',$10,'champion',$11)`,
		req.RequestID, req.UserID, req.SessionID, strings.ToUpper(req.Scene), strings.ToUpper(req.Trigger),
		req.ContextRoomID, interest.Version, experimentID, variant, modelVersion, latencyMs)
	if err != nil {
		return err
	}

	pbItems := make([]*pb.RecommendedItem, 0, len(items))
	for i := range items {
		imp := newUUID()
		// Store impression id as a temporary internal tail marker used by the response builder.
		items[i].Reasons = append(items[i].Reasons, imp)
		srcNames := sourceNames(items[i].Hit.Sources)
		_, err = tx.Exec(ctx, `
          INSERT INTO recommendation_impressions(
            impression_id,request_id,item_id,position,recall_sources,
            retrieval_score,rank_score,final_score,reason_codes
          ) VALUES($1::uuid,$2::uuid,$3,$4,$5,$6,$7,$8,$9)`,
			imp, req.RequestID, items[i].Item.ItemID, i+1, srcNames,
			items[i].Hit.RetrievalScore, items[i].RankScore, items[i].FinalScore,
			items[i].Reasons[:len(items[i].Reasons)-1])
		if err != nil {
			return err
		}
		pbSources := []pb.RecallSource{}
		for _, s := range srcNames {
			switch s {
			case "SEMANTIC_ANN":
				pbSources = append(pbSources, pb.RecallSource_SEMANTIC_ANN)
			case "CATEGORY":
				pbSources = append(pbSources, pb.RecallSource_CATEGORY)
			case "POPULAR":
				pbSources = append(pbSources, pb.RecallSource_POPULAR)
			}
		}
		pbItems = append(pbItems, &pb.RecommendedItem{
			ImpressionId: imp, ItemId: items[i].Item.ItemID, Rank: uint32(i + 1),
			RecallSources: pbSources, RetrievalScore: float32(items[i].Hit.RetrievalScore),
			RankScore: float32(items[i].RankScore), FinalScore: float32(items[i].FinalScore),
			ReasonCodes: items[i].Reasons[:len(items[i].Reasons)-1],
		})
	}
	if err := tx.Commit(ctx); err != nil {
		return err
	}

	now := time.Now().UnixMilli()
	served := &pb.RecommendationServedEvent{
		Meta:      &pb.EventMeta{EventId: newUUID(), SchemaVersion: 1, EventTimeMs: now, IngestTimeMs: now, TraceId: newUUID(), Producer: "recommendation-api"},
		RequestId: req.RequestID, UserId: req.UserID, Scene: parseScene(req.Scene), Trigger: parseTrigger(req.Trigger),
		InterestVersion: interest.Version, Items: pbItems,
		RecModel:   &pb.ModelRef{Name: "rec-model", Version: modelVersion, Alias: "champion"},
		Experiment: &pb.ExperimentContext{ExperimentId: experimentID, Variant: variant},
		LatencyMs:  latencyMs,
	}
	if req.SessionID != "" {
		served.SessionId = &req.SessionID
	}
	raw, err := proto.Marshal(served)
	if err != nil {
		return err
	}
	return a.kafka.WriteMessages(ctx, kafka.Message{
		Topic: "live2shop.rec.served.v1",
		Key:   []byte(strconv.FormatInt(req.UserID, 10)),
		Value: raw, Time: time.Now(),
	})
}

func mergeHit(m map[int64]*recallHit, h recallHit) {
	if x, ok := m[h.ItemID]; ok {
		if h.RetrievalScore > x.RetrievalScore {
			x.RetrievalScore = h.RetrievalScore
		}
		for s := range h.Sources {
			x.Sources[s] = true
		}
		return
	}
	copy := h
	if copy.Sources == nil {
		copy.Sources = map[string]bool{}
	}
	m[h.ItemID] = &copy
}
func topTags(s interestState) []string {
	out := []string{}
	for _, x := range s.TopInterests {
		if v, ok := x["tag"].(string); ok && v != "" {
			out = append(out, v)
		}
		if len(out) >= 5 {
			break
		}
	}
	return out
}
func tagMatch(tags, itemTags []string) float64 {
	set := map[string]bool{}
	for _, t := range tags {
		set[strings.ToLower(t)] = true
	}
	for _, t := range itemTags {
		if set[strings.ToLower(t)] {
			return 1
		}
	}
	return 0
}
func reasonCodes(s scoredItem, tags []string) []string {
	out := []string{}
	if s.Hit.Sources["SEMANTIC_ANN"] {
		out = append(out, "semantic_interest_match")
	}
	if tagMatch(tags, s.Item.StyleTags) > 0 {
		out = append(out, "style_interest_match")
	}
	if s.Hit.Sources["POPULAR"] {
		out = append(out, "popular_fallback")
	}
	return out
}
func diversityRerank(xs []scoredItem, limit, maxPerCategory int) []scoredItem {
	out := []scoredItem{}
	counts := map[string]int{}
	for _, x := range xs {
		cat := "unknown"
		if len(x.Item.CategoryPath) > 0 {
			cat = x.Item.CategoryPath[0]
		}
		if counts[cat] >= maxPerCategory {
			continue
		}
		out = append(out, x)
		counts[cat]++
		if len(out) >= limit {
			break
		}
	}
	return out
}
func sourceNames(m map[string]bool) []string {
	out := []string{}
	for _, k := range []string{"SEMANTIC_ANN", "CATEGORY", "POPULAR"} {
		if m[k] {
			out = append(out, k)
		}
	}
	return out
}
func experimentVariant(userID int64) string {
	b := make([]byte, 8)
	binary.LittleEndian.PutUint64(b, uint64(userID))
	h := sha256.Sum256(b)
	if binary.LittleEndian.Uint64(h[:8])%100 < 50 {
		return "A"
	}
	return "B"
}
func normalizeCos(x float64) float64 { return clamp((x+1)/2, 0, 1) }
func clamp(x, a, b float64) float64 {
	if x < a {
		return a
	}
	if x > b {
		return b
	}
	return x
}
func parseScene(s string) pb.RecommendationScene {
	switch strings.ToUpper(s) {
	case "HOME_FEED":
		return pb.RecommendationScene_HOME_FEED
	case "RELATED_ITEMS":
		return pb.RecommendationScene_RELATED_ITEMS
	default:
		return pb.RecommendationScene_POST_LIVE
	}
}
func parseTrigger(s string) pb.RecommendationTrigger {
	switch strings.ToUpper(s) {
	case "MANUAL_REFRESH":
		return pb.RecommendationTrigger_MANUAL_REFRESH
	case "PAGE_OPEN":
		return pb.RecommendationTrigger_PAGE_OPEN
	default:
		return pb.RecommendationTrigger_EXIT_ROOM
	}
}
func newUUID() string {
	id, e := uuid.NewV7()
	if e != nil {
		return uuid.NewString()
	}
	return id.String()
}
func env(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
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
func redisFromURL(raw string) (*redis.Client, error) {
	o, e := redis.ParseURL(raw)
	if e != nil {
		return nil, e
	}
	return redis.NewClient(o), nil
}
func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

type statusWriter struct {
	http.ResponseWriter
	status int
}

func (w *statusWriter) WriteHeader(c int) { w.status = c; w.ResponseWriter.WriteHeader(c) }
func withMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		st := time.Now()
		w.Header().Set("Access-Control-Allow-Origin", "*")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		w.Header().Set("Access-Control-Allow-Methods", "POST,GET,OPTIONS")
		if r.Method == "OPTIONS" {
			w.WriteHeader(204)
			return
		}
		sw := &statusWriter{ResponseWriter: w, status: 200}
		next.ServeHTTP(sw, r)
		requestCount.WithLabelValues(r.URL.Path, strconv.Itoa(sw.status)).Inc()
		requestLatency.WithLabelValues(r.URL.Path).Observe(time.Since(st).Seconds())
	})
}

var _ = pgx.ErrNoRows
