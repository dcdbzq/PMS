'use strict';

const categories = {leave:'休假',learning:'学习/研究/培训',other:'其他'};
const roleNames = {member:'普通参与人',department_manager:'部门负责人',admin:'公司管理员'};
const permissionNames = {report_submit:'填报本人周报',report_view:'查看授权范围内的周报',stats_personal:'个人统计',stats_department:'部门统计及提醒',stats_company:'公司统计及提醒'};
let board = '', statisticsScope = '', analysisMember = '', analysisProject = '';
let analyticsCache = new Map();

function projectPath(p) {
  const names = [], seen = new Set();
  while (p && !seen.has(p.id)) { seen.add(p.id); names.unshift(p.name); p = project(p.parent_id); }
  return names.join(' / ');
}
function treeRows(items) {
  const ids = new Set(items.map(p=>p.id)), result = [], seen = new Set();
  function walk(parent, depth) {
    items.filter(p=>p.parent_id === parent).sort((a,b)=>a.id-b.id).forEach(p=>{
      if (seen.has(p.id)) return;
      seen.add(p.id); result.push({...p,depth}); walk(p.id,depth+1);
    });
  }
  items.filter(p=>!ids.has(p.parent_id)).sort((a,b)=>a.id-b.id).forEach(p=>{
    if (!seen.has(p.id)) { seen.add(p.id); result.push({...p,depth:0}); walk(p.id,1); }
  });
  return result;
}
function myAssignment(p, week) {
  return !p.effective_archived && p.assignments?.some(m=>m.user_id === state.user.id &&
    (!m.start_date || m.start_date <= dateAdd(week,6)) && (!m.end_date || m.end_date >= week));
}
function otherTotal() { return (draft.non_project_entries || []).reduce((s,e)=>s+Number(e.effort || 0),0); }
function reportText(r) {
  return `<div class="weekly-text"><strong>本周工作成果</strong><p>${esc(r.note)}</p><strong>下周计划</strong><p>${esc(r.next_plan || '历史记录未填写')}</p><strong>需要协调的工作</strong><p>${esc(r.coordination || '无')}</p>${coordinationControls(r)}${attachmentLinks(reportAttachments(r,r.user_id,r.week))}</div>`;
}
function reportTable(rows) {
  return rows.length ? `<div class="table-wrap"><table class="data-table"><thead><tr><th>成员 / 部门</th><th>项目</th><th>工作量</th><th>累计进度</th><th>周报内容</th></tr></thead><tbody>${rows.map(r=>`<tr><td>${esc(person(r.user_id)?.name)}<div class="small muted">${esc(person(r.user_id)?.department || '未分配部门')}</div></td><td><button class="text-button" type="button" data-action="detail" data-id="${r.project_id}">${esc(projectPath(project(r.project_id)))}</button></td><td>${number(r.effort)}%</td><td>${number(r.progress)}%</td><td class="weekly-content">${reportText(r)}</td></tr>`).join('')}</tbody></table></div>` : empty('暂无周报记录','切换周次，或等待成员提交周报。','',true);
}

function subtreeIds(root) {
  const ids = new Set([root]);
  let changed = true;
  while (changed) { changed = false; state.projects.forEach(p=>{if(ids.has(p.parent_id) && !ids.has(p.id)){ids.add(p.id);changed=true;}}); }
  return ids;
}

function personnelSearch(kind, label) {
  return `<div class="personnel-search">${icon('search')}<input id="p-${kind}-search" type="search" data-people-search="p-${kind}-list" placeholder="搜索${label}" aria-label="搜索${label}" aria-controls="p-${kind}-list" title="按姓名、部门或登录名搜索" autocomplete="off"></div>`;
}

function filterPersonnel(input) {
  const list=document.getElementById(input.dataset.peopleSearch);
  if (!list) return;
  const terms=input.value.normalize('NFKC').toLocaleLowerCase().trim().split(/\s+/).filter(Boolean);
  let matches=0;
  list.querySelectorAll('[data-person-key]').forEach(row=>{
    const text=row.dataset.personKey.normalize('NFKC').toLocaleLowerCase();
    row.hidden=!terms.every(term=>text.includes(term));
    if (!row.hidden) matches++;
  });
  const empty=list.querySelector('.personnel-search-empty');
  if (empty) empty.hidden=matches>0;
}

