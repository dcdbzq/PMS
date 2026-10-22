"""日常使用缺口的浏览器验收：管理页面、填错提示、双窗口覆盖、会话中途失效。

数据写在临时库。需要 Playwright 与 Chrome。本机有 Edge 时额外用 Edge 打开填报页。

    python tests/browser_daily.py
"""
import sys
import tempfile
import threading
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import Handler, Server, current_week
from feishu import FeishuConfig
from playwright.sync_api import sync_playwright, expect

OUTPUT = Path(__file__).resolve().parents[1] / "test-results"
ADMIN = ("林序", "admin_daily", "Admin-password-123")
MEMBER = ("周予安", "zhouyuan", "Member-password-123")
RESET = "Reset-password-123"


def login(page, origin, username, password, name=None):
    page.goto(origin + "/")
    expect(page.locator("#auth-form")).to_be_visible()
    if name:
        page.locator("#auth-name").fill(name)
    page.locator("#auth-username").fill(username)
    page.locator("#auth-password").fill(password)
    page.locator("#auth-form button[type=submit]").click()
    expect(page.locator(".metrics")).to_be_visible()


def open_report(page):
    page.locator('nav a[href="#report"]').click()
    expect(page.locator("#report-form")).to_be_visible()


def fill_report(page, progress, note):
    page.locator('[data-field="progress"]').fill(str(progress))
    page.locator('[data-field="effort"]').fill("100")
    page.locator('[data-field="note"]').fill(note)
    page.locator('[data-field="next_plan"]').fill("继续推进下周工作")
    expect(page.locator("#submit-report")).to_be_enabled()


def watch(page, errors, label):
    page.on("pageerror", lambda error: errors.append(f"{label}: {error}"))
    page.on("dialog", lambda dialog: dialog.accept())


