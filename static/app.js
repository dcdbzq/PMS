'use strict';

const $ = (selector, root = document) => root.querySelector(selector);
const app = $('#app');
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const paths = {
  home:'<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
  folder:'<path d="M3 7a2 2 0 0 1 2-2h5l2 2h7a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"/><path d="M3 10h18"/>',
  edit:'<path d="m15 4 5 5M4 20l5-1L20 8a2.1 2.1 0 0 0-5-5L4 14Z"/>',
  users:'<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2m20 0v-2a4 4 0 0 0-3-3.9M16 3a4 4 0 0 1 0 8"/><circle cx="9" cy="7" r="4"/>',
  chart:'<path d="M4 3v17h17M8 15l4-5 4 2 5-6"/>',
  check:'<path d="m5 12 4 4L19 6"/>',
  refresh:'<path d="M20 7v5h-5M4 17v-5h5M5 8a8 8 0 0 1 13-3l2 3M4 16l2 3a8 8 0 0 0 13-3"/>',
  calendar:'<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18m-13 4h2m4 0h2"/>',
  plus:'<path d="M12 5v14M5 12h14"/>',
  right:'<path d="m9 5 7 7-7 7"/>', left:'<path d="m15 5-7 7 7 7"/>',
  arrow:'<path d="M4 12h16m-6-6 6 6-6 6"/>', down:'<path d="m7 10 5 5 5-5"/>',
  search:'<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  close:'<path d="m6 6 12 12M6 18 18 6"/>',
  download:'<path d="M12 3v12m-5-5 5 5 5-5M4 15v5h16v-5"/>',
  clock:'<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
  leaf:'<path d="M20 3c0 12-4 17-11 17a6 6 0 0 1-6-6C3 7 8 3 20 3ZM4 20 15 9"/>',
  info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-10v.1"/>',
  logout:'<path d="M9 4H4v16h5m0-8h12m-4-4 4 4-4 4"/>',
  lock:'<rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3m-4 4v3"/>',
  copy:'<rect x="8" y="8" width="13" height="13" rx="2"/><path d="M16 8V3H3v13h5"/>',
  split:'<path d="M5 4v16m14-16v16M9 9h6m-6 6h6"/>',
  archive:'<rect x="3" y="3" width="18" height="4" rx="1"/><path d="M5 7v14h14V7m-10 5h6"/>',
  sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5"/>',
  bell:'<path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/>',
  message:'<path d="M21 14a3 3 0 0 1-3 3H8l-5 4V6a3 3 0 0 1 3-3h12a3 3 0 0 1 3 3ZM7 8h10M7 12h6"/>',
  'check-circle':'<circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/>',
  book:'<path d="M12 5C8 2 4 3 2 4v15c3-1 6-1 10 2 4-3 7-3 10-2V4c-2-1-6-2-10 1Zm0 0v16"/>',
  code:'<path d="m8 5-6 7 6 7m8-14 6 7-6 7m-3-15-2 18"/>',
};
const icon = name => `<svg class="icon" viewBox="0 0 24 24" aria-hidden="true">${paths[name] || paths.folder}</svg>`;
const brand = () => '<div class="brand"><img src="/favicon.svg" alt=""><div><strong>轻舟</strong><small>QINGZHOU · PMS</small></div></div>';
const button = (label, action, type = '', symbol = '') => `<button type="button" class="button ${type}" data-action="${action}">${symbol ? icon(symbol) : ''}${label}</button>`;
const iconButton = (symbol, action, title, disabled = false) => `<button type="button" class="icon-button" data-action="${action}" title="${title}" aria-label="${title}" ${disabled ? 'disabled' : ''}>${icon(symbol)}</button>`;
const avatar = (user, size = '', index = 0) => `<span class="avatar ${size} ${index % 4 ? `tone-${index % 4}` : ''}" title="${esc(user?.name || '成员')}">${esc((user?.name || '?').slice(-2))}</span>`;
const empty = (title, message, action = '', compact = false) => `<div class="empty ${compact ? 'compact' : ''}"><div class="empty-icon">${icon('leaf')}</div><h3>${title}</h3><p>${message}</p>${action}</div>`;
const number = value => Number(value || 0).toLocaleString('zh-CN', {maximumFractionDigits:1});
let state = null, setup = false, route = 'overview', viewWeek = '', reportWeek = '', projectFilter = 'active', search = '', draft = null, dirty = false;
let dialog = null;
let authMode = 'local', authError = '';
let uploading = false;