function projectModal(id = null, parentId = null) {
  const p = id ? project(id) : null, parent = project(parentId || p?.parent_id);
  const users = state.users.filter(u=>u.active || p?.member_ids.includes(u.id) || p?.viewer_ids.includes(u.id));
  const owner = p?.owner_id || state.user.id;
  const blocked = p ? subtreeIds(p.id) : new Set();
  const parents = state.projects.filter(q=>!blocked.has(q.id) && !q.effective_archived && q.can_manage);
  const currentParent = p?.parent_id || parentId || '';
  if (currentParent && !parents.some(q=>q.id===currentParent) && project(currentParent)) parents.push(project(currentParent));
  openModal(p ? '配置项目与人员' : parent ? '添加子项目' : '新建项目','每个节点的参与人和时间独立配置；查看人可查看本节点与下级。',`<form id="project-form" data-id="${p?.id || ''}"><div class="modal-body"><div id="form-error"></div><div class="form-grid">${field('项目名称','p-name',`<input id="p-name" name="name" maxlength="80" value="${esc(p?.name || '')}" required>`,true)}${field('所属项目','p-parent',`<select id="p-parent" name="parent_id">${state.capabilities.create_project || !currentParent ? '<option value="">顶级项目</option>' : ''}${treeRows(parents).map(q=>`<option value="${q.id}" ${q.id===currentParent?'selected':''}>${esc(projectPath(q))}</option>`).join('')}</select>`)}${projectDepartmentField(p,parent)}${field('项目说明','p-description',`<textarea id="p-description" name="description" maxlength="1000">${esc(p?.description || '')}</textarea>`,true)}${field('项目负责人','p-owner',`<select id="p-owner" name="owner_id">${users.filter(u=>u.active).map(u=>`<option value="${u.id}" ${u.id===owner?'selected':''}>${esc(u.name)}</option>`).join('')}</select>`)}${field('优先级','p-priority',`<select id="p-priority" name="priority">${[['normal','普通'],['high','高优先级'],['low','低优先级']].map(([v,t])=>`<option value="${v}" ${p?.priority===v?'selected':''}>${t}</option>`).join('')}</select>`)}${field('开始日期','p-start',`<input id="p-start" name="start_date" type="date" value="${p?.start_date || parent?.start_date || state.today}" required>`)}${field('计划完成日期','p-due',`<input id="p-due" name="due_date" type="date" value="${p?.due_date || parent?.due_date || dateAdd(state.today,30)}" required>`)}</div><div class="detail-subhead personnel-heading"><span>参与人及参与时间</span>${personnelSearch('members','参与人')}</div><p class="hint">负责人可管理项目，需要填报时再勾选为参与人。日期留空不限制参与时间；子项目独立配置参与人。</p><div class="assignment-list" id="p-members-list">${users.map(u=>{const m=p?.assignments.find(m=>m.user_id===u.id);return `<div class="assignment-row" data-user="${u.id}" data-person-key="${esc(`${u.name} ${u.department} ${u.username}`)}"><label class="checkbox-option"><input type="checkbox" name="member_ids" value="${u.id}" ${m || (!p && u.id===owner)?'checked':''}>${esc(u.name)}<small>${esc(u.department)}</small></label><input type="date" data-period="start_date" aria-label="${esc(u.name)}参与开始日期" value="${m?.start_date || ''}"><span>至</span><input type="date" data-period="end_date" aria-label="${esc(u.name)}参与结束日期" value="${m?.end_date || ''}"></div>`;}).join('')}<p class="personnel-search-empty" role="status" hidden>没有找到匹配的人员</p></div><div class="detail-subhead personnel-heading"><span>查看人</span>${personnelSearch('viewers','查看人')}</div><div class="checkbox-grid" id="p-viewers-list">${users.map(u=>`<label class="checkbox-option project-viewer-option" data-person-key="${esc(`${u.name} ${u.department} ${u.username}`)}"><input type="checkbox" name="viewer_ids" value="${u.id}" ${(p ? p.viewer_ids.includes(u.id) : u.id===state.user.id)?'checked':''}>${esc(u.name)}<small>${esc(u.department)}</small></label>`).join('')}<p class="personnel-search-empty" role="status" hidden>没有找到匹配的人员</p></div><p class="hint">查看身份不授予填报权限。填写周报、查看周报和统计维度还受账号权限控制。</p>${p ? `<label class="checkbox-option archive-option"><input name="archived" type="checkbox" ${p.archived?'checked':''}>归档本节点及其下级的填报入口</label>` : ''}</div><footer class="modal-foot">${button('取消','close-modal')}<button type="submit" class="button primary">${p ? '保存配置' : '创建项目'}</button></footer></form>`,'',true);
  updateAssignmentControls();
}

