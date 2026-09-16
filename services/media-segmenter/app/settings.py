from dataclasses import dataclass
import os

@dataclass(frozen=True)
class Settings:
    kafka_brokers: str = os.getenv("KAFKA_BROKERS", "kafka:9092")
    redis_url: str = os.getenv("REDIS_URL", "redis://redis:6379/0")
    minio_endpoint: str = os.getenv("MINIO_ENDPOINT", "minio:9000")
    minio_access_key: str = os.getenv("MINIO_ACCESS_KEY", "live2shop")
    minio_secret_key: str = os.getenv("MINIO_SECRET_KEY", "live2shop-secret")
    media_bucket: str = os.getenv("MEDIA_BUCKET", "live2shop-media")
    postgres_dsn: str = os.getenv(
        "POSTGRES_DSN",
        "postgresql://live2shop:live2shop@postgres:5432/live2shop",
    )
    srs_rtmp_base: str = os.getenv("SRS_RTMP_BASE", "rtmp://srs:1935/live")
    segment_seconds: float = float(os.getenv("SEGMENT_SECONDS", "5"))
    work_dir: str = os.getenv("WORK_DIR", "/tmp/live2shop-segments")

settings = Settings()
