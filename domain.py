"""WBS、业务授权和统计；仅依赖标准库，不接入外部通讯或身份服务。"""
import json
from datetime import date, timedelta


PERMISSIONS = ('report_submit', 'report_view', 'stats_personal', 'stats_department', 'stats_company')
CATEGORIES = {'leave': '休假', 'learning': '学习/研究/培训', 'other': '其他'}


def initialize_domain(db):
    migrate_department = 'department' not in {r[1] for r in db.execute('PRAGMA table_info(projects)')}
    for table, columns in {
        'users': {'permissions': "TEXT NOT NULL DEFAULT '{}'", 'managed_departments': "TEXT NOT NULL DEFAULT '[]'"},
        'projects': {'parent_id': 'INTEGER REFERENCES projects(id)', 'department': "TEXT NOT NULL DEFAULT ''"},
        'memberships': {'start_date': "TEXT NOT NULL DEFAULT ''", 'end_date': "TEXT NOT NULL DEFAULT ''"},
        'reports': {'next_plan': "TEXT NOT NULL DEFAULT ''", 'coordination': "TEXT NOT NULL DEFAULT ''",
                    'coordination_completed': 'INTEGER NOT NULL DEFAULT 0',
                    'coordination_completed_by': 'INTEGER REFERENCES users(id)',
                    'coordination_completed_at': "TEXT NOT NULL DEFAULT ''"},
    }.items():
        existing = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
        for name, definition in columns.items():
            if name not in existing:
                db.execute(f'ALTER TABLE {table} ADD COLUMN {name} {definition}')
    db.executescript('''
        CREATE TABLE IF NOT EXISTS project_viewers (
            project_id INTEGER NOT NULL REFERENCES projects(id), user_id INTEGER NOT NULL REFERENCES users(id),
            PRIMARY KEY(project_id,user_id));
        CREATE INDEX IF NOT EXISTS viewers_user ON project_viewers(user_id,project_id);
        CREATE INDEX IF NOT EXISTS projects_parent ON projects(parent_id);
        CREATE TABLE IF NOT EXISTS non_project_entries (
            user_id INTEGER NOT NULL REFERENCES users(id), week TEXT NOT NULL,
            category TEXT NOT NULL CHECK(category IN ('leave','learning','other')),
            effort REAL NOT NULL CHECK(effort > 0 AND effort <= 100), note TEXT NOT NULL,
            PRIMARY KEY(user_id,week,category));
        CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL REFERENCES users(id),
            sender_id INTEGER NOT NULL REFERENCES users(id), week TEXT NOT NULL,
            created_at TEXT NOT NULL, read_at TEXT, UNIQUE(user_id,sender_id,week));
    ''')
    # Preserve old leave registrations, including repeated startup/migration.
    db.execute('''INSERT OR IGNORE INTO non_project_entries(user_id,week,category,effort,note)
        SELECT s.user_id,s.week,'leave',s.leave_percent,s.leave_note FROM submissions s
        JOIN users u ON u.id=s.user_id WHERE s.leave_percent>0''')
    if migrate_department:
        # Persist responsibility once; changing a person's department must not
        # silently transfer a project's management rights. Children inherit it.
        projects = {r['id']: dict(r) for r in db.execute('SELECT * FROM projects')}
        owners = {r['id']: r['department'] for r in db.execute('SELECT id,department FROM users')}
        for p in projects.values():
            root, seen = p, set()
            while root['parent_id'] in projects and root['id'] not in seen:
                seen.add(root['id'])
                root = projects[root['parent_id']]
            db.execute('UPDATE projects SET department=? WHERE id=?', (owners.get(root['owner_id'], ''), p['id']))


def permissions(user):
    result = dict.fromkeys(PERMISSIONS, False)
    result.update(report_submit=True, report_view=True, stats_personal=True)
    if user['role'] == 'department_manager':
        result['stats_department'] = True
    if user['role'] == 'admin':
        result.update(stats_department=True, stats_company=True)
    result.update(json.loads(user['permissions']))
    # Administrators manage company work; old explicit grants must not make
    # them expected reporters again after this role policy changes.
    if user['role'] == 'admin':
        result.update(report_submit=False, stats_personal=False)
    return result


def departments(user):
    configured = json.loads(user['managed_departments'])
    return set(configured or ([user['department']] if user['department'] else []))


def department_manager(user):
    return user['role'] == 'department_manager'


def descendants(projects, roots):
    result = set(roots)
    children = {}
    for p in projects.values():
        children.setdefault(p['parent_id'], []).append(p['id'])
    pending = list(roots)
    while pending:
        for child in children.get(pending.pop(), []):
            if child not in result:
                result.add(child)
                pending.append(child)
    return result