function projectDepartmentField(p,parent) {
  const department=parent?.department ?? p?.department ?? (state.capabilities.department_management?state.managed_departments[0]:state.user.department) ?? '';
  const editable=!parent && (state.user.role==='admin'||state.capabilities.department_management);
  if(!editable) return `<div class="field" id="project-department-field"><span class="field-label">归属部门</span><span class="readonly-value">${esc(department || '未分配部门')}</span></div>`;
  const choices=state.user.role==='admin'?[...new Set([...state.users.map(u=>u.department),department].filter(Boolean))]:state.managed_departments;
  return field('归属部门','p-department',`<select id="p-department" name="department">${state.user.role==='admin'?'<option value="">未分配部门</option>':''}${choices.map(d=>`<option value="${esc(d)}" ${d===department?'selected':''}>${esc(d)}</option>`).join('')}</select>`,false,'子项目跟随上级项目的归属部门。').replace('<div class="field ', '<div id="project-department-field" class="field ');
}

function leaveEditor() {
  return `<section class="leave-editor"><div class="section-head"><div><h2>非项目工作量</h2><p class="hint">从下拉框选择休假、学习/研究/培训或其他；每类最多一条，可组合填写。</p></div>${draft.non_project_entries.length < 3 ? button('添加非项目工作','add-other','','plus') : ''}</div>${draft.non_project_entries.map((e,i)=>`<div class="non-project-row" data-other="${i}"><div class="field"><label for="other-category-${i}">分类</label><select id="other-category-${i}" data-other-field="category">${Object.entries(categories).map(([k,label])=>`<option value="${k}" ${k===e.category?'selected':''} ${draft.non_project_entries.some((r,j)=>j!==i&&r.category===k)?'disabled':''}>${label}</option>`).join('')}</select></div><div class="field"><label for="other-effort-${i}">本周比例 %</label><input id="other-effort-${i}" data-other-field="effort" type="number" min="0.5" max="100" step="0.5" value="${esc(e.effort)}" required></div><div class="field"><label for="other-note-${i}">工作说明</label><textarea id="other-note-${i}" data-other-field="note" maxlength="500" required placeholder="填写工作内容或休假日期">${esc(e.note)}</textarea></div>${iconButton('close','remove-other','移除非项目工作')}</div>`).join('')}</section>`;
}
function rowHTML(entry,index) {
  const p = project(entry.project_id);
  return `<section class="report-card" data-row="${index}"><div class="report-card-head"><span class="project-symbol">${icon('folder')}</span><h3>${esc(projectPath(p))}</h3>${iconButton('close','remove-row','移除此项目')}</div><div class="report-fields"><div class="field"><label for="progress-${index}">我的累计开发进度</label><div class="percent-input"><input id="progress-${index}" data-field="progress" type="number" min="0" max="100" step="0.5" value="${esc(entry.progress)}" required><span>%</span></div><div class="range-line"><input type="range" min="0" max="100" step="0.5" data-field="progress-range" value="${esc(entry.progress)}" aria-label="${esc(p?.name)}开发进度滑块"></div></div><div class="field"><label for="effort-${index}">本周工作量比例</label><div class="percent-input"><input id="effort-${index}" data-field="effort" type="number" min="0" max="100" step="0.5" value="${esc(entry.effort)}" required><span>%</span></div></div><div class="field full"><label for="note-${index}">本周工作成果</label><textarea id="note-${index}" data-field="note" maxlength="1000" required placeholder="完成的工作、交付的成果">${esc(entry.note)}</textarea></div><div class="field full"><label for="plan-${index}">下周计划</label><textarea id="plan-${index}" data-field="next_plan" maxlength="1000" required placeholder="下周计划推进的工作、预期交付">${esc(entry.next_plan)}</textarea></div><div class="field full"><label for="coordination-${index}">需要协调的工作</label><textarea id="coordination-${index}" data-field="coordination" maxlength="1000" placeholder="需要谁协助、事项和期望时间；无则留空">${esc(entry.coordination)}</textarea></div></div>${attachmentEditor(entry,index)}</section>`;
}
function reportPage() {
  if (!state.permissions.report_submit) return empty('未开通填报权限','请联系公司管理员配置账号权限。');
  prepareDraft();
  const existing = weekSubmission(state.user.id,reportWeek);
  return `<div class="report-layout"><div class="page-head"><div><div class="eyebrow">MY WEEKLY UPDATE</div><h1>我的填报</h1><p>记录成果、计划和需要协调的工作。</p></div><div class="head-actions">${weekSwitch('report')}</div></div><div class="report-intro">${icon('info')}<span>所有项目与非项目工作量合计必须为 100%。开发进度记录累计完成比例，工作量记录本周投入，两者独立。</span></div><div class="toolbar"><div><h2>${weekLabel(reportWeek,true)} <span class="badge ${existing?'':'gray'}">${existing?'已提交，可修改':'待填报'}</span></h2><p id="draft-status" class="week-caption">${dirty ? '有尚未提交的草稿，已保存在当前浏览器' : existing ? `最近提交于 ${displayTime(existing.updated_at)}` : '选择本周实际参与的项目'}</p></div><label class="week-field">选择日期<input id="report-date" type="date" value="${reportWeek}" max="${state.today}"></label></div><div class="report-toolbar"><span>本周项目 · ${draft.entries.length} 个</span><div class="head-actions">${button('复用上周','copy-week','','copy')}${button('平分投入','split-effort','','split')}</div></div><form id="report-form"><div id="form-error"></div><div id="report-rows">${draft.entries.length ? draft.entries.map(rowHTML).join('') : empty('添加本周参与的项目','也可以在下方登记非项目工作。','',true)}</div>${availableProjects().some(p=>!draft.entries.some(e=>e.project_id===p.id)) ? button('添加参与的项目','add-row','','plus') : ''}${leaveEditor()}<div class="report-footer"><div id="effort-total" class="effort-total" aria-live="polite"></div><button id="submit-report" type="submit" class="button primary">${icon('check')}${existing?'更新本周填报':'提交本周填报'}</button></div></form></div>`;
}
function updateTotal() {
  if (!draft || !$('#effort-total')) return;
  const effort = draft.entries.reduce((s,e)=>s+Number(e.effort || 0),0), other = otherTotal(), total = effort+other;
  const valid = total === 100 && draft.entries.length + draft.non_project_entries.length > 0 &&
    !draft.entries.some(e=>!Number.isFinite(Number(e.effort)) || Number(e.effort)<0 || Number(e.effort)>100 || Number(e.effort)*2%1!==0) &&
    !draft.non_project_entries.some(e=>Number(e.effort)<=0 || !Number.isFinite(Number(e.effort))) &&
    !(draft.non_project_entries.some(e=>e.category==='leave' && Number(e.effort)===100) && draft.entries.length);
  $('#effort-total').className = `effort-total ${valid?'valid':''}`;
  $('#effort-total').innerHTML = `项目 ${number(effort)}%＋非项目 ${number(other)}%<strong>${number(total)}%</strong><small>${uploading ? '正在上传文件…' : valid ? '分配完成，可以提交' : `合计必须为 100%${total<100?`，还差 ${number(100-total)}%`:total>100?`，超出 ${number(total-100)}%`:''}`}</small>`;
  $('#submit-report').disabled = !valid || uploading;
  if (dirty && $('#draft-status')) $('#draft-status').textContent='有尚未提交的草稿，已保存在当前浏览器';
}

