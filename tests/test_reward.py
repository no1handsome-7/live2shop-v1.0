import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "live2shop_reward",
    ROOT / "ml" / "commerce_vlm" / "live2shop_commerce" / "reward.py",
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
verifier_reward = module.verifier_reward


def test_reward_prefers_correct_json():
    target = {
        "active_item_id": 1,
        "category": "牛仔夹克",
        "brand": "Levi's",
        "color": "blue",
        "price_cent": 39900,
        "style_tags": ["古着", "牛仔"],
        "evidence": {"asr": ["399"]},
    }
    good = """{
      "active_item_id": 1,
      "category": "牛仔夹克",
      "brand": "Levi's",
      "color": "blue",
      "price_cent": 39900,
      "style_tags": ["古着", "牛仔"],
      "evidence": {"asr": ["399"]}
    }"""
    bad = """{
      "active_item_id": 2,
      "category": "鞋",
      "brand": "X",
      "color": "red",
      "price_cent": 99900,
      "style_tags": ["运动"],
      "promotion": "五折"
    }"""
    rg, _ = verifier_reward(good, target)
    rb, _ = verifier_reward(bad, target)
    assert rg > rb


def test_invalid_json_penalized():
    r, _ = verifier_reward("not json", {"category": "x"})
    assert r < 0