def context(db, user):
    projects = {r['id']: dict(r) for r in db.execute('SELECT * FROM projects ORDER BY archived,id DESC')}
    people = {r['id']: dict(r) for r in db.execute('SELECT * FROM users ORDER BY id')}
    assignments = [dict(r) for r in db.execute('SELECT * FROM memberships ORDER BY user_id')]
    viewers = [dict(r) for r in db.execute('SELECT * FROM project_viewers ORDER BY user_id')]
    grants = permissions(user)
    owned = descendants(projects, {p['id'] for p in projects.values() if p['owner_id'] == user['id']})
    department_projects = descendants(projects, {p['id'] for p in projects.values() if p['department'] and p['department'] in departments(user)})
    managed = owned | (department_projects if department_manager(user) else set())
    if user['role'] == 'admin':
        managed = set(projects)
    read_projects = managed | descendants(projects, {v['project_id'] for v in viewers if v['user_id'] == user['id']})
    direct = read_projects | {m['project_id'] for m in assignments if m['user_id'] == user['id']}
    direct |= {r[0] for r in db.execute('SELECT DISTINCT project_id FROM reports WHERE user_id=?', (user['id'],))}
    scope_people = {user['id']}
    if grants['stats_company']:
        scope_people = set(people)
    elif grants['stats_department']:
        scope_people |= {u['id'] for u in people.values() if u['department'] in departments(user)}
    if grants['stats_company'] or user['role'] == 'admin':
        reports = [dict(r) for r in db.execute('SELECT * FROM reports ORDER BY week DESC,updated_at DESC')]
    else:
        # Restrict history in SQLite before materializing rows for ordinary staff.
        direct_marks = ','.join('?' for _ in read_projects) or 'NULL'
        people_marks = ','.join('?' for _ in scope_people)
        reports = [dict(r) for r in db.execute(f'''SELECT * FROM reports
            WHERE project_id IN ({direct_marks}) OR user_id IN ({people_marks})
            ORDER BY week DESC,updated_at DESC''', (*sorted(read_projects), *sorted(scope_people)))]
    scoped = {m['project_id'] for m in assignments if m['user_id'] in scope_people}
    scoped |= {r['project_id'] for r in reports if r['user_id'] in scope_people}
    # Also include projects owned by department colleagues with no current
    # participants/reports; responsibility is independent of participation.
    scoped |= {p['id'] for p in projects.values() if p['owner_id'] in scope_people}
    if grants['stats_department'] or department_manager(user):
        scoped |= department_projects
    visible = direct | scoped
    if grants['stats_company']:
        visible = set(projects)
    department_people = {u['id'] for u in people.values() if u['department'] in departments(user)}
    department_visible = department_projects | {m['project_id'] for m in assignments if m['user_id'] in department_people}
    department_visible |= {r['project_id'] for r in reports if r['user_id'] in department_people}
    department_visible |= {p['id'] for p in projects.values() if p['owner_id'] in department_people}
    context_only = set()
    for pid in list(visible):
        seen = set()
        while pid in projects and projects[pid]['parent_id']:
            pid = projects[pid]['parent_id']
            if pid in seen:
                break
            seen.add(pid)
            if pid not in visible:
                context_only.add(pid)
    return dict(projects=projects, people=people, assignments=assignments, viewers=viewers, reports=reports,
                grants=grants, managed=managed, owned=owned, read_projects=read_projects,
                department_projects=department_projects, department_visible=department_visible, direct=direct, scope_people=scope_people,
                visible=visible, context_only=context_only)


def can_read_report(ctx, user, report):
    return report['user_id'] == user['id'] or (ctx['grants']['report_view'] and
        (report['project_id'] in ctx['read_projects'] or report['user_id'] in ctx['scope_people']))


def can_manage_coordination(ctx, user, report):
    return bool(report['coordination'].strip() and can_read_report(ctx, user, report) and (
        report['user_id'] == user['id'] or user['role'] == 'admin' or
        report['project_id'] in ctx['managed'] or
        (department_manager(user) and ctx['people'][report['user_id']]['department'] in departments(user))))


def report_data(ctx, user, report):
    return dict(report, can_manage_coordination=can_manage_coordination(ctx, user, report))


def active_assignment(member, project, week):
    end = (date.fromisoformat(week) + timedelta(days=6)).isoformat()
    # Planned deadlines can pass while work continues; only explicit assignment
    # dates constrain reporting. Empty dates preserve legacy project membership.
    return (not member['start_date'] or member['start_date'] <= end) and (not member['end_date'] or member['end_date'] >= week)


def archived_in_tree(projects, project_id):
    seen = set()
    while project_id in projects and project_id not in seen:
        seen.add(project_id)
        p = projects[project_id]
        if p['archived']:
            return True
        project_id = p['parent_id']
    return False