function weekSubmission(userId, week = viewWeek) {
  return state.submissions.find(s => s.user_id === userId && s.week === week);
}
function leaveHTML(userId, week = viewWeek) {
  const s = weekSubmission(userId, week);
  return s?.leave_percent > 0 ? `<div class="leave-status"><span class="badge amber">${s.leave_percent === 100 ? '整周休假' : `休假 ${number(s.leave_percent)}%`}</span><span>${esc(s.leave_note)}</span></div>` : '';
}
function submissionLabel(userId, week) {
  if(person(userId)?.role==='admin' || person(userId)?.permissions?.report_submit===false) return '无需填报';
  const s = weekSubmission(userId, week);
  return !s ? '未填报' : s.leave_percent === 100 ? '已登记整周休假' : s.leave_percent > 0 ? `已填报 · 休假 ${number(s.leave_percent)}%` : '已填报';
}
function reportAttachments(entry, userId, week) {
  return (state.attachments || []).filter(a => a.attached && a.project_id === entry.project_id && a.user_id === userId && a.week === week);
}
function fileSize(size) { return size >= 1048576 ? `${number(size / 1048576)} MB` : `${number(Math.max(0.1, size / 1024))} KB`; }
function attachmentLinks(files, editable = false) {
  return files.length ? `<ul class="attachment-list">${files.map(a=>`<li><a href="/api/attachments/${a.id}" download>${icon('download')}<span>${esc(a.filename)}</span><small>${fileSize(a.size)}</small></a>${editable ? `<button type="button" class="icon-button" data-action="remove-attachment" data-id="${a.id}" aria-label="移除附件 ${esc(a.filename)}">${icon('close')}</button>` : ''}</li>`).join('')}</ul>` : '';
}
function attachmentEditor(entry, index) {
  const files = (entry.attachment_ids || []).map(id=>(state.attachments || []).find(a=>a.id === id)).filter(Boolean);
  return `<div class="report-attachments"><label class="field-label" for="attachments-${index}">周报附件</label>${attachmentLinks(files,true)}<input type="file" id="attachments-${index}" class="attachment-input" multiple aria-label="上传项目周报附件"><p class="hint">每个文件最多 20 MB，每条项目周报最多 10 个。提交周报后，有权查看该条周报的同事可点击下载；复用上周不会复制附件。</p></div>`;
}
function leaveOverview() {
  const leaves = state.submissions.filter(s=>s.week === viewWeek && s.leave_percent > 0);
  if (!leaves.length) return '';
  return `<section class="leave-overview panel panel-pad"><div class="section-head"><h2>本周休假情况</h2><span class="small muted">${leaves.length} 位成员已登记</span></div>${leaves.map(s=>`<div class="leave-person">${avatar(person(s.user_id))}<strong>${esc(person(s.user_id)?.name)}</strong>${leaveHTML(s.user_id)}</div>`).join('')}</section>`;
}

function dateAdd(value, days) {
  const d = new Date(`${value}T12:00:00Z`);
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0,10);
}
function weekLabel(week, full = false) {
  return `${full ? week.replaceAll('-', '.') : week.slice(5).replace('-', '.') } — ${dateAdd(week,6).slice(5).replace('-', '.')}`;
}
function displayTime(value) {
  return value ? value.slice(5,16).replace('T',' ').replace('-', '/') : '尚未填报';
}
function person(id) { return state.users.find(u => u.id === Number(id)); }
function project(id) { return state.projects.find(p => p.id === Number(id)); }
function latestReport(projectId, userId, week = viewWeek || state.week) {
  return state.reports.find(r => r.project_id === projectId && r.user_id === userId && r.week <= week);
}
function projectProgress(p, week = viewWeek || state.week) {
  if (!p.member_ids.length) return 0;
  return Math.round(p.member_ids.reduce((sum,id) => sum + (latestReport(p.id,id,week)?.progress || 0),0) / p.member_ids.length);
}
function membersWithProjects() {
  const ids = new Set(state.projects.filter(p => !p.archived).flatMap(p => p.member_ids));
  return state.users.filter(u => u.active && ids.has(u.id));
}
function submittedIds(week = viewWeek) {
  return new Set(state.submissions.filter(s => s.week === week).map(s => s.user_id));
}
function projectBadge(p) {
  if (p.effective_archived) return '<span class="badge gray">已归档</span>';
  if (projectProgress(p) >= 100) return '<span class="badge">已完成开发</span>';
  if (p.due_date < state.today) return '<span class="badge red">已逾期</span>';
  if (p.priority === 'high') return '<span class="badge amber">高优先级</span>';
  return '<span class="badge">进行中</span>';
}

