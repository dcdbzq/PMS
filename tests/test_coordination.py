"""Coordination completion, authorization and administrator reporting exemption."""
import sqlite3
import unittest
from contextlib import closing
from datetime import date,timedelta
from app import current_week,initialize
import test_management as management


class CoordinationTests(unittest.TestCase):
    setUp=management.ManagementTests.setUp
    tearDown=management.ManagementTests.tearDown
    add_member=management.ManagementTests.add_member
    update_person=management.ManagementTests.update_person
    project=management.ManagementTests.project
    entry=management.ManagementTests.entry
    submit=management.ManagementTests.submit
    stats=management.ManagementTests.stats
    upload=management.ManagementTests.upload

    def record(self,client):
        return client.state()[1]['reports'][0]

    def toggle(self,client,report,completed=True,**changes):
        return client.request('PUT',f"/api/reports/{report['id']}/coordination",
            dict(completed=completed,expected_coordination=report['coordination'],**changes))

    def test_complete_reopen_and_counts_persist_for_all_views(self):
        uid,member=self.add_member()
        pid,_=self.project('协调项目',[uid])
        self.assertEqual(self.submit(member,[self.entry(pid)])[0],200)
        report=self.record(member)
        self.assertEqual(len(self.stats(member,'personal')[1]['coordination']),1)
        self.assertEqual(self.toggle(member,report)[0],200)
        closed=self.record(member)
        self.assertEqual(closed['coordination_completed'],1)
        self.assertEqual(closed['coordination_completed_by'],uid)
        self.assertTrue(closed['coordination_completed_at'])
        for client,scope in [(member,'personal'),(self.admin,'company')]:
            stats=self.stats(client,scope)[1]
            self.assertEqual(stats['coordination'],[])
            self.assertEqual(stats['coordination_completed'][0]['id'],report['id'])
        self.assertEqual(self.toggle(self.admin,closed)[0],200)
        self.assertEqual(self.record(member)['coordination_completed_by'],uid) # idempotent completion
        self.assertEqual(self.toggle(self.admin,closed,False)[0],200)
        self.assertEqual(len(self.stats(member,'personal')[1]['coordination']),1)
        self.assertIsNone(self.record(member)['coordination_completed_by'])
        self.assertEqual(self.record(member)['coordination_completed_at'],'')

    def test_resubmit_preserves_completion_until_coordination_content_changes(self):
        uid,member=self.add_member()
        pid,_=self.project('协调项目',[uid])
        entry=self.entry(pid)
        self.submit(member,[entry]);report=self.record(member)
        self.toggle(member,report)
        self.assertEqual(self.submit(member,[{**entry,'note':'更新成果','next_plan':'调整计划'}])[0],200)
        self.assertEqual(self.record(member)['coordination_completed'],1)
        self.assertEqual(self.submit(member,[{**entry,'coordination':'新的协调请求'}])[0],200)
        updated=self.record(member)
        self.assertEqual(updated['coordination_completed'],0)
        self.assertIsNone(updated['coordination_completed_by'])
        self.toggle(member,updated)
        previous=(date.fromisoformat(current_week())-timedelta(days=7)).isoformat()
        self.assertEqual(self.submit(member,[{**entry,'coordination':'新的协调请求'}],week=previous)[0],200)
        history=next(r for r in member.state()[1]['reports'] if r['week']==previous)
        self.assertEqual(history['coordination_completed'],0)
        self.assertEqual(self.submit(member,[{**entry,'coordination':''}])[0],200)
        self.assertEqual(self.stats(member,'personal')[1]['coordination'],[])
        self.assertEqual(self.stats(member,'personal')[1]['coordination_completed'],[])

    def test_status_management_is_stricter_than_viewing(self):
        uid,member=self.add_member()
        oid,owner=self.add_member('项目负责人','owner')
        mid,manager=self.add_member('部门负责人','manager')
        vid,viewer=self.add_member('查看人','viewer')
        aid,analyst=self.add_member('统计查看','analyst')
        self.update_person(mid,role='department_manager')
        self.update_person(aid,permissions={'stats_company':True})
        root,_=self.project('负责项目',owner=oid,department='研发部',viewer_ids=[vid])
        child,_=self.project('阶段',[uid],root)
        self.submit(member,[self.entry(child)])
        report=self.record(member)
        self.assertFalse(self.record(viewer)['can_manage_coordination'])
        self.assertFalse(self.record(analyst)['can_manage_coordination'])
        for client in (viewer,analyst):
            self.assertEqual(self.toggle(client,report)[0],403)
        for client in (owner,manager,self.admin,member):
            self.assertTrue(self.record(client)['can_manage_coordination'])
            self.assertEqual(self.toggle(client,report)[0],200)
            self.assertEqual(self.toggle(client,report,False)[0],200)
        self.update_person(oid,permissions={'report_view':False})
        self.assertEqual(self.toggle(owner,report)[0],403)

    def test_department_manager_cannot_change_other_departments_by_view_grant(self):
        uid,member=self.add_member()
        mid,manager=self.add_member('部门负责人','manager')
        self.update_person(uid,department='市场部')
        self.update_person(mid,role='department_manager')
        pid,_=self.project('其他部门',[uid],owner=uid,viewer_ids=[mid])
        self.submit(member,[self.entry(pid)])
        report=self.record(member)
        self.assertFalse(self.record(manager)['can_manage_coordination'])
        self.assertEqual(self.toggle(manager,report)[0],403)

    def test_stale_content_validation_and_csrf_do_not_modify_status(self):
        uid,member=self.add_member()
        pid,_=self.project('协调项目',[uid])
        self.submit(member,[self.entry(pid)])
        report=self.record(member)
        self.submit(member,[{**self.entry(pid),'coordination':'更新后的协调内容'}])
        self.assertEqual(self.toggle(member,report)[0],409)
        url=f"/api/reports/{report['id']}/coordination"
        self.assertEqual(member.request('PUT',url,dict(completed=1,expected_coordination='更新后的协调内容'))[0],400)
        self.assertEqual(member.request('PUT',url,dict(completed=True))[0],400)
        self.assertEqual(member.request('PUT',url,dict(completed=True,expected_coordination='更新后的协调内容'),{'X-CSRF-Token':'bad'})[0],403)
        self.assertEqual(self.record(member)['coordination_completed'],0)

    def test_administrator_is_exempt_even_with_old_explicit_submit_grants(self):
        uid,member=self.add_member()
        pid,_=self.project('历史工作',[uid])
        self.submit(member,[self.entry(pid)])
        payload=dict(scope='company',week=current_week(),user_ids=[1])
        self.assertEqual(self.admin.request('POST','/api/reminders',payload)[0],403)
        self.update_person(uid,role='admin',permissions={'report_submit':True,'stats_personal':True})
        state=member.state()[1]
        self.assertFalse(state['permissions']['report_submit'])
        self.assertFalse(state['permissions']['stats_personal'])
        self.assertEqual(self.submit(member,[self.entry(pid)])[0],403)
        self.assertEqual(self.upload(member,pid)[0],403)
        self.assertEqual(self.stats(member,'personal')[0],403)
        stats=self.stats()[1]
        self.assertEqual(stats['expected'],0)
        self.assertEqual(stats['submitted'],0)
        self.assertEqual(stats['pending_ids'],[])
        self.assertEqual(stats['project_effort'],100) # historical contributions retained
        self.assertEqual(len(state['reports']),1)

    def test_migration_keeps_existing_coordination_and_completed_state(self):
        uid,member=self.add_member()
        pid,_=self.project('历史协调',[uid])
        self.submit(member,[self.entry(pid)])
        before=self.record(member)
        with closing(sqlite3.connect(self.path)) as db,db:
            for name in ('coordination_completed','coordination_completed_by','coordination_completed_at'):
                db.execute('ALTER TABLE reports DROP COLUMN '+name)
        initialize(self.path);initialize(self.path)
        migrated=self.record(member)
        self.assertEqual(migrated['coordination'],before['coordination'])
        self.assertEqual(migrated['coordination_completed'],0)
        self.toggle(member,migrated)
        initialize(self.path)
        self.assertEqual(self.record(member)['coordination_completed'],1)


if __name__=='__main__':unittest.main()