def statistics(db, user, ctx, scope, week):
    """Return totals without disclosing weekly narrative to statistics-only users."""
    end = (date.fromisoformat(week) + timedelta(days=6)).isoformat()
    project_scope = scope == 'project'
    ids = ({m['user_id'] for m in ctx['assignments'] if m['project_id'] in ctx['owned'] and
            active_assignment(m, ctx['projects'][m['project_id']], week) and
            not archived_in_tree(ctx['projects'], m['project_id'])} |
           {r['user_id'] for r in ctx['reports'] if r['project_id'] in ctx['owned'] and r['week'] == week}) if project_scope else (
        {user['id']} if scope == 'personal' else set(ctx['people']) if scope == 'company' else {
        u['id'] for u in ctx['people'].values() if u['department'] in departments(user)})
    people = [u for u in ctx['people'].values() if u['id'] in ids]
    eligible = {u['id'] for u in people if u['active'] and permissions(u)['report_submit'] and u['created_at'][:10] <= end}
    submissions = {r['user_id']: dict(r) for r in db.execute('SELECT * FROM submissions WHERE week=?', (week,)) if r['user_id'] in ids}
    reports = [r for r in ctx['reports'] if r['user_id'] in ids and r['week'] == week and
               (not project_scope or r['project_id'] in ctx['owned'])]
    non_project = [] if project_scope else [dict(r) for r in db.execute('SELECT * FROM non_project_entries WHERE week=?', (week,)) if r['user_id'] in ids]
    pids = {r['project_id'] for r in reports} | {m['project_id'] for m in ctx['assignments'] if m['user_id'] in ids and
        active_assignment(m, ctx['projects'][m['project_id']], week) and not archived_in_tree(ctx['projects'], m['project_id'])}
    if project_scope:
        pids = set(ctx['owned'])
    elif scope == 'department':
        pids |= {p['id'] for p in ctx['projects'].values() if p['id'] in ctx['department_projects'] and p['start_date'] <= end and not archived_in_tree(ctx['projects'], p['id'])}
    elif scope == 'company':
        pids |= {p['id'] for p in ctx['projects'].values() if p['start_date'] <= end and not archived_in_tree(ctx['projects'], p['id'])}
    pids = descendants_ancestors(ctx['projects'], pids)
    project_rows = []
    for pid in pids:
        p = ctx['projects'][pid]
        subtree = descendants(ctx['projects'], {pid})
        rows = [r for r in reports if r['project_id'] in subtree]
        direct = [r for r in reports if r['project_id'] == pid]
        project_rows.append(dict(id=pid, name=p['name'], parent_id=p['parent_id'], due_date=p['due_date'], department=p['department'],
            archived=archived_in_tree(ctx['projects'], pid), effort=sum(r['effort'] for r in direct),
            rolled_effort=sum(r['effort'] for r in rows), contributors=len({r['user_id'] for r in rows}),
            records=len(rows), context_only=pid not in ctx['owned'] if project_scope else pid in ctx['context_only']))
    members = []
    for u in people:
        own = [r for r in reports if r['user_id'] == u['id']]
        other = [r for r in non_project if r['user_id'] == u['id']]
        members.append(dict(id=u['id'], name=u['name'], department=u['department'], active=bool(u['active']),
            expected=u['id'] in eligible, submitted=u['id'] in submissions, projects=len({r['project_id'] for r in own}),
            project_effort=sum(r['effort'] for r in own), non_project_effort=sum(r['effort'] for r in other), project_reported=bool(own),
            categories={k: sum(r['effort'] for r in other if r['category'] == k) for k in CATEGORIES}))
    dept_rows = []
    for dept in sorted({m['department'] for m in members}):
        rows = [m for m in members if m['department'] == dept]
        dept_rows.append(dict(name=dept or '未分配部门', expected=sum(m['expected'] for m in rows),
            submitted=sum(m['expected'] and m['submitted'] for m in rows),
            project_effort=sum(m['project_effort'] for m in rows),
            **{k: sum(m['categories'][k] for m in rows) for k in CATEGORIES}))
    project_department = {}
    for r in reports:
        key = (r['project_id'], ctx['people'][r['user_id']]['department'])
        project_department[key] = project_department.get(key, 0) + r['effort']
    return dict(scope=scope, week=week, expected=len(eligible), submitted=len(eligible & submissions.keys()),
        pending_ids=sorted(eligible - submissions.keys()), members=members, projects=project_rows, departments=dept_rows,
        project_departments=[dict(project_id=pid, department=d or '未分配部门', effort=e) for (pid,d),e in project_department.items()],
        project_effort=sum(r['effort'] for r in reports),
        categories={k: sum(r['effort'] for r in non_project if r['category'] == k) for k in CATEGORIES},
        coordination=[report_data(ctx,user,r) for r in reports if r['coordination'].strip() and not r['coordination_completed'] and can_read_report(ctx, user, r)],
        coordination_completed=[report_data(ctx,user,r) for r in reports if r['coordination'].strip() and r['coordination_completed'] and can_read_report(ctx, user, r)])


def descendants_ancestors(projects, pids):
    result = set(pids)
    for pid in list(pids):
        seen = set()
        while pid in projects and projects[pid]['parent_id']:
            pid = projects[pid]['parent_id']
            if pid in seen:
                break
            seen.add(pid)
            result.add(pid)
    return result