async function api(path, data, method = 'GET') {
  const response = await fetch(path, {
    method, credentials:'same-origin',
    headers:{'Content-Type':'application/json', ...(state?.csrf ? {'X-CSRF-Token':state.csrf} : {})},
    ...(data ? {body:JSON.stringify(data)} : {}),
  });
  const result = await response.json();
  if (!response.ok) {
    if (response.status === 401 && !['/api/login','/api/setup'].includes(path)) {
      state = null; draft = null; dirty = false; closeModal(); renderAuth();
    }
    throw new Error(result.error || '请求失败，请稍后重试');
  }
  return result;
}
function toast(message, error = false) {
  $('#toasts').innerHTML = `<div class="toast ${error ? 'error' : ''}" role="status">${esc(message)}</div>`;
  setTimeout(() => $('#toasts').replaceChildren(), 4000);
}
async function refresh(renderPage = true) {
  state = await api('/api/state');
  analyticsCache.clear();
  viewWeek ||= state.week;
  reportWeek ||= state.week;
  if (renderPage) render();
}
async function start() {
  try {
    const data = await api('/api/bootstrap');
    authMode = data.auth_mode || 'local';
    setup = data.needs_setup;
    if (new URLSearchParams(location.search).has('auth_error')) {
      authError = '飞书登录未完成。请重新授权；若仍失败，请联系管理员检查通讯录同步状态、应用使用范围和账号状态。';
      history.replaceState(null, '', location.pathname + location.hash);
    }
    if (!data.authenticated) return renderAuth();
    await refresh(false);
    route = location.hash.slice(1) || 'overview';
    render();
  } catch (error) {
    app.innerHTML = `<div class="connection-error">${brand()}<h2>暂时无法打开工作空间</h2><p class="muted">${esc(error.message)}</p>${button('重新加载','retry','primary','arrow')}</div>`;
  }
}
function renderAuth(error = '') {
  app.innerHTML = `<div class="auth-layout"><section class="auth-story">${brand()}<div class="story-text"><div class="eyebrow">LESS MANAGEMENT. MORE PROGRESS.</div><h1>每一份进展，<br>都清晰可见。</h1><p class="subtitle">简单记录，轻松协作。<br>用一点时间，让团队走在同一个方向。</p></div><div class="story-visual" aria-hidden="true"><div class="visual-head"><span class="project-symbol">${icon('folder')}</span>团队项目<span class="badge">有序推进</span></div><div class="visual-title">向前一步<small>每周都看得见</small></div><div class="visual-bars">${[21,29,40,44,56,64,72,82].map(h=>`<i style="height:${h}%"></i>`).join('')}</div><div class="visual-footer"><span>记录 · 同步 · 完成</span><span>持续积累的每一份努力 ↗</span></div></div><div class="story-foot">一个轻量的工作空间，只关注项目和人的进展。</div></section><section class="auth-form-side"><div class="mobile-brand">${brand()}</div><form id="auth-form" class="auth-form"><div class="eyebrow">${setup ? 'START YOUR WORKSPACE' : 'WELCOME BACK'}</div><h2>${setup ? '建立你的工作空间' : '欢迎回到轻舟'}</h2><p>${setup ? '创建第一个管理员账号，然后邀请成员一起记录项目进展。' : '登录后，看看项目的最新进展，记录这一周的努力。'}</p><div id="auth-error">${error ? `<div class="inline-error">${esc(error)}</div>` : ''}</div>${setup ? '<div class="field"><label for="auth-name">你的姓名</label><input id="auth-name" name="name" placeholder="怎么称呼你" maxlength="30" required autocomplete="name"></div>' : ''}<div class="field"><label for="auth-username">登录名</label><input id="auth-username" name="username" placeholder="${setup ? '3–32 位字母、数字或下划线' : '输入你的登录名'}" maxlength="32" ${setup ? 'pattern="[a-zA-Z0-9_.-]{3,32}"' : ''} required autocomplete="username"></div><div class="field"><label for="auth-password">密码</label><input id="auth-password" type="password" name="password" placeholder="${setup ? '设置密码，至少 8 位' : '输入你的密码'}" ${setup ? 'minlength="8"' : ''} maxlength="128" required autocomplete="${setup ? 'new-password' : 'current-password'}"></div><button type="submit" class="button primary">${setup ? '创建工作空间' : '进入工作空间'}${icon('arrow')}</button><p class="auth-hint">${setup ? '账号与数据保存在当前服务中' : '账号由管理员创建 · 忘记密码请联系管理员'}</p></form></section></div>`;
  if (authMode === 'feishu') {
    $('.auth-form').innerHTML = `<div class="eyebrow">YOUR TEAM. CONNECTED.</div><h2>用飞书，进入轻舟</h2><p class="feishu-login-copy">使用公司的飞书账号登录，成员信息会从通讯录自动同步。</p>${error || authError ? `<div class="inline-error" role="alert">${esc(error || authError)}</div>` : ''}<a href="/api/auth/feishu/start" class="button primary">${icon('arrow')}使用飞书登录</a><p class="auth-hint">飞书内可继续授权 · 普通浏览器可通过飞书授权页登录<br>无需注册，也无需设置额外密码</p>`;
  }
}

