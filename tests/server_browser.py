"""Read-only browser acceptance against the seeded server (no credentials in traces)."""
import argparse
import json
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

from server_acceptance import read_admin


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--admin-file", required=True)
    parser.add_argument("--accounts-file", required=True)
    parser.add_argument("--out", default="test-results/server-browser")
    parser.add_argument("--candidate-css", help="Preview local CSS without modifying the deployed server")
    args = parser.parse_args()
    admin = read_admin(args.admin_file)
    cohort = json.loads(Path(args.accounts_file).read_text(encoding="utf-8"))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    errors, checks, timings = [], [], []
    passed = False
    expect.set_options(timeout=30000)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")

        def login(account):
            ctx = browser.new_context(viewport={"width": 1440, "height": 1000}, locale="zh-CN")
            if args.candidate_css:
                ctx.route("**/styles.css", lambda route: route.fulfill(path=str(Path(args.candidate_css).resolve()), content_type="text/css"))
            page = ctx.new_page()
            page.set_default_timeout(30000)
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(cohort["url"])
            expect(page.locator("#auth-form")).to_be_visible()
            page.locator("#auth-username").fill(account["username"])
            page.locator("#auth-password").fill(account["password"])
            started = time.perf_counter()
            page.locator("#auth-form button[type=submit]").click()
            expect(page.locator(".metrics")).to_be_visible()
            timings.append({"role": account.get("role", "admin"), "login_to_dashboard_ms": round((time.perf_counter()-started)*1000, 2)})
            return ctx, page

        try:
            admin_context, page = login(admin)
            admin_state = page.request.get(cohort["url"]+"/api/state").json()
            assert set(cohort["project_ids"]) <= {p["id"] for p in admin_state["projects"]}
            expect(page.locator(".project-card")).to_have_count(min(4, len(admin_state["projects"])))
            checks.append("管理员总览显示项目摘要和半年历史")
            page.screenshot(path=str(out/"admin-overview.png"), full_page=True)
            page.locator('a[href="#members"]').click()
            expect(page.locator(".data-table tbody tr")).to_have_count(len(admin_state["users"]))
            with page.expect_download() as download:
                page.locator('a[href="/api/export"]').click()
            assert download.value.suggested_filename.endswith(".csv")
            page.screenshot(path=str(out/"admin-members.png"), full_page=True)
            checks.append("百人成员列表及CSV下载")
            page.locator('a[href="#projects"]').click()
            expect(page.locator(".project-card")).to_have_count(len(admin_state["projects"]))
            page.locator("#project-search").fill("协作项目01")
            expect(page.locator(".project-card")).to_have_count(1)
            page.locator(".project-card").click()
            expect(page.locator("dialog .history-item").first).to_be_visible()
            expect(page.locator('[data-action="edit-project"]')).to_be_visible()
            page.locator('[data-action="close-modal"]').click()
            checks.append("项目搜索、详细历史及管理员编辑入口")
            member_context, member = login(cohort["accounts"][1])
            expect(member.locator('a[href="#members"]')).to_have_count(0)
            expect(member.locator(".project-card")).to_have_count(3)
            checks.append("独立成员会话仅显示参与的3个项目")
            member.locator('a[href="#report"]').click()
            expect(member.locator(".report-card")).to_have_count(3)
            expect(member.locator(".attachment-list")).to_contain_text("验收证据.txt")
            with member.expect_download() as download:
                member.locator(".attachment-list a").first.click()
            assert download.value.suggested_filename == "验收证据.txt"
            member.locator('[data-action="report-prev"]').click()
            expect(member.locator("#draft-status")).to_contain_text("最近提交于")
            member.locator('[data-action="report-next"]').click()
            expect(member.locator(".attachment-list")).to_contain_text("验收证据.txt")
            member.screenshot(path=str(out/"member-report.png"), full_page=True)
            checks.append("多项目周报、跨周历史及真实附件下载")
            for label, target in [("admin", page), ("member", member)]:
                target.set_viewport_size({"width": 390, "height": 844})
                target.screenshot(path=str(out/f"{label}-mobile.png"), full_page=True)
                sizes = target.evaluate("({viewport:innerWidth, width:document.documentElement.scrollWidth, elements:[...document.querySelectorAll('body *')].filter(e=>e.getBoundingClientRect().right>innerWidth+1).slice(0,10).map(e=>({tag:e.tagName,cls:e.className,width:e.getBoundingClientRect().width}))})")
                if sizes["width"] > sizes["viewport"]:
                    errors.append(label+" mobile overflow: "+json.dumps(sizes, ensure_ascii=False))
            if not errors:
                checks.append("手机390px宽度无页面横向溢出")
            owner_context, owner = login(cohort["accounts"][0])
            owner.locator(f'.project-card[data-id="{cohort["project_ids"][0]}"]').click()
            expect(owner.locator('[data-action="edit-project"]')).to_be_visible()
            owner.locator('[data-action="edit-project"]').click()
            expect(owner.locator("#p-name")).to_have_value(f'【验收{cohort["prefix"]}】协作项目01')
            expect(owner.locator('input[name="member_ids"]:checked')).to_have_count(15)
            owner.locator('[data-action="close-modal"]').first.click()
            checks.append("负责人独立会话可打开项目编辑及15人成员分配")
            assert not errors, errors
            checks.append("所有角色无JavaScript运行异常")
            passed = True
        finally:
            browser.close()
            (out/"browser-report.json").write_text(json.dumps({"passed": passed, "candidate_css": bool(args.candidate_css), "checks": checks, "errors": errors, "timings": timings}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"passed": True, "checks": checks, "timings": timings}, ensure_ascii=False))


if __name__ == "__main__":
    main()
