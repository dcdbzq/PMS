"""Add a separate department manager to the existing manual trial, without resetting anyone."""
import argparse
import json
import secrets
from pathlib import Path
from server_acceptance import Client, ROOT, read_admin


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--admin-file',required=True)
    parser.add_argument('--accounts-file',required=True)
    parser.add_argument('--guide-out',required=True)
    args=parser.parse_args()
    target,guide=Path(args.accounts_file).resolve(),Path(args.guide_out).resolve()
    assert not target.is_relative_to(ROOT) and not guide.is_relative_to(ROOT)
    fixture=json.loads(target.read_text(encoding='utf-8'))
    info=read_admin(args.admin_file)
    client=Client(fixture['url']);state=client.login(info['username'],info['password'])
    account=next((a for a in fixture['accounts'] if a['username']=='trial1015_dept_manager'),None)
    if account is None:
        assert not any(u['username']=='trial1015_dept_manager' for u in state['users']), 'Existing account not in fixture; do not reset credentials'
        account=dict(name='体验部门负责人',username='trial1015_dept_manager',password=secrets.token_urlsafe(18),role='department_manager',department='人工体验',active=True)
        fixture['accounts'].append(account)
        # Save generated credentials before the HTTP write for recovery.
        target.write_text(json.dumps(fixture,ensure_ascii=False,indent=2),encoding='utf-8')
    existing=next((u for u in state['users'] if u['username']==account['username']),None)
    if existing:
        assert existing['role']=='department_manager' and existing['department']=='人工体验'
        account['id']=existing['id']
    else:
        account['id']=client.request('POST','/api/users',account)['id']
    Client(fixture['url']).login(account['username'],account['password'])
    target.write_text(json.dumps(fixture,ensure_ascii=False,indent=2),encoding='utf-8')
    descriptions={
        'trial1015_admin':'公司管理员：公司总览、账号权限及全部项目管理。',
        'trial1015_owner_a':'项目负责人甲：默认进入负责项目，可管理客户协作平台及其子项目；其他参与项目在“我参与的”中查看。',
        'trial1015_owner_b':'项目负责人乙：默认进入负责项目，可管理数据看板及其子项目。',
        'trial1015_member':'普通参与人：只看自己的工作总览和周报，不能查看同项目其他人的周报正文，除非配置为查看人。',
        'trial1015_dept_manager':'部门负责人：默认进入部门项目，可查看人工体验部门成员的全部工作，管理部门项目、指定负责人、查看未填报并提醒。',
    }
    lines=['轻舟 PMS 人工体验账号（密码请保密）','地址：'+fixture['url'],'',
        '2026-10-21：已区分项目负责人、部门负责人和普通参与人。原有账号密码保持不变，新增独立的体验部门负责人。',
        '请使用“人工体验”账号与项目操作；不要修改“验收”项目、账号或历史周报。','']
    for a in fixture['accounts']:
        lines += [descriptions.get(a['username'],a['name']), '姓名：'+a['name'],'用户名：'+a['username'],'密码：'+a['password'],'']
    lines += ['建议体验顺序：',
        '1. 负责人甲：查看负责项目、配置参与人及参与时间、添加子项目；在项目空间切换“我负责的 / 我参与的 / 全部可见”。',
        '2. 部门负责人：查看部门项目和人员投入，进入待办与异常查看未更新周报、协调事项及逾期项目；可以新建归属人工体验部门的项目。',
        '3. 普通参与人：填写自己的周报，总工作量须为100%；不展示部门管理指标。',
        '4. 查看人：在项目配置中给普通成员增加查看身份，再登录验证授权范围内的项目周报。',
        '负责人可管理项目，不必在全部阶段作为参与人；仅勾选实际需要填报的节点。',
        '项目负责人仅查看负责项目内的周报，成员的其他项目、休假和培训详情不会因此开放。',
        '工作量提示仅用于协调，多项目并行以单周4个及以上有投入记录的项目作为提示；不新增审批流程。']
    guide.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(accounts=len(fixture['accounts']),department_manager_id=account['id'],guide=str(guide)),ensure_ascii=False))


if __name__=='__main__':main()
