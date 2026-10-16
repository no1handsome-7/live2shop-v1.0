from __future__ import annotations

import hashlib
import json
import re
from typing import Any
from urllib.parse import parse_qs, urlparse

COMMERCE_EVENT_SCHEMA_VERSION = "commerce_event.v2"
PAGE_RECORD_SCHEMA_VERSION = "product_page_record.v1"


def empty_commerce_event() -> dict[str, Any]:
    """Return the P2 target shape.

    ``active_item_id`` deliberately is not part of v2: a platform identifier is
    not observable from video/ASR/OCR. Annotation confidence is metadata for QA,
    not a prediction target.
    """
    return {
        "schema_version": COMMERCE_EVENT_SCHEMA_VERSION,
        "product_visible": False,
        "category": None,
        "brand": None,
        "style_tags": [],
        "color": None,
        "attributes": {},
        "price_cent": None,
        "promotion_tags": [],
        "evidence": {"asr": [], "ocr": [], "visual": []},
    }


def build_student_prompt(asr: str, ocr: list[str]) -> str:
    """Build the only prompt that may be placed in final P2 student rows."""
    return (
        "根据商品讲解视频及以下自动转写信息，输出当前窗口的 CommerceEvent JSON。"
        "只能使用视频画面、ASR 和 OCR 中可观察到的证据；未知字段使用 null、空列表或空对象，"
        "不得猜测平台商品 ID 或任何未观察属性。price_cent 仅表示当前视频中明确出现的人民币价格（分）。\n"
        f"ASR={asr or ''}\n"
        f"OCR={json.dumps(ocr or [], ensure_ascii=False)}"
    )


def product_group_key(platform: str, product_id: str | None, canonical_url: str) -> str:
    """Stable split group; all clips of one product must share this key."""
    if product_id:
        raw = f"{platform}:{product_id}"
    else:
        parsed = urlparse(canonical_url)
        raw = f"{platform}:{parsed.netloc.lower()}:{parsed.path}:{parsed.query}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def product_id_from_url(url: str, platform: str) -> str | None:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    keys = ("id", "itemId", "item_id", "skuId", "sku")
    for key in keys:
        values = query.get(key)
        if values and re.fullmatch(r"[A-Za-z0-9_-]+", values[0]):
            return values[0]
    if platform == "jd":
        match = re.search(r"/(\d{5,})\.html(?:$|[?#])", url)
        if match:
            return match.group(1)
    return None


def normalize_price_cent(value: Any) -> int | None:
    """Normalize page-display prices (which are denominated in yuan) to cents."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value * 100 if value >= 0 else None
    if isinstance(value, float):
        return round(value * 100) if value >= 0 else None
    text = str(value).strip().replace(",", "")
    match = re.search(r"(?:¥|￥|RMB|CNY)?\s*(\d+(?:\.\d{1,2})?)", text, re.I)
    if not match:
        return None
    return round(float(match.group(1)) * 100)


def normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None
