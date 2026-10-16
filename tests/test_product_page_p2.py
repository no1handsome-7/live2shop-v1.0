import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "data_tools"))

from align_page_video_evidence import align_row
from audit_commerce_labels import reasons
from build_product_page_manifest import build_manifest
from collect_product_pages import classify_access_block, redact_page_url
from export_p2_student_samples import export_row
from product_pages.adapters import adapter_for_url, detect_platform
from product_pages.schema import build_student_prompt, empty_commerce_event


def test_page_text_alignment_matches_p2_policy_examples():
    supported = align_row({
        "asr": "这款是100%纯棉",
        "ocr": [],
        "offline_page_facts": {"attributes": {"材质": "纯棉"}},
    })
    assert supported["weak_supervision"]["page_candidates"][0]["text_alignment"] == "supported_text"

    absent = align_row({
        "asr": "今天介绍这款新品",
        "ocr": [],
        "offline_page_facts": {"attributes": {"材质": "纯棉"}},
    })
    assert absent["weak_supervision"]["page_candidates"][0]["text_alignment"] == "unobserved_text"

    conflict = align_row({
        "asr": "",
        "ocr": ["蓝色", "直播价￥299"],
        "offline_page_facts": {"color": "红色", "page_price_cent": 39900},
    })
    by_field = {x["field"]: x for x in conflict["weak_supervision"]["page_candidates"]}
    assert by_field["color"]["text_alignment"] == "conflict_text"
    assert by_field["color"]["observed_value"] == ["蓝色"]
    assert by_field["price_cent"]["text_alignment"] == "conflict_text"
    assert by_field["price_cent"]["observed_value"] == [29900]
    assert "offline_page_facts" not in conflict


def test_platform_adapter_rejects_wrong_or_untrusted_hosts():
    assert detect_platform("https://item.jd.com/123456.html") == "jd"
    assert detect_platform("https://item.taobao.com/item.htm?id=123") == "taobao"
    try:
        adapter_for_url("https://example.com/item?id=123", "taobao")
    except ValueError as exc:
        assert "unsupported product-page host" in str(exc)
    else:
        raise AssertionError("untrusted host should be rejected")


def test_page_url_redaction_and_access_barrier_classification():
    url = (
        "https://item.taobao.com/item.htm?id=123&skuId=456&mi_id=secret"
        "&newtonParams=signed-value"
    )
    redacted = redact_page_url(url)
    assert redacted == "https://item.taobao.com/item.htm?id=123&skuId=456"
    assert "secret" not in redacted
    assert "signed-value" not in redacted
    assert (
        classify_access_block("https://login.taobao.com/havanaone/login/login.htm", {})
        == "login_required"
    )
    assert classify_access_block("https://item.taobao.com/item.htm?id=123", {"bxpunish": "1"}) == (
        "anti_bot_challenge"
    )
    assert classify_access_block("https://item.jd.com/123.html", {}) is None


def test_training_and_online_prompt_contract_are_identical():
    source = (ROOT / "services/content-ai/app/perception.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "build_prompt"
    )
    module = ast.Module(body=[ast.Import(names=[ast.alias(name="json")]), function], type_ignores=[])
    namespace = {}
    exec(compile(ast.fix_missing_locations(module), "perception.py", "exec"), namespace)
    assert namespace["build_prompt"]("口播", ["文字"]) == build_student_prompt("口播", ["文字"])
    main_source = (ROOT / "services/content-ai/app/main.py").read_text(encoding="utf-8")
    assert "build_prompt(asr_text, ocr_text)" in main_source
    assert "build_prompt(asr_text, ocr_text, candidates" not in main_source


def _auditable_row():
    target = empty_commerce_event()
    target.update({
        "product_visible": True,
        "price_cent": 29900,
        "evidence": {"asr": ["直播价299元"], "ocr": [], "visual": ["画面展示主商品"]},
    })
    return {
        "media": "/tmp/window.mp4",
        "prompt": build_student_prompt("直播价299元", []),
        "target_json": target,
        "context": {"asr": "直播价299元", "ocr": []},
        "annotation": {
            "confidence": 0.9,
            "page_fact_decisions": [
                {"field": "price_cent", "decision": "conflict", "observed_value": 29900}
            ],
        },
        "annotation_context": {
            "weak_supervision": {
                "observed_prices_cent": [29900],
                "page_candidates": [{
                    "field": "price_cent",
                    "page_field": "page_price_cent",
                    "page_value": 39900,
                    "text_alignment": "conflict_text",
                    "observed_value": [29900],
                }],
            }
        },
        "meta": {"audit_status": "accepted", "split": "train"},
    }


def test_audit_accepts_observed_live_price_over_page_price():
    assert reasons(_auditable_row()) == []


def test_audit_routes_page_only_and_color_conflicts_to_review():
    row = _auditable_row()
    row["target_json"]["brand"] = "页面品牌"
    row["annotation_context"]["weak_supervision"]["page_candidates"].append({
        "field": "brand",
        "page_value": "页面品牌",
        "text_alignment": "unobserved_text",
        "observed_value": None,
    })
    row["annotation"]["page_fact_decisions"].append({
        "field": "brand", "decision": "supported", "observed_value": "页面品牌"
    })
    assert "page_only_or_visual_only_field:brand" in reasons(row)

    row = _auditable_row()
    row["target_json"]["color"] = "蓝色"
    row["annotation_context"]["weak_supervision"]["page_candidates"].append({
        "field": "color",
        "page_value": "红色",
        "text_alignment": "conflict_text",
        "observed_value": ["蓝色"],
    })
    row["annotation"]["page_fact_decisions"].append({
        "field": "color", "decision": "conflict", "observed_value": "蓝色"
    })
    got = reasons(row)
    assert "page_video_conflict:color" in got

    row = _auditable_row()
    row["target_json"]["brand"] = "页面品牌"
    row["annotation_context"]["weak_supervision"]["page_reference"] = {
        "title": "页面品牌 新款商品"
    }
    assert "page_reference_only_or_visual_only:brand" in reasons(row)


def test_student_export_physically_removes_page_annotation_data():
    row = _auditable_row()
    row["meta"].update({"source_product_id": "123", "product_group_key": "group"})
    exported = export_row(row)
    assert set(exported) == {"media", "media_type", "prompt", "target", "target_json", "retrieval_text", "meta"}
    assert "annotation" not in exported
    assert "annotation_context" not in exported
    serialized = json.dumps(exported, ensure_ascii=False)
    assert "page_candidates" not in serialized
    assert "39900" not in serialized
    assert "active_item_id" not in serialized


def test_manifest_deduplicates_video_and_rejects_cross_product_reuse():
    base = {
        "status": "collected",
        "platform": "jd",
        "schema_version": "product_page_record.v1",
        "selected_video": {"sha256": "abc", "local_path": "/tmp/a.mp4"},
        "page_facts": {},
    }
    manifest, rejected = build_manifest([
        {**base, "product_group_key": "one", "source_product_id": "1"},
        {**base, "product_group_key": "two", "source_product_id": "2"},
    ], 5, 5)
    assert manifest == []
    assert rejected[0]["reason"] == "same_video_assigned_to_multiple_products"