function detailModal(id) {
  const p = project(id);
  if (!p) return;
  const ids = subtreeIds(id), history = state.reports.filter(r=>ids.has(r.project_id) && r.week <= viewWeek);
  const children = treeRows(state.projects.filter(q=>q.id!==id && ids.has(q.id)));
  openModal(esc(p.name),esc(projectPath(p)),`<div class="modal-body"><div class="modal-detail-meta">${projectBadge(p)}<span>负责人：${esc(person(p.owner_id)?.name)}</span><span>归属部门：${esc(p.department || '未分配部门')}</span><span>${p.start_date} → ${p.due_date}</span></div>${p.limited_view?'<p class="hint">跨部门项目：此处仅展示本部门成员的参与信息与授权周报。</p>':''}${p.context_only ? '<p class="hint">当前节点仅作为上级目录展示，周报内容按各节点授权提供。</p>' : `<p class="detail-description">${esc(p.description)}</p><div class="detail-subhead">参与人及参与时间</div>${p.assignments.map(m=>`<div class="assignment-summary"><strong>${esc(person(m.user_id)?.name)}</strong><span>${m.start_date || '不限开始'} → ${m.end_date || '至归档'}</span></div>`).join('')}<div class="detail-subhead">查看人</div><p class="hint">${esc(p.viewer_ids.map(id=>person(id)?.name).join('、') || '未单独配置')}</p>`}${children.length ? `<div class="detail-subhead">子项目</div>${children.map(q=>`<p class="child-link"><button class="text-button" type="button" data-action="detail" data-id="${q.id}">${esc(projectPath(q))}</button></p>`).join('')}` : ''}<div class="detail-subhead">${p.can_read_all_reports?'项目成员的最新进展':p.limited_view?'本部门成员的最新进展':'我的最新进展'}</div>${p.member_ids.filter(uid=>p.can_read_all_reports || p.limited_view || uid===state.user.id).map(uid=>{const r=latestReport(p.id,uid);return `<div class="detail-person">${avatar(person(uid))}<div class="person-info"><strong>${esc(person(uid)?.name)}</strong><small>${r?weekLabel(r.week,true):'暂无可见周报'}</small>${r?reportText(r):''}</div><div class="detail-value">${number(r?.progress)}%<small>累计进度</small></div></div>`;}).join('')}<div class="detail-subhead">历史填报记录 · 含下级节点</div>${history.length ? history.map(r=>`<div class="history-item"><div class="history-head"><span>${weekLabel(r.week,true)} · ${esc(person(r.user_id)?.name)} · ${esc(project(r.project_id)?.name)}</span><span>工作量 ${number(r.effort)}%</span></div>${reportText(r)}</div>`).join('') : '<p class="hint">暂无授权范围内的周报。</p>'}</div>`,`${p.can_manage ? `<button type="button" class="button" data-action="edit-project" data-id="${p.id}">配置项目</button>${!p.effective_archived ? `<button type="button" class="button" data-action="new-child" data-id="${p.id}">添加子项目</button>`:''}`:''}${state.permissions.report_submit && myAssignment(p,state.week) ? `<button class="button primary" data-action="report-project" data-id="${p.id}">填写我的周报</button>`:''}`,true);
}

