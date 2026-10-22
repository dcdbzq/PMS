"""Role boundaries tested through HTTP, including direct API bypass attempts."""
import sqlite3
import unittest
from contextlib import closing
from datetime import date, timedelta
from app import current_week, initialize
import test_management as management


class RoleScopesTests(unittest.TestCase):
    setUp = management.ManagementTests.setUp
    tearDown = management.ManagementTests.tearDown
    add_member = management.ManagementTests.add_member
    update_person = management.ManagementTests.update_person
    project = management.ManagementTests.project
    entry = management.ManagementTests.entry
    submit = management.ManagementTests.submit
    stats = management.ManagementTests.stats
    upload = management.ManagementTests.upload

    def test_participant_only_own_reports_explicit_viewer_grant_and_revocation(self):
        a, first = self.add_member('参与人甲', 'first')
        b, second = self.add_member('参与人乙', 'second')
        pid, data = self.project('共同参与', [a,b])
        _, file = self.upload(second, pid)
        self.assertEqual(self.submit(second, [{**self.entry(pid),'attachment_ids':[file['id']]}])[0],200)
        self.assertEqual(first.state()[1]['reports'],[])
        self.assertEqual(first.state()[1]['submissions'],[])
        self.assertEqual(first.state()[1]['attachments'],[])
        self.assertEqual(first.request('GET',f"/api/attachments/{file['id']}")[0],403)
        self.assertEqual(first.request('GET','/api/analytics?scope=project')[0],403)
        self.assertEqual(self.admin.request('PUT',f'/api/projects/{pid}',{**data,'viewer_ids':[a]})[0],200)
        self.assertEqual(len(first.state()[1]['reports']),1)
        self.assertEqual(first.request('GET',f"/api/attachments/{file['id']}")[0],200)
        self.assertEqual(self.admin.request('PUT',f'/api/projects/{pid}',{**data,'viewer_ids':[]})[0],200)
        self.assertEqual(first.state()[1]['reports'],[])
        self.assertEqual(first.request('GET',f"/api/attachments/{file['id']}")[0],403)

    def test_owner_sees_full_tree_without_membership_but_not_other_work(self):
        oid, owner = self.add_member('项目负责人', 'owner')
        uid, member = self.add_member('阶段成员', 'worker')
        root, data = self.project('负责主项目', owner=oid)
        child, child_data = self.project('阶段', [uid], root)
        sibling,_ = self.project('阶段二',parent=root)
        private,_ = self.project('成员其他工作',[uid])
        self.assertEqual(self.submit(member,[self.entry(child,40),self.entry(private,30)],
            [dict(category='leave',effort=30,note='私人的休假说明')])[0],200)
        state = owner.state()[1]
        self.assertEqual({p['id'] for p in state['projects']},{root,child,sibling})
        self.assertTrue(all(p['can_manage'] for p in state['projects']))
        self.assertTrue(state['capabilities']['project_management'])
        self.assertNotIn(oid,next(p for p in state['projects'] if p['id']==root)['member_ids'])
        self.assertEqual({r['project_id'] for r in state['reports']},{child})
        self.assertEqual(state['non_project_entries'],[])
        self.assertEqual(state['submissions'][0]['leave_note'],'')
        stats = self.stats(owner,'project')[1]
        self.assertEqual(stats['project_effort'],40)
        self.assertEqual(stats['members'][0]['categories'],dict(leave=0,learning=0,other=0))
        self.assertNotIn('私人的休假说明',str(stats))
        self.assertEqual(owner.request('PUT',f'/api/projects/{child}',child_data)[0],200)
        self.assertEqual(self.submit(owner,[self.entry(root)])[0],403)
        self.assertEqual(owner.request('POST','/api/projects',{**data,'parent_id':root,'name':'新阶段'})[0],200)
        self.assertEqual(owner.request('POST','/api/projects',{**data,'parent_id':None})[0],403)

    def test_department_management_and_cross_department_limited_view(self):
        mid, manager = self.add_member('部门负责人', 'manager')
        uid, member = self.add_member('本部门成员', 'worker')
        oid, outside = self.add_member('外部门成员', 'outside')
        self.update_person(mid,role='department_manager')
        self.update_person(oid,department='市场部')
        own, own_data = self.project('部门承担但无本人参与', owner=oid, department='研发部')
        cross, cross_data = self.project('外部门承担的协作',[uid,oid],owner=oid,description='外部项目私密说明')
        unrelated,_ = self.project('外部独立项目',owner=oid)
        self.assertEqual(self.submit(member,[self.entry(cross)])[0],200)
        self.assertEqual(self.submit(outside,[self.entry(cross)])[0],200)
        state=manager.state()[1]
        projects={p['id']:p for p in state['projects']}
        self.assertEqual(set(projects),{own,cross})
        self.assertTrue(projects[own]['can_manage'])
        self.assertFalse(projects[cross]['can_manage'])
        self.assertTrue(projects[cross]['limited_view'])
        self.assertTrue(projects[cross]['is_department'])
        self.assertEqual(projects[cross]['description'],'')
        self.assertEqual(projects[cross]['member_ids'],[uid])
        self.assertEqual({r['user_id'] for r in state['reports']},{uid})
        self.assertEqual(self.stats(manager,'department')[1]['project_effort'],100)
        self.assertEqual(manager.request('PUT',f'/api/projects/{own}',own_data)[0],200)
        self.assertEqual(manager.request('PUT',f'/api/projects/{cross}',cross_data)[0],403)
        status,result=manager.request('POST','/api/projects',{**own_data,'name':'新建部门项目'})
        self.assertEqual(status,200,result)
        self.assertEqual(manager.request('POST','/api/projects',{**own_data,'department':'市场部'})[0],403)
        self.assertEqual(manager.request('POST','/api/users',{})[0],403)
        self.assertEqual(self.stats(manager,'company')[0],403)
        self.update_person(mid,role='member')
        self.assertEqual(manager.request('PUT',f'/api/projects/{own}',own_data)[0],403)

    def test_numeric_grant_does_not_allow_project_management_or_narrative_when_disabled(self):
        uid, member=self.add_member()
        aid, analyst=self.add_member('数值查看', 'analyst')
        pid,data=self.project('统计项目',[uid])
        self.assertEqual(self.submit(member,[self.entry(pid)])[0],200)
        self.update_person(aid,permissions={'stats_company':True,'report_view':False})
        self.assertEqual(self.stats(analyst,'company')[1]['project_effort'],100)
        self.assertEqual(analyst.state()[1]['reports'],[])
        self.assertEqual(analyst.request('PUT',f'/api/projects/{pid}',data)[0],403)
        self.assertEqual(analyst.request('POST','/api/projects',data)[0],403)

    def test_project_reminders_respect_phase_dates_and_completed_nonproject_week(self):
        oid, owner=self.add_member('项目负责人','owner')
        uid, member=self.add_member('本周参与','worker')
        expired,_=self.add_member('阶段已结束','expired')
        unrelated,_=self.add_member('其他项目成员','outside')
        previous=(date.fromisoformat(current_week())-timedelta(days=1)).isoformat()
        pid,_=self.project('阶段提醒',[uid,expired],owner=oid,assignments=[dict(user_id=expired,end_date=previous)])
        stats=self.stats(owner,'project')[1]
        self.assertEqual(stats['pending_ids'],[uid])
        payload=dict(scope='project',week=current_week(),user_ids=[uid])
        self.assertEqual(owner.request('POST','/api/reminders',payload)[1]['count'],1)
        self.assertEqual(owner.request('POST','/api/reminders',{**payload,'user_ids':[unrelated]})[0],403)
        self.assertEqual(owner.request('POST','/api/reminders',{**payload,'user_ids':[expired]})[0],403)
        self.assertEqual(self.submit(member,[],[dict(category='leave',effort=100,note='私密说明')])[0],200)
        self.assertEqual(self.stats(owner,'project')[1]['pending_ids'],[])
        self.assertEqual(owner.request('POST','/api/reminders',payload)[1]['count'],0)
        self.assertNotIn('私密说明',str(owner.state()[1]))

    def test_department_migration_is_stable_after_delegation_and_repeated_start(self):
        oid,owner=self.add_member('研发负责人','owner')
        other,_=self.add_member('市场负责人','outside')
        self.update_person(other,department='市场部')
        root,data=self.project('历史主项目',owner=oid)
        child,_=self.project('历史子项目',parent=root,owner=other)
        with closing(sqlite3.connect(self.path)) as db,db:
            db.execute('ALTER TABLE projects DROP COLUMN department')
        initialize(self.path)
        self.assertTrue(all(p['department']=='研发部' for p in owner.state()[1]['projects']))
        self.update_person(oid,department='其他部门')
        initialize(self.path)
        self.assertTrue(all(p['department']=='研发部' for p in owner.state()[1]['projects']))
        self.assertEqual(owner.request('PUT',f'/api/projects/{root}',{**data,'department':'其他部门'})[0],403)


if __name__=='__main__':unittest.main()
