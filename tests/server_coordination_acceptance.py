"""Verify deployed coordination on an existing isolated QA record, then restore its state."""
import argparse
import json
from pathlib import Path
from server_acceptance import Client,read_admin
from playwright.sync_api import sync_playwright,expect


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--admin-file',required=True)
    parser.add_argument('--qa-file',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    fixture=json.loads(Path(args.qa_file).read_text(encoding='utf-8'))
    accounts=fixture['accounts'];info=read_admin(args.admin_file);url=fixture['url']
    admin=Client(url);full=admin.login(info['username'],info['password'])
    member=Client(url);own=member.login(accounts['member']['username'],accounts['member']['password'])
    report=next(r for r in own['reports'] if r['week']==own['week'] and r['project_id'] in fixture['project_ids'].values() and r['coordination'].strip())
    assert fixture['prefix'] in next(p['name'] for p in full['projects'] if p['id']==report['project_id'])
    initial=bool(report['coordination_completed']);assert not initial,'Use a pending isolated QA record';rid=report['id'];path=f'/api/reports/{rid}/coordination'
    baseline=admin.request('GET','/api/analytics?scope=company&week='+own['week'])
    ownbaseline=member.request('GET','/api/analytics?scope=personal&week='+own['week'])
    checks=[];errors=[]
    def change(client,completed):
        client.request('PUT',path,dict(completed=completed,expected_coordination=report['coordination']))
    try:
        assert not full['permissions']['report_submit'] and not full['permissions']['stats_personal']
        admins={u['id'] for u in full['users'] if u['role']=='admin'}
        assert not admins.intersection(baseline['pending_ids'])
        assert all(not m['expected'] for m in baseline['members'] if m['id'] in admins)
        admin.request('PUT','/api/reports',{},expected=(403,))
        checks.append('administrators exempt from reporting, personal analytics and reminder eligibility')
        viewer=Client(url);viewer.login(accounts['viewer']['username'],accounts['viewer']['password'])
        viewer.request('PUT',path,dict(completed=True,expected_coordination=report['coordination']),expected=(403,))
        change(member,True)
        closed=next(r for r in member.request('GET','/api/state')['reports'] if r['id']==rid)
        assert closed['coordination_completed'] and closed['coordination_completed_by']==own['user']['id']
        assert closed['updated_at']==report['updated_at'] and closed['coordination']==report['coordination']
        closed_count=len(baseline['coordination'])-(not initial)
        assert len(admin.request('GET','/api/analytics?scope=company&week='+own['week'])['coordination'])==closed_count
        checks.append('completion decrements company count by one and persists actor/time; viewer cannot mutate')
        expect.set_options(timeout=30000)
        with sync_playwright() as p:
            browser=p.chromium.launch(channel='chrome')
            def login(account):
                page=browser.new_context(viewport={'width':1440,'height':1000},locale='zh-CN').new_page()
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(url);page.locator('#auth-username').fill(account['username']);page.locator('#auth-password').fill(account['password'])
                page.locator('#auth-form button[type=submit]').click();expect(page.locator('.overview-head')).to_be_visible()
                return page
            page=login(info)
            expect(page.locator('nav a[href="#report"]')).to_have_count(0)
            expect(page.locator('[data-action="go-report"]')).to_have_count(0)
            expect(page.locator('.overview-nav [data-value="mine"]')).to_have_count(0)
            expect(page.locator('.team-overview-grid')).to_be_visible()
            page.locator('#toasts').evaluate('(el)=>el.replaceChildren()')
            page.screenshot(path=str(out/'admin-overview-desktop.png'),full_page=True)
            page.locator('.overview-nav [data-value="followup"]').click()
            metric=page.locator('.overview-metric').filter(has_text='需要协调').locator('.metric-value')
            expect(metric).to_have_text(str(closed_count)+'项')
            page.locator('[data-overview-section="team-coordination"]>summary').click()
            page.locator(f'[data-coordination-id="{rid}"] [data-action="coordination-toggle"]').click()
            expect(metric).to_have_text(str(closed_count+1)+'项')
            page.locator(f'[data-coordination-id="{rid}"] [data-action="coordination-toggle"]').click()
            expect(metric).to_have_text(str(closed_count)+'项')
            detail=page.locator('[data-overview-section="team-coordination"]')
            if not detail.evaluate('(el)=>el.open'):detail.locator('summary').click()
            expect(page.locator(f'[data-coordination-id="{rid}"]')).to_contain_text('已完成')
            page.set_viewport_size({'width':390,'height':844});page.locator('#toasts').evaluate('(el)=>el.replaceChildren()')
            page.screenshot(path=str(out/'admin-coordination-mobile.png'),full_page=True)
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            worker=login(accounts['member'])
            ownmetric=worker.locator('.overview-metric').filter(has_text='需要协调').locator('.metric-value')
            expect(ownmetric).to_have_text(str(len(ownbaseline['coordination'])-(not initial))+'项')
            worker.reload();expect(ownmetric).to_have_text(str(len(ownbaseline['coordination'])-(not initial))+'项')
            worker.locator('[data-overview-section="personal-coordination"]>summary').click()
            expect(worker.locator(f'[data-coordination-id="{rid}"]')).to_contain_text('已完成')
            worker.locator('#toasts').evaluate('(el)=>el.replaceChildren()')
            worker.screenshot(path=str(out/'member-completed-desktop.png'),full_page=True)
            browser.close()
        checks.append('real browser admin reopen/complete and personal counter persistence; mobile overflow absent')
        assert not errors,errors
    finally:
        change(admin,initial)
        restored=next(r for r in admin.request('GET','/api/state')['reports'] if r['id']==rid)
        assert restored['coordination_completed']==report['coordination_completed']
        assert restored['updated_at']==report['updated_at']
    checks.append('isolated QA state restored; original weekly content and submission timestamp untouched')
    result=dict(passed=True,checks=checks,errors=errors,qa_report_id=rid)
    (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=True,checks=len(checks),errors=errors),ensure_ascii=False))


if __name__=='__main__':main()