function render() {
  if (!state) return renderAuth();
  if (!['overview','projects','report','members'].includes(route) || (route === 'members' && state.user.role !== 'admin') || (route === 'report' && !state.permissions.report_submit)) route = 'overview';
  const nav = [['overview','home','工作总览'],['projects','folder','项目空间'],...(state.permissions.report_submit ? [['report','edit','我的填报']] : []), ...(state.user.role === 'admin' ? [['members','users','团队成员']] : [])];
  const title = nav.find(item => item[0] === route)[2];
  const body = {overview:overviewPage, projects:projectsPage, report:reportPage, members:membersPage}[route]();
  app.innerHTML = `<aside class="sidebar">${brand()}<div class="nav-label">WORKSPACE</div><nav class="nav" aria-label="主导航">${nav.map(([key,symbol,label])=>`<a href="#${key}" class="${key === route ? 'active' : ''}" ${key === route ? 'aria-current="page"' : ''}>${icon(symbol)}<span>${label}</span>${key === route ? '<i class="nav-dot"></i>' : ''}</a>`).join('')}</nav><div class="sidebar-foot"><div class="workspace-note"><strong>${icon('leaf')}简单一点，走得更远</strong>专注每一份真实的进展。</div><div class="sidebar-bottom"><span>轻舟 · 轻量协作</span><span>v2.0</span></div></div></aside><div class="main-shell"><header class="topbar"><div class="breadcrumb">${icon('home')}<span class="home-crumb">我的工作空间</span><span>/</span><strong>${title}</strong></div><div class="topbar-right">${iconButton('refresh','refresh','刷新最新进展')}<span class="top-date">${state.today.replaceAll('-','.')} · ${['周日','周一','周二','周三','周四','周五','周六'][new Date(`${state.today}T12:00:00Z`).getUTCDay()]}</span><button type="button" class="user-button" data-action="account" aria-label="账户设置">${avatar(state.user)}<span class="name">${esc(state.user.name)}</span>${icon('down')}</button></div></header><main class="content ${route === 'projects' ? 'projects-page' : ''}">${body}<footer class="footer"><span>轻舟 QINGZHOU · 让协作更轻，让进展更清晰</span><span>少一点管理，多一点进展。</span></footer></main></div>`;
}
function weekSwitch(kind) {
  const week = kind === 'report' ? reportWeek : viewWeek;
  return `<div class="week-switch">${iconButton('left',`${kind}-prev`,'上一周')}<span>${week === state.week ? '本周 · ' : ''}${weekLabel(week)}</span>${iconButton('right',`${kind}-next`,'下一周', week >= state.week)}</div>`;
}
function projectCard(p, personal=false) {
  const latest=latestReport(p.id,state.user.id), progress=personal ? Number(latest?.progress || 0) : projectProgress(p);
  const people=p.member_ids.map(person).filter(Boolean), parent=project(p.parent_id);
  return `<button type="button" class="project-card" data-action="detail" data-id="${p.id}"><div class="card-top"><span class="project-symbol tone-${p.id % 4}">${icon(p.id % 2?'code':'folder')}</span>${projectBadge(p)}</div>${parent?`<span class="project-parent-caption">${esc(parent.name)}</span>`:''}<h3>${esc(p.name)}</h3><p class="description">${esc(p.description || '一起把目标，一步步向前推进。')}</p><div class="progress-label"><span>${personal?'我的累计进度':'成员平均进度'}</span><strong>${progress}%</strong></div><div class="progress-track"><div class="progress-fill" style="width:${progress}%"></div></div><div class="card-foot"><div class="people"><div class="avatar-stack">${people.slice(0,3).map((u,i)=>avatar(u,'',i)).join('')}</div><span>${people.length} 位成员</span></div><div class="deadline">${icon('calendar')}${p.due_date.slice(5).replace('-','/')} 截止</div></div></button>`;
}
function draftKey() { return `qingzhou-draft-${state.user.id}-${reportWeek}`; }
function prepareDraft() {
  if (draft?.week === reportWeek && draft.user === state.user.id) return;
  const existing = state.reports.filter(r => r.week === reportWeek && r.user_id === state.user.id);
  const submission = weekSubmission(state.user.id, reportWeek);
  draft = {week:reportWeek, user:state.user.id, leave_percent:submission?.leave_percent || 0, leave_note:submission?.leave_note || '', entries:existing.map(r=>({project_id:r.project_id, progress:r.progress, effort:r.effort, note:r.note, next_plan:r.next_plan || '', coordination:r.coordination || '', attachment_ids:reportAttachments(r,state.user.id,reportWeek).map(a=>a.id)}))};
  draft.non_project_entries = (state.non_project_entries || []).filter(e=>e.user_id === state.user.id && e.week === reportWeek).map(e=>({category:e.category,effort:e.effort,note:e.note}));
  dirty = false;
  try {
    const saved = JSON.parse(localStorage.getItem(draftKey()));
    if (saved && saved.user === state.user.id && saved.week === reportWeek && Array.isArray(saved.entries)) {
      draft = saved; dirty = true;
    }
  } catch { /* A draft is optional; server records are authoritative. */ }
  draft.non_project_entries ??= draft.leave_percent > 0 ? [{category:'leave',effort:draft.leave_percent,note:draft.leave_note || ''}] : [];
  draft.leave_percent ??= 0;
  draft.leave_note ??= '';
  draft.entries.forEach(entry => {
    entry.next_plan ??= ''; entry.coordination ??= '';
    entry.attachment_ids ??= reportAttachments(entry,state.user.id,reportWeek).map(a=>a.id);
    entry.attachment_ids = entry.attachment_ids.filter(id=>(state.attachments || []).some(a=>a.id === id));
  });
}
function saveDraft() {
  dirty = true;
  try { localStorage.setItem(draftKey(), JSON.stringify(draft)); } catch { /* Storage may be unavailable. */ }
  updateTotal();
}
function availableProjects() {
  return state.projects.filter(p=>myAssignment(p,reportWeek) && state.permissions.report_submit);
}
function membersPage() {
  if (state.auth_mode === 'feishu') return feishuMembersPage();
  const active = state.users.filter(u=>u.active);
  return `<div class="page-head"><div><div class="eyebrow">TEAM MEMBERS</div><h1>团队成员</h1><p>让每一位成员找到自己的项目，记录自己的进展。</p></div><div class="head-actions"><a href="/api/export" class="button">${icon('download')}导出填报记录</a><a href="/api/export/attachments" class="button">${icon('download')}导出全部附件</a>${button('添加成员','new-member','primary','plus')}</div></div><div class="member-summary"><span>团队成员 <strong>${state.users.length}</strong> 人</span><span>启用中 <strong>${active.length}</strong> 人</span><span>管理员 <strong>${active.filter(u=>u.role === 'admin').length}</strong> 人</span></div><div class="panel table-wrap"><table class="data-table"><thead><tr><th>成员</th><th>部门</th><th>角色</th><th>参与项目</th><th>本周填报</th><th>账号状态</th><th>操作</th></tr></thead><tbody>${state.users.map((u,i)=>`<tr><td><div class="person">${avatar(u,'',i)}<div class="member-name">${esc(u.name)}${u.id === state.user.id ? '（我）' : ''}<small>${esc(u.username)}</small></div></div></td><td class="muted">${esc(u.department || '—')}</td><td><span class="badge ${u.role === 'admin' ? '' : 'gray'}">${esc(memberRoleLabel(u))}</span></td><td class="muted">${state.projects.filter(p=>!p.archived && p.member_ids.includes(u.id)).length} 个</td><td><span class="badge ${submittedIds(state.week).has(u.id) ? '' : 'amber'}">${submissionLabel(u.id,state.week)}</span></td><td><span class="badge ${u.active ? '' : 'gray'}">${u.active ? '启用' : '停用'}</span></td><td><button type="button" class="text-button" data-action="edit-member" data-id="${u.id}">${icon('edit')}编辑</button></td></tr>`).join('')}</tbody></table></div><p class="small muted" style="font-size:11px;margin-top:18px;line-height:1.9">新增成员后，将账号和初始密码告知本人，再在项目中添加成员。停用账号会保留所有历史记录。</p>`;
}