def main():
    OUTPUT.mkdir(exist_ok=True)
    checks = []
    with tempfile.TemporaryDirectory() as temp, patch.object(Handler, "log_message", lambda *_: None):
        server = Server(("127.0.0.1", 0), Path(temp) / "daily.sqlite3", FeishuConfig())
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        origin = f"http://127.0.0.1:{server.server_port}"
        oversized = Path(temp) / "too-big.bin"
        oversized.write_bytes(b"x" * (20 * 1024 * 1024 + 1))
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(channel="chrome")
                errors = []
                admin = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN").new_page()
                watch(admin, errors, "admin")
                login(admin, origin, ADMIN[1], ADMIN[2], ADMIN[0])

                admin.locator('nav a[href="#members"]').click()
                admin.locator('[data-action="new-member"]').click()
                admin.locator("#u-name").fill(MEMBER[0])
                admin.locator("#u-dept").fill("研发团队")
                admin.locator("#u-username").fill(MEMBER[1])
                admin.locator("#u-password").fill(MEMBER[2])
                admin.locator("#member-form button[type=submit]").click()
                expect(admin.locator(".data-table")).to_contain_text(MEMBER[0])
                admin.locator('[data-action="new-member"]').click()
                admin.locator("#u-name").fill("重名成员")
                admin.locator("#u-username").fill(MEMBER[1])
                admin.locator("#u-password").fill(MEMBER[2])
                admin.locator("#member-form button[type=submit]").click()
                expect(admin.locator("#member-form .inline-error")).to_contain_text("登录名")
                admin.locator('.icon-button[data-action="close-modal"]').click()
                expect(admin.locator(".data-table tbody tr")).to_have_count(2)
                checks.append("重复登录名在页面上提示，并且没有建成第二个账号")

                admin.locator('nav a[href="#projects"]').click()
                admin.locator('[data-action="new-project"]').first.click()
                admin.locator("#p-name").fill("客户检索项目")
                admin.locator("#p-owner").select_option(label=MEMBER[0])
                admin.locator("#p-start").fill("2026-09-01")
                admin.locator("#p-due").fill("2026-12-31")
                admin.locator("#project-form button[type=submit]").click()
                expect(admin.locator(".root-project")).to_contain_text("客户检索项目")
                admin.locator('[data-action="new-project"]').first.click()
                admin.locator("#p-name").fill("待归档项目")
                admin.locator("#p-start").fill("2026-09-01")
                admin.locator("#p-due").fill("2026-12-31")
                admin.locator('input[name="member_ids"][value="2"]').check()
                admin.locator("#project-form button[type=submit]").click()
                expect(admin.locator(".root-project")).to_have_count(2)
                admin.locator("#project-search").fill(MEMBER[0])
                expect(admin.locator(".root-project")).to_have_count(1)
                expect(admin.locator(".root-project")).to_contain_text("客户检索项目")
                admin.locator("#project-search").fill("没有这个负责人")
                expect(admin.locator("#project-list")).to_contain_text("暂无匹配项目")
                admin.locator("#project-search").fill("")
                expect(admin.locator(".root-project")).to_have_count(2)
                checks.append("按负责人姓名搜索项目")

                member = browser.new_context(viewport={"width": 1366, "height": 900}, locale="zh-CN").new_page()
                watch(member, errors, "member")
                login(member, origin, MEMBER[1], MEMBER[2])
                open_report(member)
                member.locator('[data-action="add-row"]').click()
                member.locator('[data-action="choose-project"][data-id="1"]').click()
                expect(member.locator(".report-card")).to_be_visible()
                member.locator('[data-field="effort"]').fill("40")
                expect(member.locator("#submit-report")).to_be_disabled()
                expect(member.locator("#effort-total")).to_contain_text("还差 60%")
                member.locator('[data-field="effort"]').fill("100")
                expect(member.locator("#submit-report")).to_be_enabled()
                future = (date.fromisoformat(current_week()) + timedelta(days=7)).isoformat()
                member.locator("#report-date").evaluate(
                    "(input, value) => { input.value = value; input.dispatchEvent(new Event('change', {bubbles: true})); }",
                    future)
                expect(member.locator(".toast.error")).to_contain_text("只能填写本周或之前的进展")
                expect(member.locator("#report-date")).to_have_value(current_week())
                member.locator(".attachment-input").set_input_files(oversized)
                expect(member.locator(".toast.error")).to_contain_text("每个文件最大 20 MB")
                expect(member.locator(".attachment-list")).to_have_count(0)
                checks.append("投入不足、未来周和超大附件都在提交前被拦住")

                fill_report(member, 21, "窗口甲的说明")
                member.locator("#submit-report").click()
                expect(member.locator("#draft-status")).to_contain_text("最近提交于")
                other = browser.new_context(viewport={"width": 1280, "height": 900}, locale="zh-CN").new_page()
                watch(other, errors, "other")
                login(other, origin, MEMBER[1], MEMBER[2])
                open_report(other)
                expect(other.locator('[data-field="note"]')).to_have_value("窗口甲的说明")
                fill_report(other, 67, "窗口乙的说明")
                expect(other.locator("dialog")).to_have_count(0)
                other.locator("#submit-report").click()
                expect(other.locator("#draft-status")).to_contain_text("最近提交于")
                fresh = browser.new_context(locale="zh-CN").new_page()
                watch(fresh, errors, "fresh")
                login(fresh, origin, MEMBER[1], MEMBER[2])
                open_report(fresh)
                expect(fresh.locator(".report-card")).to_have_count(1)
                expect(fresh.locator('[data-field="note"]')).to_have_value("窗口乙的说明")
                expect(fresh.locator('[data-field="progress"]')).to_have_value("67")
                checks.append("同一周两个窗口后保存覆盖先保存，没有冲突提示，也没有两条记录")

                admin.locator('.root-project').filter(has_text='待归档项目').locator('.project-tree-actions [data-action="detail"]').click()
                admin.locator('dialog [data-action="edit-project"]').click()
                admin.locator('input[name="archived"]').check()
                admin.locator("#project-form button[type=submit]").click()
                expect(admin.locator("dialog")).to_have_count(0)
                admin.locator('[data-action="filter"][data-value="active"]').click()
                expect(admin.locator(".root-project")).to_have_count(1)
                admin.locator('[data-action="filter"][data-value="archived"]').click()
                expect(admin.locator(".root-project")).to_contain_text("已归档")
                fresh.locator('[data-action="refresh"]').click()
                open_report(fresh)
                expect(fresh.locator('[data-action="add-row"]')).to_have_count(0)
                admin.locator('.project-tree-actions [data-action="detail"]').click()
                admin.locator('dialog [data-action="edit-project"]').click()
                admin.locator('input[name="archived"]').uncheck()
                admin.locator("#project-form button[type=submit]").click()
                expect(admin.locator("dialog")).to_have_count(0)
                admin.locator('[data-action="filter"][data-value="active"]').click()
                expect(admin.locator(".root-project")).to_have_count(2)
                fresh.locator('[data-action="refresh"]').click()
                open_report(fresh)
                fresh.locator('[data-action="add-row"]').click()
                expect(fresh.locator("dialog")).to_contain_text("待归档项目")
                fresh.locator('.icon-button[data-action="close-modal"]').click()
                checks.append("页面上归档后成员不能新填该项目，重新开启后可以再选")

                open_report(fresh)
                fresh.locator('[data-field="note"]').fill("重置后不应出现")
                fresh.locator('[data-field="progress"]').fill("99")
                admin.locator('nav a[href="#members"]').click()
                admin.locator('[data-action="edit-member"][data-id="2"]').click()
                admin.locator("#u-password").fill(RESET)
                admin.locator("#member-form button[type=submit]").click()
                expect(admin.locator("dialog")).to_have_count(0)
                fresh.locator("#submit-report").click()
                expect(fresh.locator("#auth-form")).to_be_visible()
                fresh.locator("#auth-username").fill(MEMBER[1])
                fresh.locator("#auth-password").fill(MEMBER[2])
                fresh.locator("#auth-form button[type=submit]").click()
                expect(fresh.locator("#auth-error")).to_contain_text("登录名或密码不正确")
                login(fresh, origin, MEMBER[1], RESET)
                fresh.evaluate("localStorage.clear()")
                fresh.reload()
                expect(fresh.locator(".metrics")).to_be_visible()
                open_report(fresh)
                expect(fresh.locator('[data-field="note"]')).to_have_value("窗口乙的说明")
                expect(fresh.locator('[data-field="progress"]')).to_have_value("67")
                checks.append("填到一半被重置密码后回到登录页，未提交的内容没有写入")

                admin.screenshot(path=str(OUTPUT / "daily-admin-projects.png"), full_page=True)
                fresh.screenshot(path=str(OUTPUT / "daily-report-after-reset.png"), full_page=True)
                assert not errors, errors
                browser.close()
                run_edge(playwright, origin, checks)
                print("Daily browser acceptance passed: " + "；".join(checks))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


def run_edge(playwright, origin, checks):
    try:
        browser = playwright.chromium.launch(channel="msedge")
    except Exception as exc:
        print(f"Edge 未安装或无法启动，跳过：{exc}")
        return
    errors = []
    try:
        page = browser.new_context(viewport={"width": 390, "height": 844}, locale="zh-CN").new_page()
        watch(page, errors, "edge")
        login(page, origin, MEMBER[1], RESET)
        open_report(page)
        expect(page.locator('[data-field="note"]')).to_have_value("窗口乙的说明")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Edge 390px 宽度出现横向溢出"
        page.screenshot(path=str(OUTPUT / "daily-edge-mobile.png"), full_page=True)
        assert not errors, errors
        checks.append("Edge 窄屏可以登录并看到已保存的周报")
    finally:
        browser.close()


if __name__ == "__main__":
    main()
