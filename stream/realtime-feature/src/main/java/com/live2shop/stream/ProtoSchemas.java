package com.live2shop.stream;

import com.google.protobuf.MessageLite;
import com.live2shop.proto.v1.AttributedBehaviorEvent;
import com.live2shop.proto.v1.ContentStateEvent;
import com.live2shop.proto.v1.UserBehaviorEvent;
import org.apache.flink.api.common.serialization.DeserializationSchema;
import org.apache.flink.api.common.serialization.SerializationSchema;

import java.io.IOException;

public final class ProtoSchemas {
    private ProtoSchemas() {}

    public static class BehaviorDeserializer implements DeserializationSchema<UserBehaviorEvent> {
        @Override public UserBehaviorEvent deserialize(byte[] message) throws IOException {
            return UserBehaviorEvent.parseFrom(message);
        }
        @Override public boolean isEndOfStream(UserBehaviorEvent nextElement) { return false; }
        @Override public org.apache.flink.api.common.typeinfo.TypeInformation<UserBehaviorEvent> getProducedType() {
            return org.apache.flink.api.java.typeutils.TypeExtractor.getForClass(UserBehaviorEvent.class);
        }
    }

    public static class ContentDeserializer implements DeserializationSchema<ContentStateEvent> {
        @Override public ContentStateEvent deserialize(byte[] message) throws IOException {
            return ContentStateEvent.parseFrom(message);
        }
        @Override public boolean isEndOfStream(ContentStateEvent nextElement) { return false; }
        @Override public org.apache.flink.api.common.typeinfo.TypeInformation<ContentStateEvent> getProducedType() {
            return org.apache.flink.api.java.typeutils.TypeExtractor.getForClass(ContentStateEvent.class);
        }
    }

    public static class AttributedSerializer implements SerializationSchema<AttributedBehaviorEvent> {
        @Override public byte[] serialize(AttributedBehaviorEvent element) {
            return element.toByteArray();
        }
    }
}