function feishuMembersPage() {
  const directory = state.directory || {};
  const latest = directory.latest;
  const users = state.users.filter(u=>u.auth_source === 'feishu');
  const active = users.filter(u=>u.active);
  return `<div class="page-head"><div><div class="eyebrow">TEAM DIRECTORY</div><h1>团队通讯录</h1><p>人员跟随飞书同步，团队的进展在这里相聚。</p></div><div class="head-actions"><a href="/api/export" class="button">${icon('download')}导出填报记录</a><a href="/api/export/attachments" class="button">${icon('download')}导出全部附件</a>${button(directory.running ? '同步中…' : '立即同步','sync-feishu','primary','refresh')}</div></div><div class="directory-summary"><div>${icon('users')}<div><strong>飞书通讯录 · 自动同步</strong><p>最近成功同步：${directory.last_success ? esc(directory.last_success.slice(0,19).replace('T',' ')) : '尚未完成'} · 每 ${Math.round((directory.sync_interval || 900)/60)} 分钟核对</p></div><span class="badge ${latest?.status === 'failed' ? 'red' : ''}">${directory.running ? '同步中' : latest?.status === 'failed' ? '同步失败' : directory.last_success ? '已同步' : '待同步'}</span></div>${latest?.status === 'failed' ? `<div class="inline-error">${esc(latest.message)}</div>` : ''}<p>姓名、部门与账号状态由飞书维护。这里可以调整业务角色，并在项目中分配参与成员。</p></div><div class="member-summary"><span>同步成员 <strong>${users.length}</strong> 人</span><span>启用中 <strong>${active.length}</strong> 人</span><span>管理员 <strong>${active.filter(u=>u.role === 'admin').length}</strong> 人</span></div><div class="panel table-wrap">${users.length ? `<table class="data-table"><thead><tr><th>成员</th><th>所属部门</th><th>业务角色</th><th>参与项目</th><th>本周填报</th><th>飞书状态</th><th>操作</th></tr></thead><tbody>${users.map((u,i)=>`<tr><td><div class="person">${avatar(u,'',i)}<div class="member-name">${esc(u.name)}${u.id === state.user.id ? '（我）' : ''}<small>飞书企业成员</small></div></div></td><td class="muted">${esc(u.department || '—')}</td><td><span class="badge ${u.role === 'admin' ? '' : 'gray'}">${esc(memberRoleLabel(u))}</span></td><td class="muted">${state.projects.filter(p=>!p.archived && p.member_ids.includes(u.id)).length} 个</td><td><span class="badge ${submittedIds(state.week).has(u.id) ? '' : 'amber'}">${submissionLabel(u.id,state.week)}</span></td><td><span class="badge ${u.active ? '' : 'gray'}">${u.active ? '在职 · 已激活' : u.employment_status === 'not_in_directory' ? '不在通讯录范围' : '未激活或已停用'}</span></td><td><button type="button" class="text-button" data-action="edit-role" data-id="${u.id}">${icon('edit')}调整角色</button></td></tr>`).join('')}</tbody></table>` : empty('正在等待通讯录的第一次同步','配置应用权限及全员通讯录读取范围后，点击立即同步。','',true)}</div>`;
}

