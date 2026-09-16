from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path
from typing import Iterable

def write_jsonl(path: str | Path, rows: Iterable[dict]):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

def read_jsonl(path: str | Path):
    with Path(path).open("r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                yield json.loads(line)

def stable_split(key: str, val_pct: int = 5, test_pct: int = 5) -> str:
    h = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16) % 100
    if h < test_pct: return "test"
    if h < test_pct + val_pct: return "val"
    return "train"

def reservoir_sample(items: Iterable, k: int, seed: int = 42):
    rnd = random.Random(seed)
    result=[]
    for i,x in enumerate(items):
        if i<k: result.append(x)
        else:
            j=rnd.randint(0,i)
            if j<k: result[j]=x
    return result
