FROM maven:3.9-eclipse-temurin-17 AS builder
WORKDIR /src
COPY stream/realtime-feature/pom.xml .
COPY proto /src/src/main/proto
COPY stream/realtime-feature/src /src/src
RUN mvn -q -DskipTests package

FROM flink:1.20.3-scala_2.12-java17
COPY --from=builder /src/target/realtime-feature-1.0.0.jar /opt/flink/usrlib/realtime-feature.jar
