from __future__ import annotations

import json
from typing import Any

ALLOWED_KEYS = {
    "product_visible", "active_item_id", "category", "brand", "style_tags",
    "color", "attributes", "price_cent", "promotion_tags",
    "evidence", "confidence",
}

def parse_json_loose(text: str) -> dict | None:
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end+1])
            except Exception:
                return None
    return None

def verifier_reward(pred_text: str, target: dict[str, Any]) -> tuple[float, dict[str, float]]:
    pred = parse_json_loose(pred_text)
    if pred is None:
        return -1.0, {"schema": -1.0}

    schema = 1.0 if isinstance(pred, dict) else -1.0
    fields = ["active_item_id", "category", "brand", "color", "price_cent"]
    correct, total = 0.0, 0
    for f in fields:
        if f in target and target[f] not in (None, "", []):
            total += 1
            if pred.get(f) == target.get(f):
                correct += 1
    attr = correct / max(total, 1)

    target_styles = set(target.get("style_tags") or [])
    pred_styles = set(pred.get("style_tags") or [])
    style = (
        len(target_styles & pred_styles) / max(len(target_styles | pred_styles), 1)
        if target_styles or pred_styles else 1.0
    )

    unsupported = [k for k in pred if k not in ALLOWED_KEYS]
    hallucination = min(1.0, len(unsupported) / 3.0)

    evidence = 1.0 if pred.get("evidence") else 0.0

    reward = 0.15 * schema + 0.45 * attr + 0.15 * style + 0.15 * evidence - 0.25 * hallucination
    return float(reward), {
        "schema": schema,
        "attribute": attr,
        "style": style,
        "evidence": evidence,
        "hallucination_penalty": hallucination,
    }
