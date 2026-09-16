package com.live2shop.stream;

import com.live2shop.proto.v1.AttributedBehaviorEvent;
import com.live2shop.proto.v1.ContentStateEvent;
import com.live2shop.proto.v1.UserBehaviorEvent;
import org.apache.flink.api.common.eventtime.WatermarkStrategy;
import org.apache.flink.connector.base.DeliveryGuarantee;
import org.apache.flink.connector.kafka.sink.KafkaRecordSerializationSchema;
import org.apache.flink.connector.kafka.sink.KafkaSink;
import org.apache.flink.connector.kafka.source.KafkaSource;
import org.apache.flink.connector.kafka.source.enumerator.initializer.OffsetsInitializer;
import org.apache.flink.streaming.api.CheckpointingMode;
import org.apache.flink.streaming.api.datastream.DataStream;
import org.apache.flink.streaming.api.environment.StreamExecutionEnvironment;
import org.apache.kafka.clients.producer.ProducerRecord;

import java.time.Duration;
import java.nio.charset.StandardCharsets;

public class RealtimeAttributionJob {
    public static void main(String[] args) throws Exception {
        String brokers = env("KAFKA_BROKERS", "kafka:9092");
        long waitMs = Long.parseLong(env("ATTRIBUTION_LATENESS_MS", "7000"));
        long stalenessMs = Long.parseLong(env("POINT_STALENESS_MS", "8000"));

        StreamExecutionEnvironment env = StreamExecutionEnvironment.getExecutionEnvironment();
        env.enableCheckpointing(10_000, CheckpointingMode.EXACTLY_ONCE);
        env.getCheckpointConfig().setCheckpointTimeout(60_000);
        env.getCheckpointConfig().setMinPauseBetweenCheckpoints(5_000);
        env.getCheckpointConfig().setCheckpointStorage(
                env("FLINK_CHECKPOINT_URI", "file:///opt/flink/checkpoints"));
        env.setParallelism(3);

        KafkaSource<UserBehaviorEvent> behaviorSource = KafkaSource
                .<UserBehaviorEvent>builder()
                .setBootstrapServers(brokers)
                .setTopics("live2shop.user.behavior.v1")
                .setGroupId("realtime-attribution-behavior-v1")
                .setStartingOffsets(OffsetsInitializer.earliest())
                .setValueOnlyDeserializer(new ProtoSchemas.BehaviorDeserializer())
                .build();

        KafkaSource<ContentStateEvent> contentSource = KafkaSource
                .<ContentStateEvent>builder()
                .setBootstrapServers(brokers)
                .setTopics("live2shop.content.state.v1")
                .setGroupId("realtime-attribution-content-v1")
                .setStartingOffsets(OffsetsInitializer.earliest())
                .setValueOnlyDeserializer(new ProtoSchemas.ContentDeserializer())
                .build();

        WatermarkStrategy<UserBehaviorEvent> behaviorWm = WatermarkStrategy
                .<UserBehaviorEvent>forBoundedOutOfOrderness(Duration.ofSeconds(3))
                .withTimestampAssigner((e, ts) -> e.getMeta().getEventTimeMs())
                .withIdleness(Duration.ofSeconds(30));

        WatermarkStrategy<ContentStateEvent> contentWm = WatermarkStrategy
                .<ContentStateEvent>forBoundedOutOfOrderness(Duration.ofSeconds(3))
                .withTimestampAssigner((e, ts) -> e.getMeta().getEventTimeMs())
                .withIdleness(Duration.ofSeconds(30));

        DataStream<UserBehaviorEvent> behavior = env.fromSource(
                behaviorSource, behaviorWm, "user-behavior");

        DataStream<ContentStateEvent> content = env.fromSource(
                contentSource, contentWm, "content-state");

        DataStream<AttributedBehaviorEvent> attributed = behavior
                .keyBy(UserBehaviorEvent::getStreamSessionId)
                .connect(content.keyBy(ContentStateEvent::getStreamSessionId))
                .process(new AttributionFunction(waitMs, stalenessMs))
                .name("temporal-behavior-content-attribution");

        KafkaSink<AttributedBehaviorEvent> sink = KafkaSink
                .<AttributedBehaviorEvent>builder()
                .setBootstrapServers(brokers)
                .setDeliveryGuarantee(DeliveryGuarantee.EXACTLY_ONCE)
                .setTransactionalIdPrefix("live2shop-attribution-")
                .setRecordSerializer((KafkaRecordSerializationSchema<AttributedBehaviorEvent>)
                        (element, context, timestamp) -> new ProducerRecord<>(
                                "live2shop.behavior.attributed.v1",
                                Long.toString(element.getUserId()).getBytes(StandardCharsets.UTF_8),
                                element.toByteArray()))
                .build();

        attributed.sinkTo(sink).name("attributed-behavior-kafka");

        env.execute("Live2Shop Temporal Behavior-Content Attribution");
    }

    private static String env(String key, String fallback) {
        String value = System.getenv(key);
        return value == null || value.isBlank() ? fallback : value;
    }
}
