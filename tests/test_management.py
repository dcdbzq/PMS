"""WBS、填报分类、统计及授权边界的真实 HTTP 回归测试。"""
import csv
import io
import sqlite3
import unittest
import hashlib
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from contextlib import closing
from app import current_week, initialize, timestamp
import test_app as legacy


class ManagementTests(unittest.TestCase):
    setUp = legacy.AppTests.setUp
    tearDown = legacy.AppTests.tearDown
    add_member = legacy.AppTests.add_member
    add_project = legacy.AppTests.add_project
    upload = legacy.AppTests.upload

    def update_person(self, uid, **changes):
        prior = next(u for u in self.admin.state()[1]['users'] if u['id'] == uid)
        data = {k:prior[k] for k in ('name','username','department','role')}
        return self.admin.request('PUT', f'/api/users/{uid}', {**data,'active':bool(prior['active']),**changes})

    def project(self, name, members=(), parent=None, owner=1, **changes):
        data = dict(name=name, owner_id=owner, member_ids=list(members), parent_id=parent,
            start_date='2020-01-01', due_date='2035-12-31', priority='normal', archived=False,
            approval_ref='LX-2026-001', **changes)
        status, result = self.admin.request('POST','/api/projects',data)
        self.assertEqual(status,200,result)
        return result['id'], data

    def entry(self, pid, effort=100, **changes):
        return dict(project_id=pid,progress=50,effort=effort,note='完成的成果',next_plan='下周计划',coordination='需要产品确认接口',**changes)

    def submit(self, client, entries, others=None, week=None):
        return client.request('PUT','/api/reports',dict(week=week or current_week(),entries=entries,non_project_entries=others or []))

    def stats(self, client=None, scope='company', week=None):
        return (client or self.admin).request('GET',f'/api/analytics?scope={scope}&week={week or current_week()}')

    def test_three_non_project_categories_and_atomic_total(self):
        uid, member = self.add_member()
        pid,_ = self.project('项目',[uid])
        others = [dict(category='leave',effort=10,note='周五半天休假'),dict(category='learning',effort=20,note='技术研究'),dict(category='other',effort=10,note='部门例会')]
        entry = self.entry(pid,60)
        self.assertEqual(self.submit(member,[entry],others)[0],200)
        state=member.state()[1]
        self.assertEqual(state['reports'][0]['next_plan'],'下周计划')
        self.assertEqual(state['reports'][0]['coordination'],'需要产品确认接口')
        self.assertEqual(len(state['non_project_entries']),3)
        self.assertEqual(state['submissions'][0]['leave_percent'],10)
        self.assertEqual(self.submit(member,[{**entry,'effort':59.5}],others)[0],400)
        self.assertEqual(member.state()[1]['reports'][0]['effort'],60)
        self.assertEqual(self.submit(member,[entry],[*others,others[0]])[0],400)
        self.assertEqual(self.submit(member,[],[dict(category='learning',effort=100,note='整周培训')])[0],200)
        self.assertEqual(member.state()[1]['reports'],[])
        self.assertEqual(self.submit(member,[],[dict(category='invalid',effort=100,note='无效分类')])[0],400)
        self.assertEqual(self.submit(member,[{**entry,'effort':100,'next_plan':''}])[0],400)
        exported=self.admin.request('GET','/api/export')[1].decode('utf-8-sig')
        rows=list(csv.reader(io.StringIO(exported)))
        self.assertTrue(all(len(r)==len(rows[0]) for r in rows))
        self.assertIn('学习/研究/培训',exported)

    def test_wbs_cycle_management_and_context_visibility(self):
        uid, member=self.add_member()
        _, outsider=self.add_member('其他人','outside')
        root,root_data=self.project('根项目')
        child,child_data=self.project('阶段',[uid],root,uid)
        leaf,leaf_data=self.project('交付包',[uid],child,uid)
        sibling,_=self.project('其他阶段',parent=root)
        self.assertEqual(self.admin.request('PUT',f'/api/projects/{root}',{**root_data,'parent_id':leaf})[0],400)
        self.assertEqual(outsider.request('PUT',f'/api/projects/{child}',child_data)[0],403)
        view=member.state()[1]
        self.assertEqual({p['id'] for p in view['projects']},{root,child,leaf})
        self.assertTrue(next(p for p in view['projects'] if p['id']==root)['context_only'])
        self.assertNotIn(sibling,{p['id'] for p in view['projects']})
        self.assertEqual(member.request('POST','/api/projects',{**leaf_data,'name':'第四级','parent_id':leaf})[0],200)
        self.assertEqual(member.request('POST','/api/projects',{**leaf_data,'parent_id':None})[0],403)
        self.assertEqual(member.request('PUT',f'/api/projects/{child}',{**child_data,'parent_id':sibling})[0],403)
        self.assertEqual(self.admin.request('PUT',f'/api/projects/{root}',{**root_data,'archived':True})[0],200)
        self.assertEqual(self.submit(member,[self.entry(leaf)])[0],403)

    def test_viewers_inherit_reading_without_submission_or_sibling_access(self):
        uid,member=self.add_member()
        viewer_id,viewer=self.add_member('查看人','viewer')
        root,_=self.project('授权目录',viewer_ids=[viewer_id])
        child,_=self.project('子项目',[uid],root)
        private,_=self.project('不可见项目')
        status,file=self.upload(member,child)
        self.assertEqual(status,201)
        self.assertEqual(self.submit(member,[{**self.entry(child),'attachment_ids':[file['id']]}])[0],200)
        state=viewer.state()[1]
        self.assertEqual({p['id'] for p in state['projects']},{root,child})
        self.assertNotIn(private,{p['id'] for p in state['projects']})
        self.assertEqual(len(state['reports']),1)
        self.assertEqual(viewer.request('GET',f"/api/attachments/{file['id']}")[0],200)
        self.assertEqual(self.submit(viewer,[self.entry(child)])[0],403)
        self.assertEqual(self.upload(viewer,child)[0],403)
        self.assertEqual(self.stats(viewer,'company')[0],403)

    def test_participation_dates_overlap_and_history(self):
        uid,member=self.add_member()
        week=current_week()
        sunday=(date.fromisoformat(week)+timedelta(days=6)).isoformat()
        pid,data=self.project('阶段参与',[uid],assignments=[dict(user_id=uid,start_date=week,end_date=sunday)])
        self.assertEqual(self.submit(member,[self.entry(pid)])[0],200)
        previous=(date.fromisoformat(week)-timedelta(days=7)).isoformat()
        self.assertEqual(self.submit(member,[self.entry(pid)],week=previous)[0],403)
        next_week=(date.fromisoformat(week)+timedelta(days=7)).isoformat()
        self.assertEqual(self.admin.request('PUT',f'/api/projects/{pid}',{**data,'assignments':[dict(user_id=uid,start_date=next_week,end_date='2030-01-01')]})[0],200)
        self.assertEqual(self.submit(member,[self.entry(pid)])[0],200) # previously submitted history remains editable
        self.assertEqual(self.admin.request('PUT',f'/api/projects/{pid}',{**data,'assignments':[dict(user_id=uid,start_date=sunday,end_date=week)]})[0],400)

    def test_unrestricted_members_can_report_after_planned_deadline(self):
        uid,member=self.add_member()
        pid,_=self.project('已逾期仍在推进',[uid])
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("UPDATE projects SET due_date='2021-01-01' WHERE id=?",(pid,))
        self.assertEqual(self.submit(member,[self.entry(pid)])[0],200)

    def test_creator_is_viewer_and_projects_have_no_reference_field(self):
        pid,data=self.project('无需编号的项目')
        p=next(p for p in self.admin.state()[1]['projects'] if p['id']==pid)
        self.assertIn(1,p['viewer_ids'])
        self.assertNotIn(1,p['member_ids']) # managing/viewing does not require reporting membership
        self.assertNotIn('approval_ref',p)
        self.assertEqual(self.admin.request('PUT',f'/api/projects/{pid}',{**data,'viewer_ids':[1]})[0],200)
        self.assertIn(1,next(p for p in self.admin.state()[1]['projects'] if p['id']==pid)['viewer_ids'])
        header=next(csv.reader(io.StringIO(self.admin.request('GET','/api/export')[1].decode('utf-8-sig'))))
        self.assertNotIn('立项编号',header)

    def test_department_scoping_and_report_content_permission(self):
        uid,member=self.add_member()
        other_id,other=self.add_member('其他部门','otherdept')
        self.assertEqual(self.update_person(other_id,department='市场部')[0],200)
        mid,manager=self.add_member('部门负责人','manager')
        self.assertEqual(self.update_person(mid,role='department_manager',managed_departments=['研发部'])[0],200)
        pid,_=self.project('跨部门项目',[uid,other_id])
        self.assertEqual(self.submit(member,[self.entry(pid)])[0],200)
        self.assertEqual(self.submit(other,[self.entry(pid)])[0],200)
        self.assertEqual({r['user_id'] for r in manager.state()[1]['reports']},{uid})
        status,stats=self.stats(manager,'department')
        self.assertEqual(status,200)
        self.assertEqual(stats['project_effort'],100)
        self.assertNotIn(other_id,{m['id'] for m in stats['members']})
        self.assertEqual(self.stats(manager,'company')[0],403)
        self.assertEqual(self.stats(member,'department')[0],403)
        _,file=self.upload(member,pid)
        self.assertEqual(self.submit(member,[{**self.entry(pid),'attachment_ids':[file['id']]}])[0],200)
        self.assertEqual(manager.request('GET',f"/api/attachments/{file['id']}")[0],200)
        self.assertEqual(self.update_person(mid,permissions={'report_view':False})[0],200)
        state=manager.state()[1]
        self.assertEqual(state['reports'],[])
        self.assertEqual(state['attachments'],[])
        self.assertEqual(manager.request('GET',f"/api/attachments/{file['id']}")[0],403)
        stats=self.stats(manager,'department')[1]
        self.assertEqual(stats['project_effort'],100)
        self.assertEqual(stats['coordination'],[])
        self.assertNotIn('完成的成果',str(stats))

    def test_rollup_does_not_duplicate_total_and_company_department_matrix(self):
        uid,member=self.add_member()
        root,_=self.project('根',[uid])
        child,_=self.project('子',[uid],root)
        leaf,_=self.project('孙',[uid],child)
        self.assertEqual(self.submit(member,[self.entry(root,20),self.entry(child,30),self.entry(leaf,40)],[dict(category='other',effort=10,note='例会')])[0],200)
        stats=self.stats()[1]
        rows={r['id']:r for r in stats['projects']}
        self.assertEqual(stats['project_effort'],90)
        self.assertEqual((rows[root]['effort'],rows[root]['rolled_effort']),(20,90))
        self.assertEqual(rows[child]['rolled_effort'],70)
        self.assertEqual(sum(r['effort'] for r in stats['project_departments']),90)
        self.assertEqual(next(d for d in stats['departments'] if d['name']=='研发部')['other'],10)
        self.assertEqual(self.stats(member,'personal')[1]['project_effort'],90)

    def test_reminders_authorization_and_submitted_race(self):
        uid,member=self.add_member()
        outside,_=self.add_member('其他部门','otherdept')
        self.update_person(outside,department='市场部')
        mid,manager=self.add_member('负责人','manager')
        self.update_person(mid,role='department_manager')
        payload=dict(scope='department',week=current_week(),user_ids=[uid])
        self.assertEqual(member.request('POST','/api/reminders',payload)[0],403)
        self.assertEqual(manager.request('POST','/api/reminders',{**payload,'user_ids':[outside]})[0],403)
        for _ in range(2):
            self.assertEqual(manager.request('POST','/api/reminders',payload)[1]['count'],1)
        self.assertEqual(len(member.state()[1]['reminders']),1)
        self.assertEqual(member.request('PUT','/api/reminders/read',{})[0],200)
        self.assertEqual(member.state()[1]['reminders'],[])
        self.assertEqual(self.submit(member,[],[dict(category='learning',effort=100,note='培训')])[0],200)
        self.assertEqual(manager.request('POST','/api/reminders',payload)[1]['count'],0)

    def test_disabled_submit_and_scope_grants(self):
        uid,member=self.add_member()
        pid,_=self.project('项目',[uid])
        self.assertEqual(self.update_person(uid,permissions={'report_submit':False,'stats_personal':False,'stats_company':True})[0],200)
        self.assertEqual(self.submit(member,[self.entry(pid)])[0],403)
        self.assertEqual(self.upload(member,pid)[0],403)
        self.assertEqual(self.stats(member,'personal')[0],403)
        self.assertEqual(self.stats(member,'company')[0],200)
        self.assertNotIn(uid,self.stats()[1]['pending_ids'])
        self.assertEqual(self.update_person(uid,permissions={'unknown':True})[0],400)
        self.assertEqual(self.update_person(uid,permissions={'stats_company':1})[0],400)

    def test_existing_database_migration_is_idempotent(self):
        uid,member=self.add_member()
        pid,_=self.project('历史项目',[uid])
        self.assertEqual(member.request('PUT','/api/reports',dict(week=current_week(),entries=[dict(project_id=pid,effort=60,progress=30,note='旧版成果')],leave_percent=40,leave_note='旧版休假'))[0],200)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('DELETE FROM non_project_entries')
        initialize(self.path)
        initialize(self.path)
        state=member.state()[1]
        self.assertEqual(len(state['non_project_entries']),1)
        self.assertEqual(state['non_project_entries'][0]['effort'],40)
        self.assertEqual(state['reports'][0]['note'],'旧版成果')
        self.assertEqual(state['reports'][0]['next_plan'],'')
        self.assertEqual(len(state['projects'][0]['assignments']),1)

    def test_120_members_submit_at_deadline(self):
        root,_=self.project('百人项目')
        child,_=self.project('阶段交付',parent=root)
        clients=[]
        with closing(sqlite3.connect(self.path)) as db, db:
            password=db.execute('SELECT password FROM users WHERE id=1').fetchone()[0]
            for i in range(120):
                uid=db.execute("INSERT INTO users(name,username,password,department,created_at) VALUES(?,?,?,?,?)",
                    (f'并发成员{i}',f'burst_{i}',password,'研发部',timestamp())).lastrowid
                db.executemany('INSERT INTO memberships(project_id,user_id) VALUES(?,?)',[(root,uid),(child,uid)])
                token=f'test-burst-token-{uid}'
                db.execute('INSERT INTO sessions VALUES(?,?,?,?)',(hashlib.sha256(token.encode()).hexdigest(),uid,'burst-csrf',time.time()+3600))
                client=legacy.Client(self.server.server_port)
                client.cookie=f'pms_session={token}'
                client.csrf='burst-csrf'
                clients.append(client)
        start=threading.Event()
        def submit(client):
            start.wait()
            return self.submit(client,[self.entry(root,30),self.entry(child,50)],
                [dict(category='leave',effort=10,note='休假'),dict(category='learning',effort=10,note='学习')])
        with ThreadPoolExecutor(max_workers=120) as pool:
            pending=[pool.submit(submit,client) for client in clients]
            start.set()
            results=[r.result() for r in pending]
        self.assertTrue(all(status==200 for status,_ in results),results)
        stats=self.stats()[1]
        self.assertEqual(stats['submitted'],120)
        self.assertEqual(stats['project_effort'],9600)
        self.assertEqual(stats['categories']['leave'],1200)
        self.assertEqual(stats['categories']['learning'],1200)


if __name__ == '__main__':
    unittest.main()
