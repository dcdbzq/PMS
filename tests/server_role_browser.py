"""Read-only browser verification of the deployed role dashboards."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright,expect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--accounts-file',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    fixture=json.loads(Path(args.accounts_file).read_text(encoding='utf-8'))
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    accounts={a['username']:a for a in fixture['accounts']}
    errors=[];checks=[]
    expect.set_options(timeout=30000)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome')
        for key,tab in [('owner_a','负责项目'),('dept_manager','部门项目'),('member',None)]:
            a=accounts['trial1015_'+key]
            page=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN').new_page()
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(fixture['url'])
            page.locator('#auth-username').fill(a['username']);page.locator('#auth-password').fill(a['password'])
            page.locator('#auth-form button[type=submit]').click()
            expect(page.locator('.overview-head')).to_be_visible()
            if tab:
                expect(page.locator('.overview-nav [data-value="team"]')).to_have_attribute('aria-pressed','true')
                expect(page.locator('.overview-nav')).to_contain_text(tab)
                expect(page.locator('.team-overview-grid')).to_be_visible()
                expect(page.locator('.overview-nav')).to_contain_text('待办与异常')
            else:
                expect(page.locator('.board-card')).to_have_count(0)
                expect(page.locator('.personal-home')).to_be_visible()
            page.locator('#toasts').evaluate('(el)=>el.replaceChildren()')
            page.screenshot(path=str(out/(key+'-desktop.png')),full_page=True)
            page.locator('nav a[href="#projects"]').click()
            expect(page.locator('.relation-chip.selected')).to_have_text('我负责的' if key=='owner_a' else '部门项目' if key=='dept_manager' else '全部可见')
            if key=='dept_manager':
                page.locator('[data-action="new-project"]').click()
                expect(page.locator('#p-department')).to_have_value('人工体验')
                expect(page.locator(f'[name="viewer_ids"][value="{a["id"]}"]')).to_be_checked()
                expect(page.locator('#p-members-search')).to_be_visible()
                expect(page.locator('#p-viewers-search')).to_be_visible()
                page.locator('[data-action="close-modal"]').first.click()
            elif key=='member':
                expect(page.locator('[data-action="edit-project"]')).to_have_count(0)
                expect(page.locator('[data-action="new-project"]')).to_have_count(0)
            page.locator('nav a[href="#overview"]').click()
            if tab:
                page.locator('.overview-nav [data-value="followup"]').click()
                expect(page.locator('.pending-grid')).to_be_visible()
                expect(page.locator('.scope-caption')).to_be_visible()
            page.set_viewport_size({'width':390,'height':844});page.evaluate('scrollTo(0,0)')
            page.screenshot(path=str(out/(key+'-mobile.png')),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),key
            checks.append(key+': default dashboard, project range, controls, followup and mobile layout')
        assert not errors,errors
        browser.close()
    (out/'report.json').write_text(json.dumps(dict(passed=True,checks=checks,errors=errors),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=True,checks=len(checks),errors=errors),ensure_ascii=False))


if __name__=='__main__':main()
