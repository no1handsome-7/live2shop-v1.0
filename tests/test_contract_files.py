from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def test_contract_files_exist():
    names={"common.proto","behavior.proto","media.proto","content.proto","interest.proto","recommendation.proto"}
    got={p.name for p in (ROOT/"proto/live2shop/v1").glob("*.proto")}
    assert names==got

def test_test_split_is_blocked_in_evads_converter():
    text=(ROOT/"data_tools/prepare_evads.py").read_text()
    assert 'Refusing to prepare E-VAds TEST for training' in text

def test_embedding_version_in_contract():
    text=(ROOT/"proto/live2shop/v1/common.proto").read_text()
    assert "string version = 3;" in text


def test_lora_resume_reactivates_adapter_parameters():
    text = (ROOT / "ml/commerce_vlm/live2shop_commerce/model.py").read_text()
    assert 'p.requires_grad = ("lora_" in name)' in text
