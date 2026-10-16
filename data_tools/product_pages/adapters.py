from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .schema import normalize_price_cent, normalize_text, product_id_from_url


PLATFORM_HOSTS = {
    "taobao": ("taobao.com", "tmall.com", "tb.cn"),
    "jd": ("jd.com", "3.cn"),
}


def detect_platform(url: str) -> str | None:
    host = (urlparse(url).hostname or "").lower()
    for platform, suffixes in PLATFORM_HOSTS.items():
        if any(host == suffix or host.endswith("." + suffix) for suffix in suffixes):
            return platform
    return None


@dataclass(frozen=True)
class Selectors:
    title: tuple[str, ...]
    detail_roots: tuple[str, ...]
    media_roots: tuple[str, ...]
    attributes: tuple[str, ...]
    price: tuple[str, ...]
    excluded_ancestors: tuple[str, ...]


class ProductPageAdapter:
    platform = "generic"
    selectors = Selectors(
        title=("h1", "meta[property='og:title']"),
        detail_roots=("main", "[itemtype*='Product']"),
        media_roots=("main", "[itemtype*='Product']"),
        attributes=("[class*='parameter']", "[class*='attribute']"),
        price=("[class*='price']", "meta[property='product:price:amount']"),
        excluded_ancestors=("[class*='recommend']", "[class*='guess']", "[class*='similar']"),
    )

    def product_id(self, url: str) -> str | None:
        return product_id_from_url(url, self.platform)

    async def extract(self, page: Any) -> dict[str, Any]:
        """Extract only scoped main-product DOM regions, never the whole body."""
        product_id = self.product_id(page.url)
        payload = await page.evaluate(
            r"""(cfg) => {
              const firstText = (selectors) => {
                for (const sel of selectors) {
                  const node = document.querySelector(sel);
                  if (!node) continue;
                  const value = node.content || node.innerText || node.textContent || '';
                  if (value.trim()) return value.trim();
                }
                return null;
              };
              const excluded = (node) => cfg.excluded.some(sel => node.closest(sel));
              const roots = cfg.roots.flatMap(sel => Array.from(document.querySelectorAll(sel)))
                .filter(node => !excluded(node));
              const uniqueRoots = Array.from(new Set(roots));
              const mediaRoots = cfg.mediaRoots.flatMap(sel => Array.from(document.querySelectorAll(sel)))
                .filter(node => !excluded(node));
              const cleanText = (root) => {
                const clone = root.cloneNode(true);
                for (const sel of cfg.excluded) {
                  for (const node of clone.querySelectorAll(sel)) node.remove();
                }
                return clone.innerText || clone.textContent || '';
              };
              const detailText = uniqueRoots.map(cleanText).join('\n').trim();
              const attrText = cfg.attributes.flatMap(sel => Array.from(document.querySelectorAll(sel)))
                .filter(node => !excluded(node))
                .map(n => n.innerText || '').join('\n').trim();
              const videos = [];
              const mediaEntries = [];
              const detailImages = [];
              const pointsToOtherProduct = (node) => {
                const href = node.closest('a[href]')?.href || '';
                if (!href) return false;
                const link = new URL(href, location.href);
                const isItemPage = /(^|\.)taobao\.com$|(^|\.)tmall\.com$/.test(link.hostname)
                  && /\/item\.htm$/.test(link.pathname);
                const isJdItem = /^item(?:\.m)?\.jd\.com$/.test(link.hostname);
                if (!isItemPage && !isJdItem) return false;
                const match = href.match(/[?&](?:id|itemId|item_id)=(\d+)/) || href.match(/\/(\d{5,})\.html/);
                return Boolean(match && cfg.productId && match[1] !== String(cfg.productId));
              };
              for (const root of uniqueRoots) {
                for (const node of root.querySelectorAll('img')) {
                  if (excluded(node) || pointsToOtherProduct(node)) continue;
                  const src = node.getAttribute('data-src') || node.getAttribute('data-lazyload') ||
                    node.getAttribute('data-ks-lazyload') || node.currentSrc || node.src;
                  if (!src || src.startsWith('data:')) continue;
                  detailImages.push({
                    url: new URL(src, location.href).href,
                    alt: (node.alt || '').trim(),
                    provenance: 'main_product_detail'
                  });
                }
              }
              for (const root of Array.from(new Set(mediaRoots))) {
                for (const node of root.querySelectorAll('video, video source')) {
                  if (excluded(node) || pointsToOtherProduct(node)) continue;
                  const video = node.tagName === 'VIDEO' ? node : node.closest('video');
                  const src = node.currentSrc || node.src || node.getAttribute('src');
                  if (!src) continue;
                  videos.push({
                    url: new URL(src, location.href).href,
                    duration_sec: Number.isFinite(video?.duration) ? video.duration : null,
                    provenance: 'main_product_dom'
                  });
                  mediaEntries.push({kind: src.startsWith('blob:') ? 'blob_player' : 'video_element', url: src, provenance: 'main_product_dom'});
                }
                for (const node of root.querySelectorAll('a[href], iframe[src]')) {
                  if (excluded(node) || pointsToOtherProduct(node)) continue;
                  const src = node.href || node.src;
                  if (!src || !/^https?:/i.test(src)) continue;
                  const label = (node.innerText || node.title || node.getAttribute('aria-label') || '').trim().slice(0, 160);
                  const direct = /\.(mp4|m3u8|mov|webm)(?:[?#]|$)/i.test(src);
                  if (direct) {
                    videos.push({url:src, duration_sec:null, provenance:'main_product_dom'});
                  }
                  if (direct || node.tagName === 'IFRAME' || /video|live|play|视频|讲解|回放|直播/i.test(src + ' ' + label)) {
                    mediaEntries.push({kind: direct ? 'direct_media_link' : node.tagName === 'IFRAME' ? 'embedded_frame' : 'player_page_link', url:src, label, provenance:'main_product_dom'});
                  }
                }
              }
              const jsonld = Array.from(document.querySelectorAll('script[type="application/ld+json"]'))
                .map(n => n.textContent || '').filter(Boolean);
              return {
                canonical_url: document.querySelector('link[rel="canonical"]')?.href || location.href,
                title: firstText(cfg.title),
                detail_text: detailText,
                attribute_text: attrText,
                price_text: firstText(cfg.price),
                videos,
                media_entries: mediaEntries,
                detail_images: detailImages,
                jsonld
              };
            }""",
            {
                "title": list(self.selectors.title),
                "roots": list(self.selectors.detail_roots),
                "mediaRoots": list(self.selectors.media_roots),
                "attributes": list(self.selectors.attributes),
                "price": list(self.selectors.price),
                "excluded": list(self.selectors.excluded_ancestors),
                "productId": product_id,
            },
        )
        facts = self._facts_from_payload(payload, product_id)
        return {
            "page_facts": facts,
            "dom_video_candidates": payload.get("videos") or [],
            "detail_images": payload.get("detail_images") or [],
            "media_entries": payload.get("media_entries") or [],
        }

    def _facts_from_payload(
        self, payload: dict[str, Any], product_id: str | None = None
    ) -> dict[str, Any]:
        product = _first_product_jsonld(payload.get("jsonld") or [], product_id)
        title = normalize_text(product.get("name")) or normalize_text(payload.get("title"))
        description = normalize_text(product.get("description")) or normalize_text(payload.get("detail_text"))
        brand = product.get("brand")
        if isinstance(brand, dict):
            brand = brand.get("name")
        offers = product.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        offers = offers if isinstance(offers, dict) else {}
        price = offers.get("price") or payload.get("price_text")
        return {
            "title": title,
            "description": description,
            "category": normalize_text(product.get("category")),
            "brand": normalize_text(brand),
            "color": normalize_text(product.get("color")),
            "attributes_text": normalize_text(payload.get("attribute_text")),
            "attributes": parse_attributes_text(payload.get("attribute_text")),
            "page_price_cent": normalize_price_cent(price),
            "currency": normalize_text(offers.get("priceCurrency")) or "CNY",
        }


