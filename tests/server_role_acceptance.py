"""Read-only business acceptance with manual trial credentials kept outside source."""
import argparse
import json
from pathlib import Path
from server_acceptance import Client, read_admin


def subtree(projects, roots):
    result=set(roots)
    while True:
        updated=result | {p['id'] for p in projects if p['parent_id'] in result}
        if updated==result:return result
        result=updated


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--admin-file',required=True)
    parser.add_argument('--accounts-file',required=True)
    parser.add_argument('--out',required=True)
    args=parser.parse_args()
    fixture=json.loads(Path(args.accounts_file).read_text(encoding='utf-8'))
    info=read_admin(args.admin_file)
    admin=Client(fixture['url']);full=admin.login(info['username'],info['password'])
    checks=[]
    counts={k:len(full[k]) for k in ('users','projects','reports','submissions','attachments')}
    accounts={a['username']:a for a in fixture['accounts']}
    for key in ['owner_a','owner_b','member','dept_manager']:
        account=accounts['trial1015_'+key]
        client=Client(fixture['url']);state=client.login(account['username'],account['password']);uid=state['user']['id']
        managed=subtree(full['projects'],{p['id'] for p in full['projects'] if p['owner_id']==uid or (key=='dept_manager' and p['department']=='人工体验')})
        owned=subtree(full['projects'],{p['id'] for p in full['projects'] if p['owner_id']==uid})
        viewers=subtree(full['projects'],{p['id'] for p in full['projects'] if uid in p['viewer_ids']})
        people={u['id'] for u in full['users'] if key=='dept_manager' and u['department']=='人工体验'} | {uid}
        expected={r['id'] for r in full['reports'] if r['user_id'] in people or r['project_id'] in managed|viewers}
        assert {r['id'] for r in state['reports']}==expected,key+' narrative boundary'
        assert {p['id'] for p in state['projects'] if p['can_manage']}==managed,key+' management scope'
        assert all(n['user_id'] in people for n in state['non_project_entries']),key+' nonproject privacy'
        if key.startswith('owner'):
            assert state['capabilities']['project_management']
            stats=client.request('GET','/api/analytics?scope=project&week='+state['week'])
            assert stats['project_effort']==sum(r['effort'] for r in full['reports'] if r['week']==state['week'] and r['project_id'] in owned)
            client.request('GET','/api/analytics?scope=department',expected=(403,))
            assert all(n['user_id']==uid for n in state['non_project_entries'])
            checks.append(key+': project tree, project statistics and private other-work isolation')
        elif key=='dept_manager':
            assert state['capabilities']['department_management'] and state['capabilities']['create_project']
            stats=client.request('GET','/api/analytics?scope=department&week='+state['week'])
            assert stats['project_effort']==sum(r['effort'] for r in full['reports'] if r['week']==state['week'] and r['user_id'] in people)
            assert all(m['department']=='人工体验' for m in stats['members'])
            client.request('GET','/api/analytics?scope=company',expected=(403,))
            checks.append('department manager: all department projects/members, scoped statistics, company denied')
        else:
            assert not any(state['capabilities'].values())
            client.request('GET','/api/analytics?scope=project',expected=(403,))
            client.request('GET','/api/analytics?scope=department',expected=(403,))
            checks.append('participant: own reports only unless explicit viewer, no management statistics')
        forbidden=next((p for p in full['projects'] if p['id'] not in managed),None)
        if forbidden:
            client.request('PUT',f"/api/projects/{forbidden['id']}",dict(forbidden,name='权限检查不可写入'),expected=(403,))
        attachment=next((a for a in full['attachments'] if a['attached'] and a['user_id'] not in people and a['project_id'] not in managed|viewers),None)
        if attachment:
            client.request('GET',f"/api/attachments/{attachment['id']}",expected=(403,))
    after=admin.request('GET','/api/state')
    assert {k:len(after[k]) for k in counts}==counts,'Business counts changed during read-only acceptance'
    checks.append('direct unauthorized writes/downloads rejected; business object counts unchanged')
    report=dict(passed=True,url=fixture['url'],checks=checks,counts=counts)
    Path(args.out).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(passed=True,checks=len(checks)),ensure_ascii=False))


if __name__=='__main__':main()