function roleSelect(u, id='u-role') {
  return `<select id="${id}" name="role">${Object.entries(roleNames).map(([v,label])=>`<option value="${v}" ${v===(u?.role || 'member')?'selected':''}>${label}</option>`).join('')}</select>`;
}
function memberRoleLabel(u) {
  const owns=state.projects.some(p=>p.owner_id===u.id);
  return owns&&u.role==='member'?'项目负责人':`${roleNames[u.role] || '普通参与人'}${owns&&u.role==='department_manager'?' · 项目负责人':''}`;
}
function permissionEditor(u) {
  const defaults = {report_submit:u?.role!=='admin',report_view:true,stats_personal:u?.role!=='admin',stats_department:u?.role==='department_manager'||u?.role==='admin',stats_company:u?.role==='admin'};
  const grants = u?.permissions || defaults;
  return `<div class="field full"><span class="field-label">业务权限</span><div class="checkbox-grid permission-grid">${Object.entries(permissionNames).map(([k,label])=>`<label class="checkbox-option"><input type="checkbox" name="permission" value="${k}" ${grants[k]?'checked':''} ${u?.role==='admin'&&['report_submit','stats_personal'].includes(k)?'disabled':''}>${label}</label>`).join('')}</div><p class="hint">项目负责人由各项目单独指定，可与部门负责人兼任。部门负责人可管理归属部门的项目；统计授权不授予项目编辑权限。管理员无需填报周报和个人投入；查看他人正文还需查看周报权限。</p></div>${field('授权管理的部门','u-managed',`<input id="u-managed" name="managed_departments" value="${esc((u?.managed_departments || []).join('、'))}" placeholder="多个部门以顿号、逗号分隔">`,true,'部门负责人按此范围管理部门项目；部门统计按此范围汇总人员。留空使用本人部门。')}`;
}
function memberModal(id = null) {
  const u = id ? person(id) : null;
  openModal(u?'编辑成员与权限':'添加成员','账号、部门与业务权限在平台中维护。',`<form id="member-form" data-id="${u?.id || ''}"><div class="modal-body"><div id="form-error"></div><div class="form-grid">${field('姓名','u-name',`<input id="u-name" name="name" maxlength="30" value="${esc(u?.name || '')}" required>`)}${field('部门','u-dept',`<input id="u-dept" name="department" maxlength="50" value="${esc(u?.department || '')}">`)}${field('登录名','u-username',`<input id="u-username" name="username" pattern="[a-zA-Z0-9_.-]{3,32}" maxlength="32" value="${esc(u?.username || '')}" required>`,true)}${field(u?'重置密码（选填）':'初始密码','u-password',`<input id="u-password" name="password" type="password" minlength="8" maxlength="128" ${u?'':'required'} autocomplete="new-password">`,true)}${field('角色','u-role',roleSelect(u))}${field('账号状态','u-active',`<select id="u-active" name="active"><option value="1" ${!u||u.active?'selected':''}>启用</option><option value="0" ${u&&!u.active?'selected':''}>停用</option></select>`)}${permissionEditor(u)}</div></div><footer class="modal-foot">${button('取消','close-modal')}<button type="submit" class="button primary">保存成员</button></footer></form>`);
}
function roleModal(id) {
  const u = person(id);
  openModal('配置业务权限',esc(u.name),`<form id="role-form" data-id="${id}"><div class="modal-body"><div id="form-error"></div><div class="form-grid">${field('角色','u-role',roleSelect(u),true)}${permissionEditor(u)}</div></div><footer class="modal-foot">${button('取消','close-modal')}<button type="submit" class="button primary">保存权限</button></footer></form>`);
}
function grantsPayload(form) {
  const data = new FormData(form);
  return {permissions:Object.fromEntries(Object.keys(permissionNames).map(k=>[k,data.getAll('permission').includes(k)])),managed_departments:String(data.get('managed_departments') || '').split(/[、,，;；]/).map(s=>s.trim()).filter(Boolean)};
}

