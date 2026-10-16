from __future__ import annotations

import hashlib
import math
import re
import unicodedata


def _norm(value) -> str:
    if value is None:
        return ""
    s = unicodedata.normalize("NFKC", str(value)).lower()
    return re.sub(r"[\s\W_]+", "", s, flags=re.UNICODE)


def _hash_embedding(text: str, dim: int = 128) -> list[float]:
    vec = [0.0] * dim

    tokens = re.findall(
        r"[a-z0-9]+|[\u4e00-\u9fff]+",
        unicodedata.normalize("NFKC", text.lower()),
    )

    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:2], "big") % dim
        sign = 1.0 if digest[2] & 1 else -1.0
        vec[idx] += sign

    norm = math.sqrt(sum(x * x for x in vec))
    if norm > 0:
        vec = [x / norm for x in vec]

    return vec


class BaselineRuntime:
    """
    Integration baseline only.

    It performs deterministic lexical matching between OCR/ASR evidence
    and the room catalog. It is NOT CommerceVLM and must never be
    reported as the trained/champion model.
    """

    def infer(
        self,
        asr_text: str,
        ocr_text: list[str],
        candidates: list[dict],
    ) -> tuple[dict, list[float]]:
        evidence_text = " ".join(
            [asr_text or ""] + [str(x) for x in (ocr_text or [])]
        ).strip()
        hay = _norm(evidence_text)

        best = None
        best_score = 0.0

        if hay:
            for c in candidates:
                score = 0.0

                title = _norm(c.get("title"))
                brand = _norm(c.get("brand"))

                if title and title in hay:
                    score += 6.0

                if brand and brand in hay:
                    score += 3.0

                for tag in c.get("style_tags") or []:
                    tag_n = _norm(tag)
                    if tag_n and tag_n in hay:
                        score += 2.0

                category_path = c.get("category_path") or []
                if category_path:
                    leaf = _norm(category_path[-1])
                    if leaf and leaf in hay:
                        score += 1.5

                if score > best_score:
                    best_score = score
                    best = c

        # A single weak category hit is not enough to identify a SKU.
        if best is None or best_score < 3.0:
            obj = {
                "product_visible": False,
                "active_item_id": None,
                "category": "",
                "brand": "",
                "style_tags": [],
                "color": "",
                "attributes": {},
                "price_cent": None,
                "promotion_tags": [],
                "confidence": 0.0,
            }
            return obj, _hash_embedding(evidence_text)

        attrs = dict(best.get("attributes") or {})
        category_path = list(best.get("category_path") or [])

        confidence = min(0.95, 0.45 + 0.07 * best_score)

        obj = {
            "product_visible": True,
            "active_item_id": int(best["item_id"]),
            "category": " / ".join(str(x) for x in category_path),
            "brand": str(best.get("brand") or ""),
            "style_tags": [str(x) for x in (best.get("style_tags") or [])],
            "color": str(attrs.get("color") or ""),
            "attributes": {str(k): str(v) for k, v in attrs.items()},
            "price_cent": (
                int(best["price_cent"])
                if best.get("price_cent") is not None
                else None
            ),
            "promotion_tags": [],
            "confidence": confidence,
        }

        embedding_text = " ".join(
            [
                evidence_text,
                str(best.get("title") or ""),
                str(best.get("brand") or ""),
                " ".join(str(x) for x in best.get("style_tags") or []),
            ]
        )

        return obj, _hash_embedding(embedding_text)