function openModal(title,subtitle,body,foot = '',wide = false) {
  closeModal();
  const root = $('#modal-root');
  root.innerHTML = `<dialog ${wide ? 'class="wide"' : ''} aria-labelledby="modal-title"><header class="modal-head"><div><h2 id="modal-title">${title}</h2><p>${subtitle}</p></div>${iconButton('close','close-modal','关闭窗口')}</header>${body}${foot ? `<footer class="modal-foot">${foot}</footer>` : ''}</dialog>`;
  dialog = $('dialog',root);
  const current = dialog;
  dialog.addEventListener('close',()=>{current.remove(); if (dialog === current) dialog = null;});
  dialog.showModal();
}
function closeModal() { if (dialog) dialog.close(); }
function field(label,id,content,full = false,hint = '') {
  return `<div class="field ${full ? 'full' : ''}"><label for="${id}">${label}</label>${content}${hint ? `<p class="hint">${hint}</p>` : ''}</div>`;
}
function accountModal() {
  openModal('我的账户','简单协作，从这里开始。',`<div class="modal-body account-menu"><div class="account-info">${avatar(state.user,'lg')}<div><h3>${esc(state.user.name)}</h3><p>${state.auth_mode === 'feishu' ? '飞书企业账号' : esc(state.user.username)} · ${esc(roleCaption())}</p></div></div>${state.auth_mode === 'feishu' ? '<p class="small muted">使用飞书企业身份登录，账号由飞书管理。</p>' : button('修改密码','change-password','','lock')}${button('退出登录','logout','','logout')}</div>`);
}
function passwordModal() {
  openModal('修改密码','设置一个只有你知道的密码。',`<form id="password-form"><div class="modal-body"><div id="form-error"></div><div class="form-grid">${field('原密码','old-password','<input id="old-password" name="old_password" type="password" required autocomplete="current-password">',true)}${field('新密码','new-password','<input id="new-password" name="password" type="password" minlength="8" maxlength="128" required autocomplete="new-password">',true,'至少 8 位，修改后其他设备上的登录会失效。')}${field('确认新密码','confirm-password','<input id="confirm-password" name="confirm" type="password" required autocomplete="new-password">',true)}</div></div><footer class="modal-foot">${button('取消','close-modal')}<button type="submit" class="button primary">保存新密码</button></footer></form>`);
}
function addRowModal() {
  const available = availableProjects().filter(p=>!draft.entries.some(e=>e.project_id === p.id));
  if (!available.length) return toast('已经添加了所有参与中的项目');
  openModal('添加参与的项目','选择你本周投入精力的项目。',`<div class="modal-body account-menu">${available.map(p=>`<button type="button" class="button" data-action="choose-project" data-id="${p.id}">${icon('folder')}${esc(p.name)}</button>`).join('')}</div>`);
}
function addDraftProject(id) {
  if (!availableProjects().some(p=>p.id === Number(id))) return;
  if (!draft.entries.some(e=>e.project_id === Number(id))) {
    draft.entries.push({project_id:Number(id), progress:latestReport(Number(id),state.user.id,reportWeek)?.progress || 0, effort:draft.entries.length ? 0 : Math.max(0,100 - otherTotal()), note:'',next_plan:'',coordination:'',attachment_ids:[]});
    saveDraft();
  }
  closeModal(); render(); updateTotal();
}
function changeWeek(kind,value) {
  if (kind === 'report') {
    if (dirty && !confirm('当前草稿已保存在浏览器。切换填报周？')) return render();
    reportWeek = value; draft = null; dirty = false;
  } else viewWeek = value;
  render(); updateTotal();
}

