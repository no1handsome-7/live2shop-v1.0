#!/usr/bin/env bash
set -euo pipefail

fail=0
ok()   { printf "[OK]   %s\n" "$*"; }
warn() { printf "[WARN] %s\n" "$*"; }
bad()  { printf "[FAIL] %s\n" "$*"; fail=1; }

command -v docker >/dev/null && ok "docker" || bad "docker missing"
docker compose version >/dev/null 2>&1 && ok "docker compose" || bad "docker compose v2 missing"
command -v ffmpeg >/dev/null && ok "ffmpeg" || warn "host ffmpeg missing (containers include it)"
command -v nvidia-smi >/dev/null && ok "NVIDIA driver visible" || bad "nvidia-smi missing"

if command -v nvidia-smi >/dev/null; then
  count=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | wc -l | tr -d ' ')
  [[ "$count" -ge 2 ]] && ok "$count GPU(s) visible" || warn "Only $count GPU(s) visible; final dual-model serving may contend"
fi

[[ -f .env ]] && ok ".env present" || warn "copy .env.example to .env"

[[ -f checkpoints/commerce-vlm/champion/commerce_config.json ]] \
  && ok "CommerceVLM champion present" \
  || warn "CommerceVLM champion missing; train/promote before full stack"

[[ -f checkpoints/rec-model/champion/rec_config.json ]] \
  && ok "RecModel champion present" \
  || warn "RecModel champion missing; train/promote before full stack"

[[ -f data/catalog/items.jsonl ]] \
  && ok "catalog present" \
  || warn "data/catalog/items.jsonl missing; create/import catalog"

if command -v docker >/dev/null && docker compose version >/dev/null 2>&1 && [[ -f .env ]]; then
  docker compose -f infra/docker-compose.yml --env-file .env config >/dev/null \
    && ok "docker compose config parses" \
    || bad "docker compose config failed"
fi

exit "$fail"