class TaobaoAdapter(ProductPageAdapter):
    platform = "taobao"
    selectors = Selectors(
        title=(
            "[class*='ItemTitle--'] [class*='mainTitle--']",
            "[class*='mainTitle--']",
            "[class*='ItemTitle']",
            "meta[property='og:title']",
            "h1:not([class*='comment']):not([class*='Comment'])",
        ),
        detail_roots=(
            "[class*='imageTextInfo--']",
            "#detail",
            "[class*='ItemDetail']",
            "[class*='DetailContent']",
            "[class*='desc-root']",
        ),
        media_roots=(
            "[class*='PicGallery']", "[class*='MainPic']", "[class*='mainPic']",
            "[class*='VideoPlayer']", "[class*='videoPlayer']", ".tb-gallery",
        ),
        attributes=(".paramsInfoArea", "[class*='Attrs']", "[class*='attributes']"),
        price=("[class*='Price--']", "[class*='priceText']", "meta[property='product:price:amount']"),
        excluded_ancestors=(
            "[class*='recommend']", "[class*='Recommend']", "[class*='guess']",
            "[class*='Guess']", "[class*='similar']", "[class*='hotSell']",
        ),
    )

    async def prepare_details(self, page):
        for label in ("参数信息", "图文详情"):
            tab = page.locator('[class*="tabTitle--"]').filter(has_text=label)
            if await tab.count():
                await tab.first.click(timeout=5000)
                await page.wait_for_timeout(600)

    async def extract(self, page):
        result = await super().extract(page)
        extra = await page.evaluate("""productId => {
          const item=window.__ICE_APP_CONTEXT__?.loaderData?.home?.data?.res?.item;
          const attributes={};
          for(const node of document.querySelectorAll('.paramsInfoArea [class*="emphasisParamsInfoItem--"]')) {
            const key=node.querySelector('[class*="emphasisParamsInfoItemSubTitle--"]')?.textContent?.trim();
            const value=node.querySelector('[class*="emphasisParamsInfoItemTitle--"]')?.textContent?.trim();
            if(key && value) attributes[key]=value;
          }
          for(const node of document.querySelectorAll('.paramsInfoArea [class*="generalParamsInfoItem--"]')) {
            const key=node.querySelector('[class*="generalParamsInfoItemTitle--"]')?.textContent?.trim();
            const value=node.querySelector('[class*="generalParamsInfoItemSubTitle--"]')?.textContent?.trim();
            if(key && value) attributes[key]=value;
          }
          return {title:item && String(item.itemId)===String(productId)?item.title:null,attributes};
        }""", self.product_id(page.url))
        facts = result["page_facts"]
        if extra["title"]:
            facts["title"] = normalize_text(extra["title"])
        if facts["title"] and re.match(r"^(用户评价|商品评价|问大家)", facts["title"]):
            facts["title"] = None
        facts["attributes"].update(extra["attributes"])
        detail_text = facts["description"]
        parameter_text = "\n".join(f"{key}：{value}" for key, value in extra["attributes"].items())
        facts["description"] = "\n".join(x for x in (detail_text, parameter_text) if x) or None
        facts["description_sources"] = (
            (["main_product_detail_text"] if detail_text else [])
            + (["main_product_parameters"] if parameter_text else [])
        )
        facts["detail_image_count"] = len({x["url"] for x in result["detail_images"]})
        facts["brand"] = facts["brand"] or extra["attributes"].get("品牌")
        return result

    async def discover_live_video(self, page, timeout_ms=12000):
        from .live_video import discover_live_video
        facts = (await self.extract(page))["page_facts"]
        return await discover_live_video(page, self.product_id(page.url), facts["title"], timeout_ms)


