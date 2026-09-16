package com.live2shop.stream;

import com.live2shop.proto.v1.*;
import org.apache.flink.api.common.state.ListState;
import org.apache.flink.api.common.state.ListStateDescriptor;
import org.apache.flink.configuration.Configuration;
import org.apache.flink.streaming.api.functions.co.KeyedCoProcessFunction;
import org.apache.flink.util.Collector;

import java.util.*;
import java.util.stream.Collectors;

public class AttributionFunction extends KeyedCoProcessFunction<
        String, UserBehaviorEvent, ContentStateEvent, AttributedBehaviorEvent> {

    private final long waitMs;
    private final long pointStalenessMs;
    private transient ListState<ContentStateEvent> contentState;
    private transient ListState<UserBehaviorEvent> pendingBehavior;

    public AttributionFunction(long waitMs, long pointStalenessMs) {
        this.waitMs = waitMs;
        this.pointStalenessMs = pointStalenessMs;
    }

    @Override
    public void open(Configuration parameters) {
        contentState = getRuntimeContext().getListState(
                new ListStateDescriptor<>("content-states", ContentStateEvent.class));
        pendingBehavior = getRuntimeContext().getListState(
                new ListStateDescriptor<>("pending-behaviors", UserBehaviorEvent.class));
    }

    @Override
    public void processElement1(
            UserBehaviorEvent behavior,
            Context ctx,
            Collector<AttributedBehaviorEvent> out) throws Exception {

        // Fast path: HLS playback usually lags the AI live edge, so the matching
        // ContentState often already exists. Attribute immediately when the PTS
        // interval is resolvable; buffer only if model output is still late.
        if (isResolvable(behavior)) {
            out.collect(attribute(behavior));
        } else {
            pendingBehavior.add(behavior);
            long timer = behavior.getMeta().getEventTimeMs() + waitMs;
            ctx.timerService().registerEventTimeTimer(timer);
        }
    }

    @Override
    public void processElement2(
            ContentStateEvent state,
            Context ctx,
            Collector<AttributedBehaviorEvent> out) throws Exception {

        contentState.add(state);
        compactContentState();

        // Newly arrived AI state may unblock behaviors that arrived first.
        List<UserBehaviorEvent> keep = new ArrayList<>();
        for (UserBehaviorEvent b : pendingBehavior.get()) {
            if (isResolvable(b)) {
                out.collect(attribute(b));
            } else {
                keep.add(b);
            }
        }
        pendingBehavior.update(keep);
    }

    @Override
    public void onTimer(
            long timestamp,
            OnTimerContext ctx,
            Collector<AttributedBehaviorEvent> out) throws Exception {

        List<UserBehaviorEvent> keep = new ArrayList<>();
        for (UserBehaviorEvent b : pendingBehavior.get()) {
            long due = b.getMeta().getEventTimeMs() + waitMs;
            if (due <= timestamp) {
                out.collect(attribute(b));
            } else {
                keep.add(b);
            }
        }
        pendingBehavior.update(keep);
    }

    private AttributedBehaviorEvent attribute(UserBehaviorEvent b) throws Exception {
        List<ContentStateEvent> states = new ArrayList<>();
        for (ContentStateEvent s : contentState.get()) {
            if (s.getStreamSessionId().equals(b.getStreamSessionId())) {
                states.add(s);
            }
        }
        states.sort(Comparator.comparingLong(ContentStateEvent::getStartPtsMs));

        boolean discontinuity = computeDiscontinuity(b);
        List<ContentContribution> contributions;
        AttributionMethod method;

        if (b.getAction() == UserAction.HEARTBEAT
                && b.hasPreviousPlayheadPtsMs()
                && b.hasPlayheadPtsMs()
                && !discontinuity) {
            contributions = intervalAttribution(
                    states,
                    Math.min(b.getPreviousPlayheadPtsMs(), b.getPlayheadPtsMs()),
                    Math.max(b.getPreviousPlayheadPtsMs(), b.getPlayheadPtsMs())
            );
            method = AttributionMethod.INTERVAL_OVERLAP;
        } else {
            long point = b.hasPlayheadPtsMs() ? b.getPlayheadPtsMs() : -1L;
            PointResult p = pointAttribution(states, point);
            contributions = p.contributions;
            method = p.method;
        }

        float confidence = 0.0f;
        if (!contributions.isEmpty()) {
            confidence = (float) contributions.stream()
                    .mapToDouble(ContentContribution::getContentConfidence)
                    .average().orElse(0.0);
            if (method == AttributionMethod.NEAREST_PREVIOUS) {
                confidence *= 0.7f;
            }
            if (discontinuity) {
                confidence *= 0.8f;
            }
        }

        EventMeta meta = EventMeta.newBuilder()
                .setEventId(UUID.randomUUID().toString())
                .setSchemaVersion(1)
                .setEventTimeMs(b.getMeta().getEventTimeMs())
                .setIngestTimeMs(System.currentTimeMillis())
                .setTraceId(
                        b.getMeta().getTraceId().isEmpty()
                                ? UUID.randomUUID().toString()
                                : b.getMeta().getTraceId())
                .setProducer("realtime-feature")
                .build();

        return AttributedBehaviorEvent.newBuilder()
                .setMeta(meta)
                .setSourceEventId(b.getMeta().getEventId())
                .setUserId(b.getUserId())
                .setSessionId(b.getSessionId())
                .setRoomId(b.getRoomId())
                .setStreamSessionId(b.getStreamSessionId())
                .setAction(b.getAction())
                .setMethod(method)
                .addAllContributions(contributions)
                .setAttributionConfidence(confidence)
                .setPlaybackDiscontinuity(discontinuity)
                .build();
    }


    private boolean isResolvable(UserBehaviorEvent b) throws Exception {
        if (!b.hasPlayheadPtsMs()) return false;
        List<ContentStateEvent> states = new ArrayList<>();
        for (ContentStateEvent s : contentState.get()) {
            if (s.getStreamSessionId().equals(b.getStreamSessionId())) states.add(s);
        }
        if (states.isEmpty()) return false;

        if (b.getAction() == UserAction.HEARTBEAT
                && b.hasPreviousPlayheadPtsMs()
                && !computeDiscontinuity(b)) {
            long a = Math.min(b.getPreviousPlayheadPtsMs(), b.getPlayheadPtsMs());
            long z = Math.max(b.getPreviousPlayheadPtsMs(), b.getPlayheadPtsMs());
            if (z <= a) return true;
            long covered = 0;
            for (ContentStateEvent s : states) {
                long lo = Math.max(a, s.getStartPtsMs());
                long hi = Math.min(z, s.getEndPtsMs());
                covered += Math.max(0, hi - lo);
            }
            return covered >= Math.max(1, (long)((z - a) * 0.90));
        }

        long p = b.getPlayheadPtsMs();
        for (ContentStateEvent s : states) {
            if (s.getStartPtsMs() <= p && p < s.getEndPtsMs()) return true;
        }
        return false;
    }

    private boolean computeDiscontinuity(UserBehaviorEvent b) {
        if (b.getClientDetectedDiscontinuity()) return true;
        if (!b.hasPreviousPlayheadPtsMs() || !b.hasPlayheadPtsMs() || !b.hasEffectiveWatchMs()) {
            return false;
        }
        long mediaDelta = Math.abs(b.getPlayheadPtsMs() - b.getPreviousPlayheadPtsMs());
        double expected = b.getEffectiveWatchMs() * Math.max(0.1, b.getPlaybackRate());
        // 2.5s absorbs HLS live-edge recalibration and heartbeat scheduling jitter.
        return Math.abs(mediaDelta - expected) > 2500;
    }

    private List<ContentContribution> intervalAttribution(
            List<ContentStateEvent> states, long fromPts, long toPts) {

        if (toPts <= fromPts) return Collections.emptyList();

        List<Overlap> overlaps = new ArrayList<>();
        long productMs = 0;
        for (ContentStateEvent s : states) {
            if (!s.getProductVisible()) continue;
            long start = Math.max(fromPts, s.getStartPtsMs());
            long end = Math.min(toPts, s.getEndPtsMs());
            long overlap = Math.max(0, end - start);
            if (overlap > 0) {
                overlaps.add(new Overlap(s, overlap));
                productMs += overlap;
            }
        }
        if (productMs <= 0) return Collections.emptyList();

        List<ContentContribution> result = new ArrayList<>();
        for (Overlap o : overlaps) {
            result.add(toContribution(
                    o.state, o.ms, (float) o.ms / (float) productMs));
        }
        return result;
    }

    private PointResult pointAttribution(List<ContentStateEvent> states, long pts) {
        if (pts < 0) return new PointResult(Collections.emptyList(), AttributionMethod.ATTRIBUTION_UNSPECIFIED);

        for (ContentStateEvent s : states) {
            if (s.getStartPtsMs() <= pts && pts < s.getEndPtsMs()) {
                if (!s.getProductVisible()) {
                    return new PointResult(Collections.emptyList(), AttributionMethod.POINT_IN_TIME);
                }
                return new PointResult(
                        List.of(toContribution(s, 0, 1.0f)),
                        AttributionMethod.POINT_IN_TIME);
            }
        }

        ContentStateEvent nearest = null;
        long gap = Long.MAX_VALUE;
        for (ContentStateEvent s : states) {
            if (s.getEndPtsMs() <= pts) {
                long g = pts - s.getEndPtsMs();
                if (g < gap) {
                    gap = g;
                    nearest = s;
                }
            }
        }
        if (nearest != null && nearest.getProductVisible() && gap <= pointStalenessMs) {
            return new PointResult(
                    List.of(toContribution(nearest, 0, 1.0f)),
                    AttributionMethod.NEAREST_PREVIOUS);
        }
        return new PointResult(Collections.emptyList(), AttributionMethod.POINT_IN_TIME);
    }

    private ContentContribution toContribution(
            ContentStateEvent s, long overlapMs, float weight) {

        ContentContribution.Builder b = ContentContribution.newBuilder()
                .setStateId(s.getStateId())
                .setOverlapMs(overlapMs)
                .setAttributionWeight(weight)
                .setContentEmbedding(s.getContentEmbedding())
                .setContentConfidence(s.getConfidence())
                .setContentModel(s.getModel());

        if (s.hasProduct() && s.getProduct().hasActiveItemId()) {
            b.setItemId(s.getProduct().getActiveItemId());
        }
        if (s.hasProduct()) {
            ProductState p = s.getProduct();
            b.addAllSemanticTags(p.getStyleTagsList());
            b.addAllSemanticTags(p.getCategoryPathList());
            if (!p.getBrand().isEmpty()) b.addSemanticTags(p.getBrand());
        }
        return b.build();
    }

    private void compactContentState() throws Exception {
        List<ContentStateEvent> states = new ArrayList<>();
        for (ContentStateEvent s : contentState.get()) states.add(s);
        states.sort(Comparator.comparingLong(ContentStateEvent::getStartPtsMs));
        // Keep the latest ~10 minutes for v1: 120 states at 5s each.
        if (states.size() > 120) {
            states = new ArrayList<>(states.subList(states.size() - 120, states.size()));
        }
        contentState.update(states);
    }

    private record Overlap(ContentStateEvent state, long ms) {}
    private record PointResult(
            List<ContentContribution> contributions,
            AttributionMethod method) {}
}
