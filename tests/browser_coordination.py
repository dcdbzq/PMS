"""Exercise coordination lifecycle and administrator pages in real browsers."""
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Server,Handler,current_week
from feishu import FeishuConfig
from browser_management import login,request
from playwright.sync_api import sync_playwright,expect

OUT=Path(__file__).resolve().parents[1]/'test-results'/'coordination-browser'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    errors=[];checks=[]
    with tempfile.TemporaryDirectory() as temp,patch.object(Handler,'log_message',lambda *_:None):
        server=Server(('127.0.0.1',0),Path(temp)/'browser.sqlite3',FeishuConfig())
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='chrome')
                admin=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN').new_page()
                admin.on('pageerror',lambda e:errors.append(str(e)))
                admin.goto(origin)
                admin.locator('#auth-name').fill('公司管理员');admin.locator('#auth-username').fill('admin')
                admin.locator('#auth-password').fill('Test-password-123');admin.locator('#auth-form button[type=submit]').click()
                expect(admin.locator('.team-overview-grid')).to_be_visible()
                expect(admin.locator('nav a[href="#report"]')).to_have_count(0)
                expect(admin.locator('[data-action="go-report"]')).to_have_count(0)
                expect(admin.locator('.overview-nav [data-value="mine"]')).to_have_count(0)
                expect(admin.locator('.personal-home')).to_have_count(0)
                admin.screenshot(path=str(OUT/'admin-overview-desktop.png'),full_page=True)
                admin.locator('nav a[href="#members"]').click();admin.locator('[data-action="edit-member"]').click()
                expect(admin.locator('[name="permission"][value="report_submit"]')).to_be_disabled()
                expect(admin.locator('[name="permission"][value="report_submit"]')).not_to_be_checked()
                expect(admin.locator('[name="permission"][value="stats_personal"]')).to_be_disabled()
                admin.locator('#u-role').select_option('member')
                expect(admin.locator('[name="permission"][value="report_submit"]')).to_be_enabled()
                expect(admin.locator('[name="permission"][value="report_submit"]')).to_be_checked()
                admin.locator('dialog .icon-button[data-action="close-modal"]').click()
                people={}
                for name,key,role in [('项目成员','worker','member'),('项目负责人','owner','member'),('部门负责人','manager','department_manager'),('只读查看人','viewer','member')]:
                    people[key]=request(admin,origin,'POST','/api/users',dict(name=name,username=key,password='Test-password-123',department='研发部',role=role,active=True))['id']
                pid=request(admin,origin,'POST','/api/projects',dict(name='客户协作平台',owner_id=people['owner'],member_ids=[people['worker']],viewer_ids=[people['viewer']],start_date='2026-01-01',due_date='2026-12-31',priority='normal',archived=False))['id']
                def page_for(key):
                    page=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN').new_page()
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    login(page,origin,key,'Test-password-123')
                    return page
                worker=page_for('worker')
                entry=dict(project_id=pid,effort=100,progress=40,note='本周完成接口联调',next_plan='下周开展验收',coordination='请产品同事确认验收时间')
                request(worker,origin,'PUT','/api/reports',dict(week=current_week(),entries=[entry]))
                worker.reload()
                metric=lambda page:page.locator('.overview-metric').filter(has_text='需要协调').locator('.metric-value')
                expect(metric(worker)).to_contain_text('1')
                worker.locator('.coordination-card [data-action="coordination-toggle"]').click()
                expect(metric(worker)).to_contain_text('0')
                expect(worker.locator('.coordination-grid .coordination-card:visible')).to_have_count(0)
                worker.locator('[data-overview-section="personal-coordination"]>summary').click()
                expect(worker.locator('.coordination-done')).to_contain_text('已完成')
                expect(worker.locator('.coordination-done')).to_contain_text('项目成员')
                worker.reload();expect(metric(worker)).to_contain_text('0')
                worker.locator('[data-overview-section="personal-coordination"]>summary').click()
                worker.locator('.coordination-done [data-action="coordination-toggle"]').click()
                expect(metric(worker)).to_contain_text('1')
                worker.screenshot(path=str(OUT/'personal-pending-desktop.png'),full_page=True)
                checks.append('个人主页面完成后1变0、刷新持久化、已完成记录及重新打开')
                viewer=page_for('viewer')
                viewer.locator('nav a[href="#projects"]').click()
                viewer.locator(f'.project-tree-actions [data-action="detail"][data-id="{pid}"]').click()
                expect(viewer.locator('dialog')).to_contain_text('请产品同事确认验收时间')
                expect(viewer.locator('[data-action="coordination-toggle"]')).to_have_count(0)
                owner=page_for('owner')
                owner.locator('.overview-nav [data-value="followup"]').click()
                expect(metric(owner)).to_contain_text('1')
                owner.locator('.coordination-card [data-action="coordination-toggle"]').click()
                expect(metric(owner)).to_contain_text('0')
                owner.locator('[data-overview-section="team-coordination"]>summary').click()
                expect(owner.locator('.coordination-done')).to_contain_text('项目负责人')
                owner.screenshot(path=str(OUT/'owner-completed-desktop.png'),full_page=True)
                manager=page_for('manager');manager.locator('.overview-nav [data-value="followup"]').click()
                expect(metric(manager)).to_contain_text('0')
                manager.locator('[data-overview-section="team-coordination"]>summary').click()
                manager.locator('.coordination-done [data-action="coordination-toggle"]').click()
                expect(metric(manager)).to_contain_text('1')
                checks.append('负责人完成、部门负责人重新打开、只读查看人无操作按钮')
                admin.locator('nav a[href="#overview"]').click();admin.locator('[data-action="refresh"]').click()
                admin.locator('.overview-nav [data-value="followup"]').click()
                expect(metric(admin)).to_contain_text('1')
                admin.locator('.coordination-card [data-action="coordination-toggle"]').click()
                expect(metric(admin)).to_contain_text('0')
                admin.locator('[data-overview-section="team-coordination"]>summary').click()
                expect(admin.locator('.coordination-done')).to_contain_text('公司管理员')
                expect(admin.locator('.pending-grid')).not_to_contain_text('公司管理员')
                for label,page in [('admin',admin),('personal',worker)]:
                    page.set_viewport_size({'width':390,'height':844});page.evaluate('scrollTo(0,0)')
                    page.locator('#toasts').evaluate('(el)=>el.replaceChildren()')
                    page.screenshot(path=str(OUT/(label+'-mobile.png')),full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                checks.append('管理员无个人周报和个人投入、可管理协调、不列入催报，390px手机无溢出')
                assert not errors,errors
                browser.close()
        finally:
            server.shutdown();server.server_close();thread.join()
    (OUT/'report.json').write_text(json.dumps(dict(passed=True,checks=checks,errors=errors),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=True,checks=len(checks),errors=errors),ensure_ascii=False))


if __name__=='__main__':main()
