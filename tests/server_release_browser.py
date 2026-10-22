"""Browser acceptance of the deployed release using isolated release QA accounts."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
import time
from playwright.sync_api import sync_playwright,expect
from server_acceptance import read_admin

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--admin-file',required=True);parser.add_argument('--accounts-file',required=True);parser.add_argument('--out',required=True)
    args=parser.parse_args();out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    info=read_admin(args.admin_file);cohort=json.loads(Path(args.accounts_file).read_text(encoding='utf-8'))
    url=cohort['url'];projects=cohort['project_ids'];accounts=cohort['accounts'];errors=[];checks=[];timings=[];passed=False
    expect.set_options(timeout=30000)
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome')
        def login(account):
            page=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN').new_page()
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(url)
            page.locator('#auth-username').fill(account['username']);page.locator('#auth-password').fill(account['password'])
            started=time.perf_counter();page.locator('#auth-form button[type=submit]').click()
            expect(page.locator('.overview-head')).to_be_visible()
            timings.append(round((time.perf_counter()-started)*1000,2))
            return page
        try:
            admin=login(info)
            admin.locator('nav a[href="#projects"]').click()
            admin.locator('#project-search').fill(cohort['prefix'])
            expect(admin.locator('[data-project-node]')).to_have_count(len(projects))
            admin.locator(f'.project-title-button[data-id="{projects["root"]}"]').click()
            expect(admin.locator(f'[data-project-node="{projects["child"]}"]')).to_be_visible()
            expect(admin.locator(f'[data-project-node="{projects["leaf"]}"]')).to_be_visible() # Search expands matching paths.
            admin.locator('#project-search').fill('')
            admin.reload()
            expect(admin.locator(f'[data-project-node="{projects["child"]}"]')).to_have_count(0)
            admin.locator(f'.project-title-button[data-id="{projects["root"]}"]').click()
            expect(admin.locator(f'[data-project-node="{projects["child"]}"]')).to_be_visible()
            admin.locator(f'.project-title-button[data-id="{projects["child"]}"]').click()
            expect(admin.locator(f'[data-project-node="{projects["leaf"]}"]')).to_be_visible()
            admin.locator('[data-action="new-project"]').click()
            expect(admin.locator('#modal-title')).to_have_text('新建项目')
            expect(admin.locator('#p-approval')).to_have_count(0)
            expect(admin.locator('[name="viewer_ids"][value="1"]')).to_be_checked()
            admin.locator('[data-action="close-modal"]').first.click()
            checks.append('正式项目列表三层展开/收起、搜索层级、新建项目无编号且默认查看人')
            admin.screenshot(path=str(out/'projects-desktop.png'),full_page=True)
            member=login(accounts['member'])
            expect(member.locator('.board-card')).to_have_count(0)
            for text in ['应填报','已填报','未填报','团队总览','部门项目','待办跟进']:
                expect(member.locator('main')).not_to_contain_text(text)
            member.locator('nav a[href="#report"]').click()
            expect(member.locator('.report-card')).to_have_count(1)
            expect(member.locator('[data-field="next_plan"]')).to_have_value('下周继续联调与验收')
            expect(member.locator('.non-project-row')).to_have_count(2)
            member.locator('[data-field="effort"]').fill('69.5');expect(member.locator('#submit-report')).to_be_disabled()
            member.locator('[data-field="effort"]').fill('70');expect(member.locator('#submit-report')).to_be_enabled()
            note=member.locator('[data-field="note"]').input_value()
            member.locator('[data-field="note"]').fill(note+'（浏览器验收通过）')
            member.locator('#submit-report').click();expect(member.locator('#draft-status')).to_contain_text('最近提交于')
            member.reload();expect(member.locator('[data-field="note"]')).to_have_value(note+'（浏览器验收通过）')
            checks.append('成员个人视角无管理信息，100%前端校验、真实提交、重载和附件保留')
            member.screenshot(path=str(out/'report-desktop.png'),full_page=True)
            member.locator('nav a[href="#overview"]').click()
            member.locator('[data-action="refresh"]').click();expect(member.locator('.weekly-preview')).to_have_count(1)
            member.screenshot(path=str(out/'personal-desktop.png'),full_page=True)
            member.locator('.weekly-preview').click();expect(member.locator('dialog')).to_contain_text('浏览器验收通过')
            member.locator('[data-action="close-modal"]').first.click()
            checks.append('个人投入分布与周报详情可用')
            manager=login(accounts['manager'])
            expect(manager.locator('.reminder-banner')).to_be_visible()
            manager.locator('[data-action="read-reminders"]').click();expect(manager.locator('.reminder-banner')).to_have_count(0)
            manager.locator('.overview-nav [data-value="team"]').click();expect(manager.locator('.team-overview-grid')).to_be_visible()
            expect(manager.locator('main')).not_to_contain_text(accounts['outsider']['name'])
            manager.locator('[data-overview-section="reports"]>summary').click()
            manager.locator('#analysis-member').select_option(str(accounts['member']['id']))
            expect(manager.locator('.weekly-content')).to_contain_text('浏览器验收通过')
            manager.locator('.overview-nav [data-value="followup"]').click()
            expect(manager.locator('.coordination-card')).to_contain_text('需要产品在周四前确认字段')
            manager.locator('[data-action="remind-all"]').click();expect(manager.locator('#toasts')).to_contain_text('已发送 1 条站内提醒')
            checks.append('部门隔离、周报筛选、协调卡片和真实站内提醒')
            viewer=login(accounts['viewer'])
            expect(viewer.locator('nav a[href="#report"]')).to_have_count(0)
            viewer.locator('nav a[href="#projects"]').click()
            expect(viewer.locator(f'[data-project-node="{projects["private"]}"]')).to_have_count(0)
            viewer.locator(f'.project-tree-actions [data-action="detail"][data-id="{projects["root"]}"]').click()
            expect(viewer.locator('dialog')).to_contain_text('浏览器验收通过')
            expect(viewer.locator('[data-action="report-project"]')).to_have_count(0)
            with viewer.expect_download() as download:viewer.locator('dialog .attachment-list a').first.click()
            with tempfile.TemporaryDirectory() as tmp:
                destination=Path(tmp)/'evidence.txt';download.value.save_as(destination)
                expected=('更新验收附件：权限、成果及计划。\n'*100).encode()
                assert hashlib.sha256(destination.read_bytes()).digest()==hashlib.sha256(expected).digest()
            checks.append('只读查看人可查看下级周报、下载附件，无填报权限或其他项目')
            admin.locator('nav a[href="#overview"]').click()
            admin.locator('.overview-nav [data-value="team"]').click();expect(admin.locator('.team-overview-grid')).to_be_visible()
            checks.append('公司总览和部门工作量正常加载')
            for name,page in [('company',admin),('personal',member),('followup',manager)]:
                page.set_viewport_size({'width':390,'height':844});page.evaluate('scrollTo(0,0)')
                page.screenshot(path=str(out/(name+'-mobile.png')),full_page=True)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),name+' overflow'
            viewer.locator('[data-action="close-modal"]').first.click();viewer.set_viewport_size({'width':390,'height':844})
            viewer.locator(f'.project-title-button[data-id="{projects["root"]}"]').click()
            viewer.locator(f'.project-title-button[data-id="{projects["child"]}"]').click()
            viewer.screenshot(path=str(out/'project-tree-mobile.png'),full_page=True)
            assert viewer.evaluate('document.documentElement.scrollWidth<=innerWidth')
            checks.append('正式数据及长名称下390px手机页面、三级树无横向溢出')
            assert not errors,errors
            checks.append('所有受测页面无JavaScript运行异常')
            passed=True
        finally:
            browser.close()
            (out/'report.json').write_text(json.dumps(dict(passed=passed,checks=checks,errors=errors,login_to_overview_ms=timings),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=passed,checks=len(checks),errors=errors),ensure_ascii=False))

if __name__=='__main__':main()