class JdAdapter(ProductPageAdapter):
    platform = "jd"
    selectors = Selectors(
        title=(".sku-name", "h1", "meta[property='og:title']"),
        detail_roots=("#detail", ".detail-content", ".Ptable", "#product-detail"),
        media_roots=(".preview-wrap", "#spec-list", ".product-intro", ".J-video", "#video-player"),
        attributes=("#parameter2", ".p-parameter", ".Ptable-item"),
        price=(".p-price .price", "[class*='price']", "meta[property='product:price:amount']"),
        excluded_ancestors=(
            "#recommend", "[class*='recommend']", "[class*='猜你喜欢']",
            "[class*='similar']", "[class*='may-like']",
        ),
    )


def adapter_for_url(url: str, platform: str | None = None) -> ProductPageAdapter:
    detected = detect_platform(url)
    if detected is None:
        raise ValueError(f"unsupported product-page host: {urlparse(url).hostname or url}")
    if platform and platform != detected:
        raise ValueError(f"URL belongs to {detected}, not requested platform {platform}")
    actual = detected
    if actual == "taobao":
        return TaobaoAdapter()
    if actual == "jd":
        return JdAdapter()
    raise ValueError(f"unsupported product-page platform: {actual or url}")


def _first_product_jsonld(
    raw_blocks: list[str], product_id: str | None = None
) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for raw in raw_blocks:
        try:
            value = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        queue = value if isinstance(value, list) else [value]
        while queue:
            item = queue.pop(0)
            if not isinstance(item, dict):
                continue
            graph = item.get("@graph")
            if isinstance(graph, list):
                queue.extend(graph)
            kind = item.get("@type")
            kinds = kind if isinstance(kind, list) else [kind]
            if any(str(x).lower() == "product" for x in kinds):
                candidates.append(item)
    if not candidates:
        return {}
    if product_id:
        for candidate in candidates:
            identities = (
                candidate.get("sku"), candidate.get("productID"),
                candidate.get("@id"), candidate.get("url"),
            )
            if any(product_id in str(value) for value in identities if value is not None):
                return candidate
    return candidates[0]


