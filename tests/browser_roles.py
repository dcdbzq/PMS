"""Role-specific dashboards and management flows with isolated local fixtures."""
import json
import sys
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import Server, Handler, current_week
from feishu import FeishuConfig
from playwright.sync_api import sync_playwright, expect
from browser_management import login, request

OUTPUT=Path(__file__).resolve().parents[1]/'test-results'/'roles-browser'


def main():
    OUTPUT.mkdir(parents=True,exist_ok=True)
    errors=[]
    checks=[]
    with tempfile.TemporaryDirectory() as temp,patch.object(Handler,'log_message',lambda *_:None):
        server=Server(('127.0.0.1',0),Path(temp)/'roles.sqlite3',FeishuConfig())
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        origin=f'http://127.0.0.1:{server.server_port}'
        try:
            with sync_playwright() as p:
                browser=p.chromium.launch(channel='chrome')
                def page_for(username):
                    page=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN').new_page()
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    login(page,origin,username,'Role-test-password')
                    return page
                admin=browser.new_page()
                admin.goto(origin)
                admin.locator('#auth-name').fill('公司管理员');admin.locator('#auth-username').fill('admin')
                admin.locator('#auth-password').fill('Role-test-password');admin.locator('#auth-form button[type=submit]').click()
                expect(admin.locator('.overview-head')).to_be_visible()
                people={}
                for key,name,role,dept in [('owner','项目负责人','member','研发部'),('manager','部门负责人','department_manager','研发部'),('worker','项目参与人','member','研发部'),('outside','市场同事','member','市场部')]:
                    people[key]=request(admin,origin,'POST','/api/users',dict(name=name,username=key,password='Role-test-password',role=role,department=dept,active=True))['id']
                def project(name,owner,members=(),parent=None,dept=None):
                    data=dict(name=name,description='项目内部说明',owner_id=people[owner],member_ids=[people[m] for m in members],parent_id=parent,start_date='2026-01-01',due_date='2026-12-31',priority='normal',archived=False)
                    if dept is not None:data['department']=dept
                    return request(admin,origin,'POST','/api/projects',data)['id']
                root=project('客户协作平台','owner')
                child=project('第一阶段 · 接口开发','outside',['worker'],root)
                cross=project('跨部门协作看板','outside',['worker','outside'])
                project('市场独立工作','outside')
                worker=page_for('worker')
                def entry(pid,effort,note):return dict(project_id=pid,effort=effort,progress=40,note=note,next_plan='下周完成联调',coordination='请协助确认验收时间')
                request(worker,origin,'PUT','/api/reports',dict(week=current_week(),entries=[entry(child,60,'本周完成接口开发'),entry(cross,30,'本部门的跨项目成果')],non_project_entries=[dict(category='leave',effort=10,note='不向项目负责人展示的休假')]))
                outside=page_for('outside')
                request(outside,origin,'PUT','/api/reports',dict(week=current_week(),entries=[entry(cross,100,'外部门保密工作成果')]))
                owner=page_for('owner')
                expect(owner.locator('.overview-nav [data-value="team"]')).to_have_attribute('aria-pressed','true')
                expect(owner.locator('.overview-nav')).to_contain_text('负责项目')
                expect(owner.locator('.managed-project-list')).to_contain_text('第一阶段')
                expect(owner.locator('main')).not_to_contain_text('外部门保密')
                expect(owner.locator('main')).not_to_contain_text('本部门的跨项目成果')
                owner.locator('[data-overview-section="members"]>summary').click()
                expect(owner.locator('[data-overview-section="members"]')).to_contain_text('60%')
                expect(owner.locator('[data-overview-section="members"]')).not_to_contain_text('休假')
                owner.screenshot(path=str(OUTPUT/'project-owner-desktop.png'),full_page=True)
                owner.locator('[data-overview-section="members"]>summary').click()
                owner.locator(f'.managed-project-list [data-action="edit-project"][data-id="{child}"]').click()
                owner.locator('#p-members-search').fill('worker')
                expect(owner.locator('#p-members-list .assignment-row:visible')).to_have_count(1)
                owner.locator('#p-name').fill('第一阶段 · 接口开发与联调')
                owner.locator('#project-form button[type=submit]').click()
                expect(owner.locator('dialog')).to_have_count(0)
                expect(owner.locator('.managed-project-list')).to_contain_text('开发与联调')
                owner.locator('nav a[href="#projects"]').click()
                expect(owner.locator('.relation-chip.selected')).to_have_text('我负责的')
                if owner.locator(f'.project-title-button[data-id="{root}"]').get_attribute('aria-expanded')=='false':
                    owner.locator(f'.project-title-button[data-id="{root}"]').click()
                expect(owner.locator(f'[data-project-node="{child}"]')).to_be_visible()
                expect(owner.locator('[data-action="new-project"]')).to_have_count(0)
                checks.append('项目负责人默认管理视角、跨级项目配置、独立参与配置及项目内投入')
                manager=page_for('manager')
                expect(manager.locator('.overview-nav')).to_contain_text('部门项目')
                expect(manager.locator('.team-overview-grid')).to_be_visible()
                expect(manager.locator('main')).not_to_contain_text('外部门保密工作成果')
                manager.locator('[data-overview-section="reports"]>summary').click()
                expect(manager.locator('main')).to_contain_text('本部门的跨项目成果')
                expect(manager.locator('main')).to_contain_text('本周完成接口开发')
                expect(manager.locator('.non-project-history')).to_contain_text('不向项目负责人展示的休假')
                manager.locator('[data-overview-section="reports"]>summary').click()
                manager.screenshot(path=str(OUTPUT/'department-desktop.png'),full_page=True)
                manager.locator('[data-action="new-project"]').click()
                manager.locator('#p-name').fill('部门负责人创建的交付项目')
                expect(manager.locator('#p-department')).to_have_value('研发部')
                manager.locator('#p-owner').select_option(str(people['owner']))
                manager.locator('[name="member_ids"]:checked').uncheck()
                manager.locator('#project-form button[type=submit]').click()
                expect(manager.locator('dialog')).to_have_count(0)
                manager.locator('nav a[href="#projects"]').click()
                expect(manager.locator('.relation-chip.selected')).to_have_text('部门项目')
                expect(manager.locator('.project-tree')).to_contain_text('交付项目')
                expect(manager.locator('.project-tree')).to_contain_text('跨部门协作看板')
                expect(manager.locator('.project-tree')).not_to_contain_text('市场独立工作')
                manager.locator(f'.project-tree-actions [data-action="detail"][data-id="{cross}"]').click()
                expect(manager.locator('dialog')).to_contain_text('跨部门项目')
                expect(manager.locator('dialog')).not_to_contain_text('项目内部说明')
                expect(manager.locator('dialog')).not_to_contain_text('外部门保密工作成果')
                expect(manager.locator('dialog [data-action="edit-project"]')).to_have_count(0)
                manager.locator('[data-action="close-modal"]').click()
                checks.append('部门项目可见范围、新建及委派、跨部门周报隔离')
                worker.reload()
                expect(worker.locator('.board-card')).to_have_count(0)
                for text in ['等待跟进','部门项目','待办与异常']:
                    expect(worker.locator('main')).not_to_contain_text(text)
                worker.locator('nav a[href="#projects"]').click()
                worker.locator(f'.project-title-button[data-id="{cross}"]').click()
                expect(worker.locator('dialog')).not_to_contain_text('外部门保密工作成果')
                worker.locator('[data-action="close-modal"]').click()
                checks.append('普通参与人无管理入口、同项目他人正文隔离')
                manager.locator('nav a[href="#overview"]').click()
                manager.locator('.overview-nav [data-value="followup"]').click()
                expect(manager.locator('.pending-grid')).to_contain_text('部门负责人')
                expect(manager.locator('.coordination-card')).to_have_count(2)
                manager.screenshot(path=str(OUTPUT/'department-followup-desktop.png'),full_page=True)
                for name,page in [('project-owner',owner),('department',manager),('participant',worker)]:
                    page.locator('nav a[href="#overview"]').click()
                    page.set_viewport_size({'width':390,'height':844});page.evaluate('scrollTo(0,0)')
                    page.screenshot(path=str(OUTPUT/(name+'-mobile.png')),full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),name
                checks.append('项目负责人、部门负责人和参与人390px手机布局')
                assert not errors,errors
                browser.close()
        finally:
            server.shutdown();server.server_close();thread.join()
    (OUTPUT/'report.json').write_text(json.dumps(dict(passed=True,checks=checks,errors=errors),ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=True,checks=len(checks),errors=errors),ensure_ascii=False))


if __name__=='__main__':main()
