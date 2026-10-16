"""Offline browser fixtures: python -m unittest discover -s tests -p test_product_page_login.py -v"""
import asyncio
import sys
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data_tools"))
from collect_product_pages import inspect_access, navigate_product, choose_video
from product_pages.adapters import TaobaoAdapter
from product_pages.browser_ui import BrowserUI

URL = "https://item.taobao.com/item.htm?id=123456"
PRODUCT = '<h1>测试商品</h1><div id="detail">材质：纯棉</div><div class="PicGallery"></div>'


class ManualConfirmation:
    def __init__(self):
        self.calls = 0

    def attach(self, page):
        self.page = page

    async def wait(self, message, timeout):
        self.calls += 1


class LoginBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        asyncio.get_running_loop().set_debug(False)
        from playwright.async_api import async_playwright
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch()
        self.page = await self.browser.new_page()
        self.adapter = TaobaoAdapter()

    async def asyncTearDown(self):
        await self.browser.close()
        await self.playwright.stop()

    def args(self, ui):
        return SimpleNamespace(browser_ui=ui, interactive=False, navigation_timeout_ms=30000,
                               settle_ms=0, login_attempts=2, login_timeout_sec=5)

    async def test_login_reloads_original_and_discards_stale_security_headers(self):
        visits = []

        async def route(request):
            visits.append(request.request.url)
            await request.fulfill(status=200, body=PRODUCT,
                                  headers={"bxpunish": "1"} if len(visits) == 1 else {})

        await self.page.route("**/*", route)
        ui = ManualConfirmation()
        response, reason = await navigate_product(self.page, URL, self.adapter, self.args(ui))
        self.assertIsNone(reason)
        self.assertEqual(visits, [URL, URL])
        self.assertEqual(ui.calls, 1)
        self.assertNotIn("bxpunish", response.headers)

    async def test_uncompleted_login_cannot_be_accepted_by_confirmation(self):
        await self.page.route("**/*", lambda r: r.fulfill(status=200, body=PRODUCT, headers={"bxpunish": "1"}))
        ui = ManualConfirmation()
        _, reason = await navigate_product(self.page, URL, self.adapter, self.args(ui))
        self.assertEqual(reason, "anti_bot_challenge")
        self.assertEqual(ui.calls, 2)

    async def test_valid_session_does_not_prompt(self):
        await self.page.route("**/*", lambda r: r.fulfill(status=200, body=PRODUCT))
        ui = ManualConfirmation()
        _, reason = await navigate_product(self.page, URL, self.adapter, self.args(ui))
        self.assertIsNone(reason)
        self.assertEqual(ui.calls, 0)

    async def test_visible_login_frame_blocks_extraction(self):
        async def route(r):
            body = PRODUCT + '<iframe src="https://login.taobao.com/login"></iframe>' if r.request.url == URL else 'login'
            await r.fulfill(status=200, body=body)
        await self.page.route("**/*", route)
        response = await self.page.goto(URL)
        self.assertEqual(await inspect_access(self.page, response, self.adapter, URL), "login_required")

    async def test_direct_media_links_player_links_and_recommendations(self):
        body = PRODUCT + '''<div class="PicGallery">
          <a href="https://media.example/product.mp4">商品视频</a>
          <a href="https://live.taobao.com/play.html?id=876543">直播回放</a>
          <div class="recommend"><a href="https://media.example/other.mp4">推荐视频</a></div>
          <a href="https://item.taobao.com/item.htm?id=999999">其他商品视频</a>
        </div>'''
        await self.page.route("**/*", lambda r: r.fulfill(status=200, body=body))
        await self.page.goto(URL)
        extracted = await self.adapter.extract(self.page)
        self.assertEqual({e['kind'] for e in extracted['media_entries']}, {'direct_media_link', 'player_page_link'})
        selected, _, issue = choose_video(extracted['dom_video_candidates'], '123456', 6)
        self.assertIsNone(issue)
        self.assertEqual(selected['url'], 'https://media.example/product.mp4')
        self.assertEqual(len(extracted['dom_video_candidates']), 1)

    async def test_taobao_main_details_exclude_review_heading_and_recommendations(self):
        body = '''<meta charset="utf-8"><h1>用户评价·700+</h1>
        <div class="ItemTitle--abc"><span class="mainTitle--xyz">测试阔腿裤</span></div>
        <div class="paramsInfoArea">
          <div class="emphasisParamsInfoItem--a"><span class="emphasisParamsInfoItemTitle--a">棉</span><span class="emphasisParamsInfoItemSubTitle--a">面料</span></div>
          <div class="generalParamsInfoItem--b"><span class="generalParamsInfoItemTitle--b">腰型</span><span class="generalParamsInfoItemSubTitle--b">高腰</span></div>
        </div>
        <div class="imageTextInfo--abc"><p>本商品洗护说明</p><img src="https://images.example/main.jpg">
          <div class="recommend"><p>其他商品广告</p><img src="https://images.example/other.jpg"></div>
        </div>'''
        await self.page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=body))
        await self.page.goto(URL)
        extracted = await self.adapter.extract(self.page)
        self.assertEqual(extracted["page_facts"]["title"], "测试阔腿裤")
        self.assertEqual(extracted["page_facts"]["attributes"]["面料"], "棉")
        self.assertEqual(extracted["page_facts"]["attributes"]["腰型"], "高腰")
        self.assertIn("本商品洗护说明", extracted["page_facts"]["description"])
        self.assertNotIn("其他商品", extracted["page_facts"]["description"])
        self.assertEqual([x["url"] for x in extracted["detail_images"]], ["https://images.example/main.jpg"])

    async def test_live_canvas_hls_is_bound_to_selected_main_product(self):
        await self._live_fixture("测试阔腿裤", False)
        discover = getattr(self.adapter, "discover_live_video", None)
        self.assertIsNotNone(discover, "main-product live-player association is missing")
        result = await discover(self.page, timeout_ms=1500)
        self.assertEqual(result["candidate"]["url"], "https://media.example/product.m3u8")
        self.assertEqual(result["candidate"]["provenance"], "main_product_live_explanation")
        chosen, _, issue = choose_video([result["candidate"]], "123456", 6)
        self.assertIsNone(issue)
        self.assertEqual(chosen["url"], "https://media.example/product.m3u8")

    async def test_detail_images_use_lazy_sources_instead_of_placeholders(self):
        body = '''<meta charset="utf-8"><div class="imageTextInfo--abc">
          <img src="https://assets.example/s.gif" data-src="https://images.example/real1.jpg">
          <img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" data-src="https://images.example/real2.jpg">
          <a href="https://item.taobao.com/item.htm?id=999999"><img data-src="https://images.example/other.jpg"></a>
        </div>'''
        await self.page.route("**/*", lambda r: r.fulfill(status=200, content_type="text/html; charset=utf-8", body=body))
        await self.page.goto(URL)
        extracted = await self.adapter.extract(self.page)
        self.assertEqual([x["url"] for x in extracted["detail_images"]], [
            "https://images.example/real1.jpg", "https://images.example/real2.jpg"
        ])

    async def test_live_player_other_product_cannot_be_selected(self):
        await self._live_fixture("其他商品", False)
        discover = getattr(self.adapter, "discover_live_video", None)
        self.assertIsNotNone(discover, "main-product live-player association is missing")
        result = await discover(self.page, timeout_ms=1500)
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["reason"], "live_selected_product_mismatch")

    async def test_multiple_hls_after_click_remain_ambiguous(self):
        await self._live_fixture("测试阔腿裤", True)
        discover = getattr(self.adapter, "discover_live_video", None)
        self.assertIsNotNone(discover, "main-product live-player association is missing")
        result = await discover(self.page, timeout_ms=1500)
        self.assertIsNone(result["candidate"])
        self.assertEqual(result["reason"], "ambiguous_live_hls")

    async def _live_fixture(self, selected_title, multiple):
        body = '''<meta charset="utf-8"><div class="ItemTitle--abc"><span class="mainTitle--abc">测试阔腿裤</span></div>
        <div id="detail"></div><div class="RightSideNav--abc"><button id="live">直播讲解</button></div>
        <script>document.querySelector('#live').onclick=async()=>{
          document.body.insertAdjacentHTML('beforeend','<div class="player-container--abc videox-container"><canvas></canvas></div><div class="card-selected--abc"><div class="cardTitle--abc">SELECTED</div></div>');
          await fetch('https://media.example/product.m3u8');
          EXTRA
        };</script>'''.replace("SELECTED", selected_title).replace("EXTRA", "await fetch('https://media.example/other.m3u8');" if multiple else "")
        async def route(r):
            if r.request.url.endswith(".m3u8"):
                await r.fulfill(status=200, content_type="application/x-mpegURL", body="#EXTM3U\n#EXT-X-ENDLIST\n", headers={"Access-Control-Allow-Origin": "*"})
            else:
                await r.fulfill(status=200, content_type="text/html", body=body)
        await self.page.route("**/*", route)
        await self.page.goto(URL)

    async def test_ui_user_can_click_type_and_resume(self):
        await self.page.set_content('<input id="field" style="margin:20px;width:300px;height:50px">')
        ui = BrowserUI(0)
        ui.attach(self.page)
        await ui.start()
        waiter = asyncio.create_task(ui.wait("TEST manual confirmation", 60))
        control = await self.browser.new_page()
        errors = []
        control.on("pageerror", lambda error: errors.append(str(error)))
        try:
            await control.goto(f"http://127.0.0.1:{ui.port}/#{ui.token}")
            # Old Playwright wait_for_function uses eval, prohibited by our CSP.
            # Poll the DOM attribute instead; keep CSP enabled for this test.
            for _ in range(50):
                if (await control.locator("#screen").get_attribute("src") or "").startswith("blob:"):
                    break
                await asyncio.sleep(0.1)
            self.assertTrue((await control.locator("#screen").get_attribute("src") or "").startswith("blob:"))
            await control.locator("#screen").click(position={"x": 50, "y": 50})
            await control.locator("#text").fill("manual-input-test")
            await control.locator("#send").click()
            for _ in range(50):
                if await self.page.locator("#field").input_value() == "manual-input-test":
                    break
                await asyncio.sleep(0.1)
            self.assertEqual(await self.page.locator("#field").input_value(), "manual-input-test")
            await control.locator("#go").click()
            await waiter
            self.assertEqual(errors, [])
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)
            await control.close()
            await ui.close()

    async def test_ui_requires_token_and_does_not_cache_frames(self):
        await self.page.set_content(PRODUCT)
        ui = BrowserUI(0)
        ui.attach(self.page)
        await ui.start()
        try:
            def fetch(path, token=None):
                request = urllib.request.Request(f'http://127.0.0.1:{ui.port}{path}',
                    headers={'Authorization': 'Bearer ' + token} if token else {})
                try:
                    with urllib.request.urlopen(request, timeout=10) as response:
                        return response.status, dict(response.headers), response.read()
                except urllib.error.HTTPError as error:
                    return error.code, {}, error.read()
            self.assertEqual((await asyncio.to_thread(fetch, '/frame'))[0], 403)
            self.assertEqual((await asyncio.to_thread(fetch, '/action', 'wrong'))[0], 403)
            status, headers, frame = await asyncio.to_thread(fetch, '/frame', ui.token)
            self.assertEqual(status, 200)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertTrue(frame.startswith(b'\xff\xd8'))
        finally:
            await ui.close()


if __name__ == '__main__':
    unittest.main()