document.addEventListener('click',async event=>{
  if (uploading && event.target.closest('a,button')) {
    event.preventDefault();
    return toast('文件正在上传，请稍候');
  }
  const target = event.target.closest('[data-action]');
  if (!target || target.disabled) return;
  const action = target.dataset.action, id = Number(target.dataset.id);
  try {
    if (action === 'retry') return start();
    if (action === 'refresh') { target.disabled = true; await refresh(); updateTotal(); return toast('已更新最新进展'); }
    if (action === 'close-modal') return closeModal();
    if (action === 'new-project') return projectModal();
    if (action === 'edit-project') return projectModal(id);
    if (action === 'detail') return detailModal(id);
    if (action === 'new-member') return memberModal();
    if (action === 'edit-role') return roleModal(id);
    if (action === 'sync-feishu') {
      target.disabled = true; target.textContent = '正在同步…';
      try { await api('/api/feishu/sync',{},'POST'); }
      finally { await refresh(); }
      return toast('飞书通讯录已同步');
    }
    if (action === 'edit-member') return memberModal(id);
    if (action === 'account') return accountModal();
    if (action === 'change-password') return passwordModal();
    if (action === 'logout') {
      target.disabled = true;
      await api('/api/logout',{},'POST');
      state = null; draft = null; dirty = false; closeModal();
      viewWeek = ''; reportWeek = ''; setup = false; renderAuth(); return;
    }
    if (action === 'go-report' || action === 'report-project') {
      reportWeek = state.week; draft = null; dirty = false; route = 'report';
      closeModal(); location.hash = 'report'; render(); updateTotal();
      if (action === 'report-project') addDraftProject(id);
      return;
    }
    if (action === 'filter') { projectFilter = target.dataset.value; return render(); }
    if (/^(view|report)-(prev|next)$/.test(action)) {
      const [kind,dir] = action.split('-');
      return changeWeek(kind,dateAdd(kind === 'report' ? reportWeek : viewWeek,dir === 'prev' ? -7 : 7));
    }
    if (action === 'add-row') return addRowModal();
    if (action === 'choose-project') return addDraftProject(id);
    if (action === 'remove-row') {
      draft.entries.splice(Number(target.closest('[data-row]').dataset.row),1);
      saveDraft(); render(); return updateTotal();
    }
    if (action === 'remove-attachment') {
      const entry = draft.entries[Number(target.closest('[data-row]').dataset.row)];
      entry.attachment_ids = entry.attachment_ids.filter(aid=>aid !== id);
      saveDraft(); render(); return updateTotal();
    }
    if (action === 'split-effort') {
      if (!draft.entries.length) return toast('先添加参与的项目');
      const units = Math.round((100 - otherTotal()) * 2);
      if (units < 0 || units > 200) return toast('请先填写正确的非项目工作量',true);
      const halfUnits = Math.floor(units / draft.entries.length), remainder = units % draft.entries.length;
      draft.entries.forEach((e,i)=>e.effort=(halfUnits+(i<remainder ? 1 : 0))/2);
      saveDraft(); render(); return updateTotal();
    }
    if (action === 'copy-week') {
      const previous = state.reports.filter(r=>r.user_id === state.user.id && r.week === dateAdd(reportWeek,-7) && availableProjects().some(p=>p.id === r.project_id));
      if (!previous.length) return toast('上周没有可复用的进行中项目记录');
      if (draft.entries.length && !confirm('复用上周会替换当前草稿的项目、投入比例和开发进度，进展说明与附件将留空。继续？')) return;
      draft.entries = previous.map(r=>({project_id:r.project_id,progress:r.progress,effort:r.effort,note:'',next_plan:'',coordination:'',attachment_ids:[]}));
      saveDraft(); render(); updateTotal(); toast('已复用上周项目，请补充本周进展');
    }
  } catch (error) { toast(error.message,true); target.disabled = false; }
});

