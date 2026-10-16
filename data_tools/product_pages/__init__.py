"""Product-page collection helpers for the CommerceVLM P2 dataset."""

from .adapters import adapter_for_url, detect_platform
from .schema import (
    COMMERCE_EVENT_SCHEMA_VERSION,
    build_student_prompt,
    empty_commerce_event,
    product_group_key,
)

__all__ = [
    "COMMERCE_EVENT_SCHEMA_VERSION",
    "adapter_for_url",
    "build_student_prompt",
    "detect_platform",
    "empty_commerce_event",
    "product_group_key",
]
