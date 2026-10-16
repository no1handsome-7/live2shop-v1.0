#!/usr/bin/env python3
"""Collect main-product facts and merchant product videos from authorized pages.

This collector intentionally uses a normal browser session. It does not bypass
CAPTCHAs, login controls, anti-bot systems, or private APIs. If a page cannot be
opened normally, the record is marked for review instead of attempting evasion.

Input JSONL (one or both of --input/--url):
  {"url":"https://item.jd.com/123.html", "rights_basis":"research", ...}

Optional per-row ``video_url`` is an explicit human selection when the adapter
cannot safely decide which of several videos belongs to the main product.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from common import read_jsonl, write_jsonl
from product_pages.adapters import (
    adapter_for_url,
    is_probable_video_url,
    parse_attributes_text,
    score_video_candidate,
)
from product_pages.schema import PAGE_RECORD_SCHEMA_VERSION, product_group_key
from product_pages.browser_ui import BrowserUI


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def redact_url(url: str) -> str:
    """Do not copy signed CDN query tokens into logs/review summaries."""
    return url.split("?", 1)[0]


def redact_page_url(url: str) -> str:
    """Keep stable product identifiers, but discard tracking/auth query data."""
    parsed = urlparse(url)
    safe_keys = {"id", "itemId", "item_id", "skuId", "sku"}
    safe_query = urlencode(
        [(key, value) for key, value in parse_qsl(parsed.query) if key in safe_keys]
    )
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path, "", safe_query, ""))


def classify_access_block(final_url: str, headers: dict[str, str] | None = None) -> str | None:
    """Classify normal platform access barriers without attempting to bypass them."""
    parsed = urlparse(final_url)
    host = (parsed.hostname or "").lower()
    path = parsed.path.lower()
    normalized_headers = {str(key).lower(): str(value) for key, value in (headers or {}).items()}
    if host in {"login.taobao.com", "login.m.taobao.com", "passport.jd.com"}:
        return "login_required"
    if "punish" in host or "punish" in path or normalized_headers.get("bxpunish") == "1":
        return "anti_bot_challenge"
    if normalized_headers.get("x-sec-reason"):
        return "access_security_check"
    return None


async def inspect_access(page, navigation, adapter, target_url):
    # A redirect or manual navigation invalidates the previous document headers.
    headers = navigation.headers if navigation and navigation.url == page.url else {}
    reason = classify_access_block(page.url, headers)
    if reason:
        return reason
    dom_reason = await page.evaluate("""() => {
      const visible = n => !!(n.getClientRects().length && getComputedStyle(n).visibility !== 'hidden');
      for (const n of document.querySelectorAll('iframe')) {
        if (!visible(n)) continue;
        const src = n.src || '';
        if (/login\\.taobao\\.com|login\\.m\\.taobao\\.com|passport\\.jd\\.com/.test(src)) return 'login_required';
        if (/punish|captcha|verify/i.test(src)) return 'anti_bot_challenge';
      }
      for (const n of document.querySelectorAll('[role=dialog], [class*=login], [class*=Login]')) {
        if (visible(n) && n.querySelector('input[type=password]')) return 'login_required';
      }
      for (const n of document.querySelectorAll('#nc_1_wrapper, #baxia-dialog-content, #captcha')) {
        if (visible(n)) return 'anti_bot_challenge';
      }
      return null;
    }""")
    if dom_reason:
        return dom_reason
    target_id = adapter.product_id(target_url)
    try:
        current_adapter = adapter_for_url(page.url)
    except ValueError:
        return "product_page_not_confirmed"
    if current_adapter.platform != adapter.platform or (target_id and adapter.product_id(page.url) != target_id):
        return "product_page_not_confirmed"
    ready = await page.evaluate("""cfg => {
      const title = cfg.title.some(s => {const n=document.querySelector(s); return n && (n.content || n.textContent || '').trim();});
      return title && cfg.roots.some(s => document.querySelector(s));
    }""", {"title": list(adapter.selectors.title), "roots": list(adapter.selectors.detail_roots + adapter.selectors.media_roots)})
    return None if ready else "product_page_not_confirmed"


async def navigate_product(page, url, adapter, args):
    """Retry the ORIGINAL product after human login, with a fresh response."""
    ui = getattr(args, "browser_ui", None)
    navigation = await page.goto(url, wait_until="domcontentloaded", timeout=args.navigation_timeout_ms)
    await page.wait_for_timeout(args.settle_ms)
    reason = await inspect_access(page, navigation, adapter, url)
    for _ in range(getattr(args, "login_attempts", 3)):
        if not reason or not (ui or args.interactive):
            break
        message = f"需要人工处理（{reason}）：请完成登录/验证，再点击完成。程序将返回原商品页复查。"
        try:
            if ui:
                ui.attach(page)
                await ui.wait(message, args.login_timeout_sec)
            else:
                print(message, flush=True)
                await asyncio.to_thread(input, "处理完成后按 Enter：")
        except asyncio.TimeoutError:
            return navigation, "manual_login_timeout"
        # Never reuse the pre-login security response or assume Enter means success.
        navigation = await page.goto(url, wait_until="domcontentloaded", timeout=args.navigation_timeout_ms)
        await page.wait_for_timeout(args.settle_ms)
        reason = await inspect_access(page, navigation, adapter, url)
    return navigation, reason


def require_http_url(url: str) -> str:
    if urlparse(url).scheme.lower() not in {"http", "https"}:
        raise ValueError("only http/https URLs are supported")
    return url


def choose_video(candidates: list[dict[str, Any]], product_id: str | None, minimum: int):
    ranked = sorted(
        ({**c, "selection_score": score_video_candidate(c, product_id)} for c in candidates),
        key=lambda x: (-x["selection_score"], x.get("url", "")),
    )
    eligible = [x for x in ranked if x["selection_score"] >= minimum]
    if not eligible:
        return None, ranked, "no_high_confidence_main_product_video"
    if len(eligible) > 1 and eligible[0]["selection_score"] == eligible[1]["selection_score"]:
        return None, ranked, "ambiguous_main_product_video"
    return eligible[0], ranked, None


def _download_http(url: str, output: Path, referer: str, cookies: list[dict[str, Any]]) -> None:
    cookie = "; ".join(f"{x['name']}={x['value']}" for x in cookies)
    headers = {"User-Agent": "Mozilla/5.0", "Referer": referer}
    if cookie:
        headers["Cookie"] = cookie
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response, output.open("wb") as handle:
        shutil.copyfileobj(response, handle, length=1024 * 1024)


def _download_hls(url: str, output: Path, referer: str, _cookies: list[dict[str, Any]]) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except ImportError as exc:
            raise RuntimeError("HLS requires ffmpeg or imageio-ffmpeg") from exc
    # Do not put browser cookies in an ffmpeg command line: command arguments
    # can be visible to other local users. Signed/public HLS URLs normally need
    # only referer/user-agent; protected streams are routed to manual review.
    headers = f"Referer: {referer}\r\nUser-Agent: Mozilla/5.0\r\n"
    subprocess.run(
        [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
            "-headers", headers, "-i", url, "-c", "copy", str(output),
        ],
        check=True,
        timeout=600,
        capture_output=True,
    )


async def collect_one(context: Any, source: dict[str, Any], root: Path, args) -> dict[str, Any]:
    url = require_http_url(str(source["url"]))
    adapter = adapter_for_url(url, source.get("platform"))
    supplied_product_id = source.get("product_id")
    provisional_id = str(supplied_product_id or adapter.product_id(url) or hashlib.sha256(url.encode()).hexdigest()[:16])
    collection_id = str(source.get("collection_id") or f"{provisional_id}_{hashlib.sha256(url.encode()).hexdigest()[:10]}")
    if len(collection_id) > 160 or not all(char.isalnum() or char in "._-" for char in collection_id):
        raise ValueError("collection_id contains unsafe path characters")
    output_dir = root / adapter.platform / collection_id
    output_dir.mkdir(parents=True, exist_ok=True)

    page = await context.new_page()
    ui = getattr(args, "browser_ui", None)
    if ui:
        ui.attach(page)
    network_videos: list[dict[str, Any]] = []
    popup_pages = []

    def on_response(response: Any):
        content_type = response.headers.get("content-type", "")
        if is_probable_video_url(response.url, content_type):
            network_videos.append({
                "url": response.url,
                "content_type": content_type,
                "duration_sec": None,
                "provenance": "page_network",
            })

    page.on("response", on_response)

    def on_popup(popup):
        popup_pages.append(popup)
        popup.on("response", on_response)
        if ui:
            ui.attach(popup)

    page.on("popup", on_popup)
    record: dict[str, Any] = {
        "schema_version": PAGE_RECORD_SCHEMA_VERSION,
        "platform": adapter.platform,
        "collection_id": collection_id,
        "source_url": redact_page_url(url),
        "source_url_sha256": hashlib.sha256(url.encode("utf-8")).hexdigest(),
        "rights_basis": source.get("rights_basis"),
        "collected_at": now_iso(),
        "adapter": f"{type(adapter).__name__}.v1",
        "status": "started",
    }
    try:
        navigation, access_reason = await navigate_product(page, url, adapter, args)
        if not access_reason and getattr(args, "inspect_media", False):
            message = "商品页已通过检查。请查看主商品视频入口，可点击播放或链接；核实完成后点击完成继续提取。"
            if ui:
                ui.attach(page)
                await ui.wait(message, args.login_timeout_sec)
            else:
                print(message, flush=True)
                await asyncio.to_thread(input, "完成视频核实后按 Enter：")
            record["manual_media_inspection"] = {
                "popup_urls": [redact_page_url(p.url) for p in popup_pages if not p.is_closed()],
                "current_url": redact_page_url(page.url),
            }
            if page.url != url:
                navigation = await page.goto(url, wait_until="domcontentloaded", timeout=args.navigation_timeout_ms)
                await page.wait_for_timeout(args.settle_ms)
            access_reason = await inspect_access(page, navigation, adapter, url)
        if not access_reason and args.scroll_steps:
            await page.evaluate(
                """async (steps) => {
                  const height = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight);
                  for (let i = 1; i <= steps; i++) {
                    window.scrollTo(0, height * i / steps);
                    await new Promise(resolve => setTimeout(resolve, 350));
                  }
                  window.scrollTo(0, 0);
                }""",
                args.scroll_steps,
            )
            await page.wait_for_timeout(500)

        canonical_url = page.url
        product_id = str(supplied_product_id or adapter.product_id(canonical_url) or provisional_id)
        group_key = product_group_key(adapter.platform, product_id, canonical_url)

        html_path = output_dir / "page.html"
        screenshot_path = output_dir / "page.png"
        if access_reason:
            # Login/challenge pages may contain transient QR or auth tokens.
            # Do not retain their HTML or screenshots, including artifacts from
            # an earlier retry of the same collection ID.
            html_path.unlink(missing_ok=True)
            screenshot_path.unlink(missing_ok=True)
            record.update({
                "canonical_url": redact_page_url(canonical_url),
                "http_status": navigation.status if navigation is not None else None,
                "source_product_id": product_id,
                "product_group_key": group_key,
                "page_facts": {},
                "detail_images": [],
                "video_candidates": [],
                "snapshot": {"captured": False, "reason": "access_page_not_persisted"},
                "status": "access_blocked",
                "review_reason": access_reason,
            })
            (output_dir / "record.json").write_text(
                json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return record

        if hasattr(adapter, "prepare_details"):
            await adapter.prepare_details(page)
        extracted = await adapter.extract(page)
        live_candidates = []
        if hasattr(adapter, "discover_live_video"):
            live_result = await adapter.discover_live_video(page)
            record["live_video_association"] = {
                key: value for key, value in live_result.items() if key != "candidate"
            }
            if live_result.get("candidate"):
                live_candidates.append(live_result["candidate"])
        html_path.write_text(await page.content(), encoding="utf-8")
        await page.screenshot(path=str(screenshot_path), full_page=True)
        record["media_entries"] = [
            {**{key: value for key, value in entry.items() if key != "url"},
             "url_for_review": redact_page_url(str(entry.get("url", "")))}
            for entry in extracted.get("media_entries", [])
        ]

        candidates_by_url: dict[str, dict[str, Any]] = {}
        for candidate in extracted["dom_video_candidates"] + network_videos + live_candidates:
            candidate_url = candidate.get("url")
            if not candidate_url:
                continue
            if urlparse(candidate_url).scheme.lower() not in {"http", "https"}:
                continue
            existing = candidates_by_url.get(candidate_url)
            if existing is None or candidate.get("provenance") in {"main_product_dom", "main_product_live_explanation"}:
                candidates_by_url[candidate_url] = candidate

        if source.get("video_url"):
            chosen = {
                "url": require_http_url(str(source["video_url"])),
                "duration_sec": None,
                "provenance": "human_selected",
                "selection_score": 100,
            }
            ranked = [chosen, *candidates_by_url.values()]
            video_issue = None
        else:
            chosen, ranked, video_issue = choose_video(
                list(candidates_by_url.values()), product_id, args.min_video_score
            )

        detail_image_records = []
        image_ocr = []
        if not args.no_download:
            image_dir = output_dir / "detail_images"
            image_dir.mkdir(exist_ok=True)
            seen_image_urls = set()
            for image in extracted.get("detail_images", []):
                image_url = str(image.get("url") or "")
                if not image_url or image_url in seen_image_urls:
                    continue
                if urlparse(image_url).scheme.lower() not in {"http", "https"}:
                    continue
                seen_image_urls.add(image_url)
                if len(detail_image_records) >= args.max_detail_images:
                    break
                suffix = Path(urlparse(image_url).path).suffix.lower()
                if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
                    suffix = ".jpg"
                image_path = image_dir / f"detail_{len(detail_image_records):04d}{suffix}"
                try:
                    image_cookies = await context.cookies([image_url])
                    await asyncio.to_thread(
                        _download_http, image_url, image_path, canonical_url, image_cookies
                    )
                    item = {
                        "local_path": str(image_path.resolve()),
                        "sha256": sha256_file(image_path),
                        "alt": image.get("alt"),
                        "source_url_redacted": redact_url(image_url),
                    }
                    detail_image_records.append(item)
                    if args.page_ocr is not None:
                        result = await asyncio.to_thread(args.page_ocr, str(image_path))
                        texts = getattr(result, "txts", None) if result is not None else None
                        scores = getattr(result, "scores", None) if result is not None else None
                        scores = scores or [1.0] * len(texts or [])
                        image_ocr.extend(
                            str(text).strip() for text, score in zip(texts or [], scores)
                            if float(score) >= 0.55 and str(text).strip()
                        )
                except Exception as exc:
                    image_path.unlink(missing_ok=True)
                    detail_image_records.append({
                        "source_url_redacted": redact_url(image_url),
                        "status": "download_error",
                        "error_type": type(exc).__name__,
                    })
        if image_ocr:
            image_ocr = list(dict.fromkeys(image_ocr))[:500]
            extracted["page_facts"]["detail_image_ocr"] = image_ocr
            parsed = parse_attributes_text("\n".join(image_ocr))
            extracted["page_facts"].setdefault("attributes", {}).update(
                {key: value for key, value in parsed.items() if key not in extracted["page_facts"]["attributes"]}
            )

        record.update({
            "canonical_url": redact_page_url(canonical_url),
            "http_status": navigation.status if navigation is not None else None,
            "source_product_id": product_id,
            "product_group_key": group_key,
            "page_facts": extracted["page_facts"],
            "detail_images": detail_image_records,
            "snapshot": {
                "html": str(html_path.resolve()),
                "html_sha256": sha256_file(html_path),
                "screenshot": str(screenshot_path.resolve()),
                "screenshot_sha256": sha256_file(screenshot_path),
                "user_agent": await page.evaluate("navigator.userAgent"),
            },
            "video_candidates": [
                {
                    **{key: value for key, value in x.items() if key != "url"},
                    "url_for_review": redact_url(str(x.get("url", ""))),
                }
                for x in ranked
            ],
            "status": "needs_video_review" if video_issue else "collected",
            "review_reason": video_issue,
        })

        if chosen and not args.no_download:
            suffix = ".mp4"
            local_video = output_dir / f"product_video{suffix}"
            cookies = await context.cookies([chosen["url"]])
            if ".m3u8" in chosen["url"].split("?", 1)[0].lower():
                await asyncio.to_thread(_download_hls, chosen["url"], local_video, canonical_url, cookies)
            else:
                await asyncio.to_thread(_download_http, chosen["url"], local_video, canonical_url, cookies)
            record["selected_video"] = {
                "local_path": str(local_video.resolve()),
                "sha256": sha256_file(local_video),
                "bytes": local_video.stat().st_size,
                "provenance": chosen["provenance"],
                "selection_score": chosen["selection_score"],
                "association": chosen.get("association"),
                "source_url_redacted": redact_url(chosen["url"]),
            }
        elif chosen:
            record["selected_video"] = {
                "local_path": None,
                "sha256": None,
                "provenance": chosen["provenance"],
                "selection_score": chosen["selection_score"],
                "source_url_redacted": redact_url(chosen["url"]),
            }
        (output_dir / "record.json").write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return record
    except Exception as exc:
        error = str(exc).replace(url, redact_page_url(url))
        record.update({"status": "error", "error_type": type(exc).__name__, "error": error})
        return record
    finally:
        if ui:
            ui.message = "该商品处理已结束；批量任务将继续下一个商品。"
        for popup in popup_pages:
            if not popup.is_closed():
                await popup.close()
        await page.close()


async def run(args) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise SystemExit(
            "缺少 playwright；请安装 data_tools/requirements.txt 并执行 playwright install chromium"
        ) from exc

    sources = list(read_jsonl(args.input)) if args.input else []
    sources.extend({"url": url, "rights_basis": args.rights_basis} for url in args.url)
    if not sources:
        raise SystemExit("至少提供 --input 或 --url")
    for source in sources:
        if not source.get("url"):
            raise ValueError("each input row requires url")
        source["rights_basis"] = source.get("rights_basis") or args.rights_basis

    output_root = Path(args.output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    args.page_ocr = None
    if args.ocr_detail_images:
        try:
            from rapidocr import RapidOCR
        except ImportError as exc:
            raise SystemExit("--ocr-detail-images requires rapidocr") from exc
        args.page_ocr = RapidOCR(params={"Rec.lang_type": "ch", "Global.text_score": 0.55})
    records: list[dict[str, Any]] = []
    args.browser_ui = BrowserUI(args.login_ui_port) if args.login_ui else None
    async with async_playwright() as playwright:
        launch = {"headless": not args.headed}
        if args.browser_channel != "chromium":
            launch["channel"] = args.browser_channel
        if args.profile_dir:
            context = await playwright.chromium.launch_persistent_context(
                str(Path(args.profile_dir).resolve()), viewport={"width": 1280, "height": 900},
                locale="zh-CN", **launch
            )
            browser = None
        else:
            browser = await playwright.chromium.launch(**launch)
            context = await browser.new_context(locale="zh-CN", viewport={"width": 1280, "height": 900})
        try:
            if args.browser_ui:
                await args.browser_ui.start()
            for index, source in enumerate(sources, 1):
                print(f"[{index}/{len(sources)}] {redact_page_url(str(source['url']))}")
                records.append(await collect_one(context, source, output_root, args))
                write_jsonl(args.output_jsonl, records)
        finally:
            if args.browser_ui:
                await args.browser_ui.close()
            await context.close()
            if browser:
                await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser(formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    parser.add_argument("--input", help="URL JSONL")
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--output-jsonl", required=True)
    parser.add_argument("--profile-dir", help="可选的独立浏览器 profile；可能含敏感登录状态，勿提交 Git")
    parser.add_argument("--browser-channel", choices=("chromium", "chrome"), default="chromium")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--interactive", action="store_true", help="等待人工登录并展开商品详情")
    parser.add_argument("--login-ui", action="store_true", help="本机 HTTP 操作界面；服务器通过 SSH 端口转发访问")
    parser.add_argument("--login-ui-port", type=int, default=8765)
    parser.add_argument("--login-timeout-sec", type=int, default=1800)
    parser.add_argument("--login-attempts", type=int, default=3)
    parser.add_argument("--inspect-media", action="store_true", help="商品页可用后等待人工查看/点击视频入口")
    parser.add_argument("--settle-ms", type=int, default=5000)
    parser.add_argument("--scroll-steps", type=int, default=5, help="触发主详情区域的懒加载；0 表示禁用")
    parser.add_argument("--navigation-timeout-ms", type=int, default=60000)
    parser.add_argument("--min-video-score", type=int, default=6)
    parser.add_argument("--no-download", action="store_true")
    parser.add_argument("--max-detail-images", type=int, default=80)
    parser.add_argument("--ocr-detail-images", action="store_true", help="对主详情图片运行 OCR，结果仍只作为弱监督")
    parser.add_argument("--rights-basis", default="user_authorized_research")
    args = parser.parse_args()
    if args.login_ui and not args.profile_dir:
        args.profile_dir = "profiles/p2-collector"
    if args.interactive and not (args.headed or args.login_ui):
        parser.error("--interactive requires --headed or --login-ui")
    if args.inspect_media and not (args.login_ui or (args.headed and args.interactive)):
        parser.error("--inspect-media requires --login-ui or --headed --interactive")
    if not 1 <= args.login_ui_port <= 65535 or args.login_timeout_sec <= 0 or args.login_attempts <= 0:
        parser.error("invalid login UI port, timeout, or attempts")
    if args.settle_ms < 0 or args.scroll_steps < 0 or args.max_detail_images < 0:
        parser.error("settle/scroll/max-detail values must be non-negative")
    if args.navigation_timeout_ms <= 0:
        parser.error("--navigation-timeout-ms must be positive")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
