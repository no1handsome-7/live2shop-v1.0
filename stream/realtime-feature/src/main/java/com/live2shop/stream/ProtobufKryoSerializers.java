package com.live2shop.stream;

import com.esotericsoftware.kryo.Kryo;
import com.esotericsoftware.kryo.KryoException;
import com.esotericsoftware.kryo.Serializer;
import com.esotericsoftware.kryo.io.Input;
import com.esotericsoftware.kryo.io.Output;
import com.google.protobuf.InvalidProtocolBufferException;
import com.live2shop.proto.v1.AttributedBehaviorEvent;
import com.live2shop.proto.v1.ContentStateEvent;
import com.live2shop.proto.v1.UserBehaviorEvent;

/**
 * Kryo adapters for protobuf generated messages.
 *
 * Protobuf generated repeated fields use protobuf-managed collection
 * implementations and must not be reflectively reconstructed by Kryo.
 *
 * Serialize the whole protobuf message as its canonical wire bytes instead.
 */
public final class ProtobufKryoSerializers {

    private ProtobufKryoSerializers() {}

    private static void writeBytes(Output output, byte[] bytes) {
        output.writeInt(bytes.length, true);
        output.writeBytes(bytes);
    }

    private static byte[] readBytes(Input input) {
        int length = input.readInt(true);
        if (length < 0) {
            throw new KryoException("Negative protobuf payload length: " + length);
        }
        return input.readBytes(length);
    }

    public static final class UserBehaviorEventSerializer
            extends Serializer<UserBehaviorEvent> {

        @Override
        public void write(
                Kryo kryo,
                Output output,
                UserBehaviorEvent object) {
            writeBytes(output, object.toByteArray());
        }

        @Override
        public UserBehaviorEvent read(
                Kryo kryo,
                Input input,
                Class<UserBehaviorEvent> type) {
            try {
                return UserBehaviorEvent.parseFrom(readBytes(input));
            } catch (InvalidProtocolBufferException e) {
                throw new KryoException(
                        "Failed to deserialize UserBehaviorEvent protobuf",
                        e);
            }
        }
    }

    public static final class ContentStateEventSerializer
            extends Serializer<ContentStateEvent> {

        @Override
        public void write(
                Kryo kryo,
                Output output,
                ContentStateEvent object) {
            writeBytes(output, object.toByteArray());
        }

        @Override
        public ContentStateEvent read(
                Kryo kryo,
                Input input,
                Class<ContentStateEvent> type) {
            try {
                return ContentStateEvent.parseFrom(readBytes(input));
            } catch (InvalidProtocolBufferException e) {
                throw new KryoException(
                        "Failed to deserialize ContentStateEvent protobuf",
                        e);
            }
        }
    }

    public static final class AttributedBehaviorEventSerializer
            extends Serializer<AttributedBehaviorEvent> {

        @Override
        public void write(
                Kryo kryo,
                Output output,
                AttributedBehaviorEvent object) {
            writeBytes(output, object.toByteArray());
        }

        @Override
        public AttributedBehaviorEvent read(
                Kryo kryo,
                Input input,
                Class<AttributedBehaviorEvent> type) {
            try {
                return AttributedBehaviorEvent.parseFrom(readBytes(input));
            } catch (InvalidProtocolBufferException e) {
                throw new KryoException(
                        "Failed to deserialize AttributedBehaviorEvent protobuf",
                        e);
            }
        }
    }
}
