"""使用模拟飞书接口验收浏览器登录与通讯录页面，不触达真实企业。"""
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Handler, Server
from test_feishu import CONFIG, FakeClient
from playwright.sync_api import expect, sync_playwright


def main():
    output = Path(__file__).resolve().parents[1] / "test-results"
    output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory() as temp, patch.object(Handler, "log_message", lambda *_: None):
        client = FakeClient()
        server = Server(("127.0.0.1", 0), Path(temp) / "feishu-ui.sqlite3", CONFIG, client)
        server.feishu.sync()
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel="chrome")
                context = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                def fake_authorize(route):
                    query = parse_qs(urlparse(route.request.url).query)
                    assert query["code_challenge_method"] == ["S256"]
                    route.fulfill(status=302, headers={"Location": origin + "/api/auth/feishu/callback?code=admin-code&state=" + query["state"][0]})
                page.route("https://accounts.feishu.cn/**", fake_authorize)
                page.goto(origin)
                expect(page.get_by_role("link", name="使用飞书登录")).to_be_visible()
                expect(page.locator("#auth-password")).to_have_count(0)
                expect(page.locator("#auth-name")).to_have_count(0)
                page.screenshot(path=str(output / "feishu-login.png"), full_page=True)
                # Start through the real endpoint without following its external redirect.
                # Then open the authorize URL as a separately routed navigation so no real
                # Feishu page is contacted by Playwright's automatic redirect handling.
                start = page.request.get(origin + "/api/auth/feishu/start", max_redirects=0)
                assert start.status == 303
                page.goto(start.headers["location"])
                expect(page.locator(".metrics")).to_be_visible()
                page.locator('a[href="#members"]').click()
                expect(page.locator("h1")).to_have_text("团队通讯录")
                expect(page.locator(".data-table tbody tr")).to_have_count(2)
                expect(page.locator('[data-action="new-member"]')).to_have_count(0)
                expect(page.locator('[data-action="edit-member"]')).to_have_count(0)
                page.locator('[data-action="edit-role"][data-id="2"]').click()
                page.locator("#u-role").select_option("admin")
                page.locator("#role-form button[type=submit]").click()
                expect(page.locator("dialog")).to_have_count(0)
                client.people[1]["name"] = "周予安"
                page.locator('[data-action="sync-feishu"]').click()
                expect(page.locator(".data-table")).to_contain_text("周予安")
                page.screenshot(path=str(output / "feishu-directory.png"), full_page=True)
                client.fail = True
                page.locator('[data-action="sync-feishu"]').click()
                expect(page.locator(".directory-summary .inline-error")).to_contain_text("模拟分页中断")
                expect(page.locator(".data-table tbody tr")).to_have_count(2)
                client.fail = False
                page.locator('[data-action="sync-feishu"]').click()
                expect(page.locator(".directory-summary .inline-error")).to_have_count(0)
                page.set_viewport_size({"width": 390, "height": 844})
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.screenshot(path=str(output / "feishu-directory-mobile.png"), full_page=True)
                page.locator('[data-action="account"]').click()
                expect(page.locator('[data-action="change-password"]')).to_have_count(0)
                page.locator('[data-action="logout"]').click()
                expect(page.get_by_role("link", name="使用飞书登录")).to_be_visible()
                assert not errors, errors
                browser.close()
                print("Feishu browser acceptance passed: OAuth redirects/cookies, directory sync, readonly identity, roles, failure recovery, mobile, logout; no JavaScript errors.")
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == "__main__":
    main()