def parse_attributes_text(text: Any) -> dict[str, str]:
    """Conservatively parse visible ``key: value`` lines from the main detail."""
    result = {}
    for raw_line in str(text or "").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        match = re.match(r"^([^:：]{1,32})\s*[:：]\s*(.{1,160})$", line)
        if not match:
            continue
        key, value = match.group(1).strip(), match.group(2).strip()
        if key and value and key not in result:
            result[key] = value
        if len(result) >= 80:
            break
    return result


def is_probable_video_url(url: str, content_type: str = "") -> bool:
    clean = url.split("?", 1)[0].lower()
    return (
        clean.endswith((".mp4", ".m3u8", ".mov", ".webm"))
        or content_type.lower().startswith("video/")
        or "mpegurl" in content_type.lower()
    )


def score_video_candidate(candidate: dict[str, Any], product_id: str | None) -> int:
    """Conservative auto-selection score; uncertain network videos stay reviewable."""
    score = 0
    if candidate.get("provenance") == "main_product_dom":
        score += 6
    association = candidate.get("association") or {}
    if (
        candidate.get("provenance") == "main_product_live_explanation"
        and association.get("source_product_id") == product_id
        and association.get("selected_title_matches") is True
        and association.get("request_started_after_activation") is True
        and association.get("hls_count") == 1
        and association.get("request_frame") == "main"
        and association.get("player_kind") in {"canvas", "video"}
    ):
        score += 8
    if product_id and product_id in str(candidate.get("url", "")):
        score += 3
    duration = candidate.get("duration_sec")
    if isinstance(duration, (int, float)) and 30 <= duration <= 600:
        score += 2
    if str(candidate.get("url", "")).lower().split("?", 1)[0].endswith(".mp4"):
        score += 1
    return score