document.addEventListener('input',event=>{
  const target = event.target;
  if (target.id === 'leave-percent' || target.id === 'leave-note') {
    draft.leave_percent = $('#leave-percent').value === '' ? '' : Number($('#leave-percent').value);
    draft.leave_note = $('#leave-note').value;
    $('#leave-note').required = Number(draft.leave_percent) > 0;
    saveDraft();
  }
  if (target.id === 'project-search') {
    search = target.value;
    $('#project-list').innerHTML = projectTree(filteredProjects());
  }
  if (target.dataset.field && target.closest('[data-row]')) {
    const row = target.closest('[data-row]');
    const entry = draft.entries[Number(row.dataset.row)];
    const field = target.dataset.field;
    if (['note','next_plan','coordination'].includes(field)) entry[field] = target.value;
    else if (field === 'progress-range') {
      entry.progress = Number(target.value); $('[data-field="progress"]',row).value = target.value;
    } else {
      entry[field] = target.value === '' ? '' : Number(target.value);
      if (field === 'progress') $('[data-field="progress-range"]',row).value = target.value;
    }
    saveDraft();
  }
});
document.addEventListener('change',async event=>{
  if (event.target.matches('.attachment-input')) {
    const input = event.target;
    const entry = draft.entries[Number(input.closest('[data-row]').dataset.row)];
    const files = Array.from(input.files);
    entry.attachment_ids ||= [];
    if (!files.length) return;
    if (entry.attachment_ids.length + files.length > (state.attachment_max_count || 10)) {
      input.value = ''; return toast('每条项目周报最多 10 个附件',true);
    }
    if (files.some(file=>!file.size || file.size > state.attachment_max_size)) {
      input.value = ''; return toast('请选择非空文件，每个文件最大 20 MB',true);
    }
    const editingDraft = draft, userId = state.user.id;
    uploading = true; input.disabled = true; updateTotal();
    try {
      for (const file of files) {
        const params = new URLSearchParams({week:editingDraft.week,project_id:entry.project_id,filename:file.name});
        const response = await fetch(`/api/attachments?${params}`, {method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/octet-stream','X-CSRF-Token':state.csrf},body:file});
        const result = await response.json();
        if (!response.ok) throw new Error(result.error || '附件上传失败');
        if (!state || state.user.id !== userId) break;
        state.attachments.push(result);
        entry.attachment_ids.push(result.id);
        if (draft === editingDraft) saveDraft();
      }
      toast('附件已上传，提交周报后同事可见');
    } catch (error) { toast(error.message,true); }
    finally { uploading = false; render(); updateTotal(); }
    return;
  }
  if (event.target.id === 'report-date') {
    const value = event.target.value;
    if (!value) return;
    const date = new Date(`${value}T12:00:00Z`);
    const monday = dateAdd(value,-((date.getUTCDay()+6)%7));
    if (monday > state.week) {toast('只能填写本周或之前的进展',true);return render();}
    changeWeek('report',monday);
  }
});
document.addEventListener('submit',async event=>{
  event.preventDefault();
  const form = event.target;
  const submit = $('button[type="submit"]',form);
  if (!submit || submit.disabled) return;
  const values = Object.fromEntries(new FormData(form));
  submit.disabled = true;
  const oldLabel = submit.innerHTML;
  submit.textContent = '正在保存…';
  try {
    if (form.id === 'auth-form') {
      await api(setup ? '/api/setup' : '/api/login',values,'POST');
      draft = null; dirty = false; viewWeek = ''; reportWeek = ''; route = 'overview'; board = ''; projectRelation = ''; statisticsScope = ''; analysisMember = ''; analysisProject = '';
      location.hash = 'overview'; await refresh(); toast(setup ? '工作空间已创建，欢迎使用轻舟' : '欢迎回来'); setup = false;
    } else if (form.id === 'project-form') {
      const id = form.dataset.id;
      const formData = new FormData(form);
      const member_ids = [...new Set(formData.getAll('member_ids').map(Number))];
      const assignments = member_ids.map(user_id=>{
        const row = form.querySelector(`.assignment-row[data-user="${user_id}"]`);
        return {user_id,start_date:row?.querySelector('[data-period="start_date"]').value || '',end_date:row?.querySelector('[data-period="end_date"]').value || ''};
      });
      const data = {...values,owner_id:Number(values.owner_id),parent_id:values.parent_id ? Number(values.parent_id) : null,member_ids,assignments,viewer_ids:formData.getAll('viewer_ids').map(Number),archived:formData.has('archived')};
      const result=await api(`/api/projects${id ? '/'+id : ''}`,data,id ? 'PUT' : 'POST');
      closeModal(); await refresh(false); revealProject(result.id); render(); toast(id ? '项目已更新' : '项目已创建');
    } else if (form.id === 'role-form') {
      await api(`/api/users/${form.dataset.id}/role`,{...values,...grantsPayload(form)},'PUT');
      closeModal(); await refresh(); toast('业务角色已更新');
    } else if (form.id === 'member-form') {
      const id = form.dataset.id;
      await api(`/api/users${id ? '/'+id : ''}`,{...values,...grantsPayload(form),active:values.active === '1'},id ? 'PUT' : 'POST');
      closeModal(); await refresh(); toast(id ? '成员信息已更新' : '成员已添加，可以分配项目了');
    } else if (form.id === 'password-form') {
      if (values.password !== values.confirm) throw new Error('两次输入的新密码不一致');
      await api('/api/password',values,'PUT'); closeModal(); toast('密码已修改');
    } else if (form.id === 'report-form') {
      await api('/api/reports',{week:reportWeek,entries:draft.entries,non_project_entries:draft.non_project_entries},'PUT');
      try { localStorage.removeItem(draftKey()); } catch { /* optional local draft */ }
      draft = null; dirty = false; await refresh(); updateTotal(); toast('本周进展已提交，辛苦啦');
    }
  } catch (error) {
    const root = form.id === 'auth-form' ? $('#auth-error') : $('#form-error',form);
    if (root) root.innerHTML = `<div class="inline-error" role="alert">${esc(error.message)}</div>`;
    else toast(error.message,true);
  } finally {
    if (submit.isConnected) {submit.disabled = false;submit.innerHTML = oldLabel; if (form.id === 'report-form') updateTotal();}
  }
});
window.addEventListener('hashchange',()=>{if (state) {route=location.hash.slice(1); render(); updateTotal();}});
window.addEventListener('beforeunload',event=>{if(dirty || uploading){event.preventDefault();event.returnValue='';}});