document.addEventListener('click',async event=>{
  const target = event.target.closest('[data-action]');
  if (!target || target.disabled || uploading) return;
  try {
    const action = target.dataset.action;
    if (action==='board') {board=target.dataset.value;analysisMember='';analysisProject='';await refresh();}
    if (action==='coordination-toggle') {
      const id=Number(target.dataset.id),r=state.reports.find(r=>r.id===id);
      if(!r) return;
      target.disabled=true;
      const completed=!r.coordination_completed;
      await api(`/api/reports/${id}/coordination`,{completed,expected_coordination:r.coordination},'PUT');
      const reopen=Boolean(dialog?.open);
      closeModal();
      await refresh();
      if(reopen) weeklyModal(id);
      toast(completed?'已完成，总览待协调数量已更新':'已重新打开协调事项');
    }
    if (action==='project-relation') {projectRelation=target.dataset.value;render();}
    if (action==='new-child') projectModal(null,Number(target.dataset.id));
    if (action==='toggle-project') {
      const id=Number(target.dataset.id);
      if(expandedProjects.has(id)) expandedProjects.delete(id); else expandedProjects.add(id);
      $('#project-list').innerHTML=projectTree(filteredProjects());
      const control=$(`#project-list .project-title-button[data-id="${id}"]`);
      control?.focus({preventScroll:true});
    }
    if (action==='weekly-detail') weeklyModal(Number(target.dataset.id));
    if (action==='go-selected-report') {
      reportWeek=viewWeek;draft=null;dirty=false;route='report';location.hash='report';render();updateTotal();
    }
    if (action==='add-other') {
      const category = Object.keys(categories).find(k=>!draft.non_project_entries.some(e=>e.category===k));
      if (category) draft.non_project_entries.push({category,effort:Math.max(0,100-otherTotal()-draft.entries.reduce((s,e)=>s+Number(e.effort || 0),0)),note:''});
      saveDraft();render();updateTotal();
    }
    if (action==='remove-other') {draft.non_project_entries.splice(Number(target.closest('[data-other]').dataset.other),1);saveDraft();render();updateTotal();}
    if (action==='read-reminders') {await api('/api/reminders/read',{},'PUT');await refresh();}
    if (action==='remind-all' || action==='remind-one') {
      const stats=analyticsCache.get(`${statisticsScope}:${viewWeek}`);
      if (!stats?.pending_ids) return;
      target.disabled=true;
      const result=await api('/api/reminders',{scope:statisticsScope,week:viewWeek,user_ids:action==='remind-one'?[Number(target.dataset.id)]:stats.pending_ids},'POST');
      toast(`已发送 ${result.count} 条站内提醒`);target.disabled=false;
    }
  } catch(error) {toast(error.message,true);target.disabled=false;}
});
document.addEventListener('input',event=>{
  const target=event.target;
  if (target.dataset.peopleSearch) filterPersonnel(target);
  if (target.dataset.otherField) {
    const item=draft.non_project_entries[Number(target.closest('[data-other]').dataset.other)];
    item[target.dataset.otherField]=target.dataset.otherField==='effort' ? (target.value===''?'':Number(target.value)) : target.value;
    saveDraft();
  }
});
document.addEventListener('change',event=>{
  const target=event.target;
  if (target.id==='statistics-scope') {statisticsScope=target.value;analysisMember='';analysisProject='';render();}
  if (target.id==='p-parent') {
    const form=target.closest('form');
    $('#project-department-field').outerHTML=projectDepartmentField(project(Number(form.dataset.id)),project(Number(target.value)));
  }
  if (target.id==='analysis-member') {analysisMember=target.value;overviewSections.add('reports');render();}
  if (target.id==='analysis-project') {analysisProject=target.value;overviewSections.add('reports');render();}
  if (target.dataset.otherField==='category') {render();updateTotal();}
  if (target.id==='u-role') {
    const form=target.closest('form'),role=target.value;
    form.querySelectorAll('[name="permission"]').forEach(box=>{
      box.disabled=role==='admin'&&['report_submit','stats_personal'].includes(box.value);
      box.checked=!box.disabled && (['report_submit','report_view','stats_personal'].includes(box.value) || (box.value==='stats_department' && role!=='member') || (box.value==='stats_company' && role==='admin'));
    });
  }
  if (target.matches('[name="member_ids"]') || target.id==='p-owner') updateAssignmentControls();
});
function updateAssignmentControls() {
  document.querySelectorAll('.assignment-row').forEach(row=>{
    const checked=$('[name="member_ids"]',row).checked;
    row.querySelectorAll('[data-period]').forEach(input=>input.disabled=!checked);
  });
}

start().then(updateTotal);
