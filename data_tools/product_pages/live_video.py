"""Associate a normal, user-visible Taobao live-explanation activation with HLS."""
from __future__ import annotations

import asyncio
from urllib.parse import urlparse


async def discover_live_video(page, product_id, title, timeout_ms=12000):
    result = {"candidate": None, "reason": None, "evidence": {}}
    entry = page.locator('[class*="RightSideNav--"]').get_by_text("直播讲解", exact=True)
    if not product_id or not title or await entry.count() != 1:
        result["reason"] = "live_entry_missing_or_ambiguous"
        return result
    player = page.locator('[class*="player-container--"] canvas, [class*="player-container--"] video')
    if await player.count():
        # Do not infer causality from a player someone opened before observation.
        result["reason"] = "live_player_already_open"
        return result

    started = set()
    manifests = {}
    arrived = asyncio.Event()

    def on_request(request):
        if request.frame == page.main_frame:
            started.add(request)

    def on_response(response):
        if response.request not in started or not 200 <= response.status < 300:
            return
        content_type = response.headers.get("content-type", "").lower()
        if urlparse(response.url).scheme not in {"http", "https"}:
            return
        if urlparse(response.url).path.lower().endswith(".m3u8") or "mpegurl" in content_type:
            manifests[response.url] = content_type
            arrived.set()

    page.on("request", on_request)
    page.on("response", on_response)
    try:
        await entry.click(timeout=timeout_ms)
        await player.first.wait_for(state="visible", timeout=timeout_ms)
        await asyncio.wait_for(arrived.wait(), timeout=timeout_ms / 1000)
        # Allow immediate quality/playlist alternatives to arrive before deciding.
        await page.wait_for_timeout(750)
        selected = page.locator('[class*="card-selected--"] [class*="cardTitle--"]')
        selected_title = (await selected.first.inner_text()).strip() if await selected.count() == 1 else None
        same_title = selected_title is not None and "".join(selected_title.split()) == "".join(title.split())
        result["evidence"] = {
            "source_product_id": product_id,
            "entry_text": "直播讲解",
            "selected_title": selected_title,
            "selected_title_matches": same_title,
            "player_kind": await player.first.evaluate("n => n.tagName.toLowerCase()"),
            "request_started_after_activation": True,
            "request_frame": "main",
            "hls_count": len(manifests),
        }
        if not same_title:
            result["reason"] = "live_selected_product_mismatch"
        elif len(manifests) != 1:
            result["reason"] = "ambiguous_live_hls"
        else:
            url, mime = next(iter(manifests.items()))
            result["candidate"] = {
                "url": url, "content_type": mime, "duration_sec": None,
                "provenance": "main_product_live_explanation",
                "association": result["evidence"],
            }
    except (asyncio.TimeoutError, TimeoutError):
        result["reason"] = "live_player_or_hls_timeout"
    except Exception as exc:
        # Playwright's own TimeoutError is distinct; preserve safe diagnostics.
        result["reason"] = "live_player_probe_failed"
        result["error_type"] = type(exc).__name__
    finally:
        page.remove_listener("request", on_request)
        page.remove_listener("response", on_response)
    return result
