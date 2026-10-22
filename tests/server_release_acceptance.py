"""Real-server release checks using a new, isolated and clearly marked QA cohort.

Existing business objects are compared before/after; never modify them. Credentials
are stored separately from reports, source and screenshots.
"""
import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date,timedelta
from urllib.parse import urlencode
import zipfile

from server_acceptance import Client,Metrics,read_admin


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--admin-file',required=True)
    parser.add_argument('--credentials-out',required=True)
    parser.add_argument('--existing-accounts-file',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    out=Path(args.out).resolve();out.mkdir(parents=True,exist_ok=True)
    credential_path=Path(args.credentials_out).resolve()
    if credential_path.is_relative_to(Path(__file__).resolve().parents[1]):
        raise ValueError('Keep credentials outside the repository')
    assert not credential_path.exists(),'Use a new credentials file'
    info=read_admin(args.admin_file)
    url=info['url'];metrics=Metrics();checks=[];created={};project_ids={}
    admin=Client(url,metrics,timeout=60)
    before=admin.login(info['username'],info['password'])
    prefix='u'+date.fromisoformat(before['today']).strftime('%m%d')+secrets.token_hex(2)
    label=f'【更新验收{prefix}】'
    dept=label+'研发';outside_dept=label+'其他'
    week=before['week'];week_end=(date.fromisoformat(week)+timedelta(days=6)).isoformat()
    start=(date.fromisoformat(week)-timedelta(days=14)).isoformat()
    end=(date.fromisoformat(week)+timedelta(days=60)).isoformat()
    result={'passed':False,'url':url,'checks':checks,'prefix':prefix}

    def check(name,condition=True):
        checks.append({'name':name,'passed':bool(condition)})
        assert condition,name

    def save_credentials():
        credential_path.write_text(json.dumps(dict(url=url,prefix=prefix,accounts=created,project_ids=project_ids),ensure_ascii=False,indent=2),encoding='utf-8')
        os.chmod(credential_path,0o600)

    def account(key,role='member',department=None,permissions=None):
        data=dict(name=label+key,username=prefix+'_'+key,password=secrets.token_urlsafe(18),role=role,department=department or dept,active=True)
        if permissions is not None:data['permissions']=permissions
        response=admin.request('POST','/api/users',data)
        created[key]={'id':response['id'],**data};save_credentials()
        client=Client(url,metrics,timeout=60)
        client.login(data['username'],data['password'])
        return client,response['id']

    def project(key,owner,members=(),parent=None,viewers=(),assignments=None,client=None):
        data=dict(name=label+key,description='更新版本验收数据',owner_id=owner,member_ids=list(members),viewer_ids=list(viewers),parent_id=parent,
                  start_date=start,due_date=end,priority='normal',archived=False)
        if assignments is not None:data['assignments']=assignments
        response=(client or admin).request('POST','/api/projects',data)
        project_ids[key]=response['id'];save_credentials()
        return response['id'],data

    def entry(pid,effort,note='完成接口与页面验收',coordination=''):
        return dict(project_id=pid,progress=45,effort=effort,note=label+note,next_plan='下周继续联调与验收',coordination=coordination,attachment_ids=[])

    def submit(client,entries,others=(),expected=(200,),selected_week=None):
        return client.request('PUT','/api/reports',dict(week=selected_week or week,entries=entries,non_project_entries=list(others)),expected=expected)

    try:
        for asset in ('/','/app.js','/management.js','/overview.js','/projects.js','/styles.css','/refinement.css'):
            body=admin.request('GET',asset)
            local=Path(__file__).resolve().parents[1]/'static'/('index.html' if asset=='/' else asset.lstrip('/'))
            check('正式地址文件与本地发布一致 '+asset,hashlib.sha256(body).digest()==hashlib.sha256(local.read_bytes()).digest())
        baseline_company=admin.request('GET',f'/api/analytics?scope=company&week={week}')
        manager,mid=account('manager','department_manager')
        member,uid=account('member')
        second,sid=account('second')
        viewer,vid=account('viewer',department=outside_dept,permissions={'report_submit':False})
        outsider,oid=account('outsider',department=outside_dept)
        analyst,aid=account('analyst',permissions={'report_submit':False,'report_view':False,'stats_department':True})
        root,_=project('root',mid,[mid],viewers=[vid])
        child,child_data=project('child',mid,[mid,uid],root,assignments=[dict(user_id=uid,start_date=week,end_date=week_end)])
        leaf,_=project('leaf',mid,[mid,sid],child)
        past_end=(date.fromisoformat(week)-timedelta(days=1)).isoformat()
        expired,_=project('expired',mid,[mid,uid],root,assignments=[dict(user_id=uid,start_date=start,end_date=past_end)])
        private,_=project('private',before['user']['id'],[oid])
        managed,_=project('manager-created',mid,[],root,client=manager)
        state=admin.request('GET','/api/state')
        newroot=next(p for p in state['projects'] if p['id']==root)
        check('创建人自动加入查看人',before['user']['id'] in newroot['viewer_ids'])
        check('项目不提供编号字段','approval_ref' not in newroot)
        manager_created=next(p for p in state['projects'] if p['id']==managed)
        check('负责人创建子项目并成为查看人',mid in manager_created['viewer_ids'])
        admin.request('PUT',f'/api/projects/{root}',{**child_data,'parent_id':leaf},expected=(400,))
        check('循环层级被拒绝')
        submit(member,[entry(expired,100)],expected=(403,))
        submit(outsider,[entry(child,100)],expected=(403,))
        submit(viewer,[entry(child,100)],expected=(403,))
        member.request('GET','/api/analytics?scope=department',expected=(403,))
        member.request('GET','/api/analytics?scope=company',expected=(403,))
        member.request('GET','/api/export',expected=(403,))
        check('参与时间、查看人只读、跨项目及管理统计权限生效')
        content=('更新验收附件：权限、成果及计划。\n'*100).encode()
        filename='更新验收证据.txt'
        upload='/api/attachments?'+urlencode(dict(project_id=child,week=week,filename=filename))
        member.request('POST',upload,b'',expected=(413,),headers={'Content-Type':'application/octet-stream'})
        attachment=member.request('POST',upload,content,expected=(201,),headers={'Content-Type':'application/octet-stream'})
        download=f"/api/attachments/{attachment['id']}"
        viewer.request('GET',download,expected=(403,))
        primary=entry(child,70,coordination='需要产品在周四前确认字段')
        primary['attachment_ids']=[attachment['id']]
        others=[dict(category='leave',effort=10,note='半天休假'),dict(category='learning',effort=20,note='技术研究与培训')]
        submit(member,[primary],others)
        submit(member,[{**primary,'effort':69.5}],others,expected=(400,))
        member.request('PUT','/api/reports',dict(week=week,entries=[primary],non_project_entries=others),expected=(403,),headers={'X-CSRF-Token':'incorrect'})
        future=(date.fromisoformat(week)+timedelta(days=7)).isoformat()
        submit(member,[primary],others,expected=(400,),selected_week=future)
        check('100%校验、未来周限制及CSRF防护')
        member_rows=[r for r in member.request('GET','/api/state')['reports'] if r['user_id']==uid and r['week']==week]
        check('非法提交保留已保存周报',len(member_rows)==1 and member_rows[0]['effort']==70 and member_rows[0]['next_plan']==primary['next_plan'])
        submit(second,[entry(leaf,50)],[dict(category='other',effort=50,note='团队例会与其他工作')])
        submit(outsider,[entry(private,100,note='其他部门私有项目')])
        check('成果、计划、协调事项及三种非项目分类保存成功')
        check('查看人下载已发布附件',hashlib.sha256(viewer.request('GET',download)).digest()==hashlib.sha256(content).digest())
        outsider.request('GET',download,expected=(403,))
        admin.request('PUT',f'/api/projects/{child}',{**child_data,'archived':True})
        submit(second,[entry(leaf,50)],[dict(category='other',effort=50,note='团队例会与其他工作')])
        admin.request('PUT',f'/api/projects/{child}',child_data)
        check('归档历史条目仍可修改，重新开启成功')
        dept=manager.request('GET',f'/api/analytics?scope=department&week={week}')
        check('部门范围仅统计授权部门',dept['expected']==3 and dept['submitted']==2 and dept['project_effort']==120 and oid not in {m['id'] for m in dept['members']})
        rollup=next(p for p in dept['projects'] if p['id']==root)
        check('父子项目工作量汇总不重复',rollup['effort']==0 and rollup['rolled_effort']==120)
        check('部门协调事项可见',len(dept['coordination'])==1)
        management_state=manager.request('GET','/api/state')
        check('其他部门私有项目与周报隔离',private not in {p['id'] for p in management_state['projects']} and oid not in {r['user_id'] for r in management_state['reports']})
        stats_only=analyst.request('GET',f'/api/analytics?scope=department&week={week}')
        analyst_state=analyst.request('GET','/api/state')
        check('统计和周报正文权限分离',stats_only['project_effort']==120 and not stats_only['coordination'] and not analyst_state['reports'] and not analyst_state['attachments'])
        analyst.request('GET',download,expected=(403,))
        company=admin.request('GET',f'/api/analytics?scope=company&week={week}')
        check('公司统计新增投入及分类正确',company['project_effort']==baseline_company['project_effort']+220 and company['categories']['leave']==baseline_company['categories']['leave']+10)
        payload=dict(scope='department',week=week,user_ids=[mid])
        member.request('POST','/api/reminders',payload,expected=(403,))
        manager.request('POST','/api/reminders',{**payload,'user_ids':[oid]},expected=(403,))
        check('站内提醒发送成功',manager.request('POST','/api/reminders',payload)['count']==1)
        check('已提交成员不重复提醒',manager.request('POST','/api/reminders',{**payload,'user_ids':[uid]})['count']==0)
        check('提醒在接收人账号中可见',len(manager.request('GET','/api/state')['reminders'])==1)
        exported=admin.request('GET','/api/export').decode('utf-8-sig')
        rows=list(csv.reader(io.StringIO(exported)))
        check('CSV字段完整且不含项目编号',all(len(r)==len(rows[0]) for r in rows) and '立项编号' not in rows[0] and filename in exported)
        with zipfile.ZipFile(io.BytesIO(admin.request('GET','/api/export/attachments'))) as bundle:
            matches=[n for n in bundle.namelist() if n.endswith(f"{attachment['id']}-{filename}")]
            check('附件ZIP字节校验通过',bundle.testzip() is None and len(matches)==1 and bundle.read(matches[0])==content)
        after=admin.request('GET','/api/state')
        for table in ('users','projects','reports','submissions','attachments'):
            key=lambda item: item['id'] if 'id' in item else (item['user_id'],item['week'])
            old={key(item):item for item in before[table]};new={key(item):item for item in after[table]}
            check('原有业务数据保持不变 '+table,all(new.get(k)==v for k,v in old.items()))
        result['functional_passed']=True
        print(f'Functional checks passed: {len(checks)}',flush=True)
        # Same company-size read scenario, existing QA users; no writes to their data.
        cohort=json.loads(Path(args.existing_accounts_file).read_text(encoding='utf-8'))
        clients=[]
        for user in cohort['accounts'][:100]:
            client=Client(url,metrics,'load-login',timeout=90)
            client.login(user['username'],user['password']);client.phase='concurrent-read';clients.append(client)
        for concurrency in (10,25,50,100):
            event=threading.Event()
            def read(client):
                event.wait()
                for _ in range(3):client.request('GET','/api/state')
                return True
            for c in clients[:concurrency]:c.phase=f'read-{concurrency}'
            with ThreadPoolExecutor(max_workers=concurrency) as pool:
                pending=[pool.submit(read,c) for c in clients[:concurrency]]
                event.set();assert all(p.result() for p in pending)
            print(f'{concurrency} concurrent readers: completed',flush=True)
        result['performance']=metrics.summary()
        read100=next(r for r in result['performance'] if r['phase']=='read-100' and r['path']=='/api/state')
        result['read_100_p95_ms']=read100['p95_ms']
        result['read_100_target_2000ms_passed']=read100['p95_ms']<=2000
        result['passed']=True
        result['test_accounts_created']=len(created)
        result['test_projects_created']=len(project_ids)
        print(json.dumps({k:v for k,v in result.items() if k not in ('checks','performance')},ensure_ascii=False),flush=True)
    finally:
        result.setdefault('performance',metrics.summary())
        (out/'report.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        save_credentials()


if __name__=='__main__':main()
