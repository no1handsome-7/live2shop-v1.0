
# Data pipeline validation changes

## prepare_products10k.py
- Detects image/label columns automatically.
- Defaults support official Products-10K style fields:
  - image/name/file
  - class/label/category
- Rejects empty or suspiciously small datasets.

## build_commerce_windows.py
- Accepts catalog JSON arrays and JSONL.
- Rejects duplicate room_id values.
- Rejects the same source video appearing in different split values.

Recommended manifest:

```json
{"video":"live01.mp4","room_id":"live01","split":"train"}
{"video":"live02.mp4","room_id":"live02","split":"test"}
```
