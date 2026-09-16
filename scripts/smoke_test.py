import json, time, urllib.request

def get(url):
    with urllib.request.urlopen(url, timeout=3) as r:
        return r.status, r.read().decode()

targets = {
    "gateway": "http://localhost:8088/healthz",
    "recommendation": "http://localhost:8090/healthz",
    "segmenter": "http://localhost:8091/healthz",
    "rec-model": "http://localhost:8092/healthz",
    "srs-api": "http://localhost:1985/api/v1/versions",
}
bad = []
for name, url in targets.items():
    try:
        status, body = get(url)
        print(f"{name:16s} {status} {body[:120]}")
        if status != 200:
            bad.append(name)
    except Exception as e:
        print(f"{name:16s} FAIL {e}")
        bad.append(name)

if bad:
    raise SystemExit("Smoke check failed: " + ", ".join(bad))
print("Smoke check passed.")
