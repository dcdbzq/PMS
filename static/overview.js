'use strict';

// Personal work is rendered from the user's own records. Management summaries
// are requested only after entering an explicitly authorized management view.
const overviewSections = new Set();

function managementScopes() {
  return [...(state.permissions.stats_company?['company']:[]), ...(state.permissions.stats_department?['department']:[]), ...(state.capabilities.project_management?['project']:[])];
}
function roleCaption() {
  const roles=[];
  if(state.user.role==='admin') roles.push('公司管理员');
  if(state.capabilities.department_management) roles.push('部门负责人');
  if(state.capabilities.project_management && state.user.role!=='admin') roles.push('项目负责人');
  return roles.join(' · ') || '普通参与人';
}

function overviewPage() {
  const scopes = managementScopes(), canManage = scopes.length>0;
  if (!canManage) board='mine';
  else if (!['mine','team','followup'].includes(board)) board='team';
  if(state.user.role==='admin' && board==='mine') board='team';
  if(state.user.role==='admin' && !canManage) return overviewHeader()+empty('暂无统计权限','可在团队成员中配置部门或公司统计权限。');
  if (board === 'mine') return personalOverview();
  if (!scopes.includes(statisticsScope)) statisticsScope = scopes[0];
  const key = `${statisticsScope}:${viewWeek}`, stats = analyticsCache.get(key);
  if (!stats) {
    analyticsCache.set(key,{loading:true});
    const snapshot = state;
    api(`/api/analytics?scope=${statisticsScope}&week=${viewWeek}`).then(result=>{
      if (state !== snapshot) return;
      analyticsCache.set(key,result);
      if (route === 'overview') render();
    }).catch(error=>{
      if (state !== snapshot) return;
      analyticsCache.set(key,{error:error.message});
      if (route === 'overview') render();
    });
  }
  const header = overviewHeader();
  if (!stats || stats.loading) return header + '<div class="panel overview-loading" role="status">正在整理本周进展…</div>';
  if (stats.error) return header + `<div class="inline-error">${esc(stats.error)}，请刷新重试。</div>`;
  const labels={company:'全公司',department:'我的部门',project:'我负责的项目'};
  const selector = scopes.length > 1 ? `<label class="overview-scope">查看范围<select id="statistics-scope">${scopes.map(scope=>`<option value="${scope}" ${statisticsScope===scope?'selected':''}>${labels[scope]}</option>`).join('')}</select></label>` : '';
  const scopeLabel = statisticsScope === 'company' ? '公司整体进展' : statisticsScope==='project'?'负责项目的进展':`${state.managed_departments.join(' / ') || '部门'} · 本周进展`;
  const subhead = `<div class="section-head overview-section-head"><h2>${board==='followup'?'需要关注的事':esc(scopeLabel)}</h2><div class="head-actions">${selector}${board==='team'&&state.capabilities.create_project?button('新建项目','new-project','','plus'):''}</div></div><p class="scope-caption">${statisticsScope==='project'?'统计仅限负责项目及其子项目；成员的其他项目和非项目工作保持独立。':statisticsScope==='department'?'投入按本部门人员汇总；跨部门项目仅显示本部门成员的记录。':'汇总全公司项目、部门投入与周报更新情况。'}</p>`;
  return header + subhead + (board==='followup' ? followupPanel(stats) : teamOverview(stats));
}

function overviewHeader() {
  const management = managementScopes().length>0;
  const tabs = management ? `<div class="overview-nav board-cards" aria-label="总览视角">${[
    ...(state.user.role==='admin'?[]:[['mine','folder','我的工作']]),['team','users',state.permissions.stats_company?'公司总览':state.permissions.stats_department?'部门项目':'负责项目'],['followup','bell','待办与异常']
  ].map(([key,symbol,label])=>`<button type="button" class="board-card ${board===key?'selected':''}" data-action="board" data-value="${key}" aria-pressed="${board===key}">${icon(symbol)}<span>${label}</span></button>`).join('')}</div>` : '';
  const reminders = (state.reminders || []).filter(r=>!weekSubmission(state.user.id,r.week));
  const notice = reminders.length ? `<div class="reminder-banner" role="status"><div>${icon('bell')}<strong>有一份周报等待你更新</strong><p>${reminders.map(r=>`${esc(person(r.sender_id)?.name || '负责人')} 提醒你填写 ${weekLabel(r.week)}`).join('；')}</p></div>${button('知道了','read-reminders')}</div>` : '';
  return `${notice}<div class="page-head overview-head"><div><div class="eyebrow">${esc(roleCaption())} · 工作总览</div><h1>${esc(state.user.name)}，你好 <span class="greeting-icon">${icon('sun')}</span></h1><p>${board==='mine'?'专注手上的项目，记录每一份进展。':board==='team'?'看见负责范围的进展，让协作有条不紊。':'把需要跟进的事，放在一起处理。'}</p></div><div class="head-actions">${weekSwitch('view')}${state.permissions.report_submit?button('填写周报','go-report','primary','edit'):''}</div></div>${tabs}`;
}

function overviewMetric(label,value,unit,symbol,caption,tone='') {
  return `<div class="metric overview-metric"><div class="metric-top"><span>${label}</span><span class="metric-icon ${tone}">${icon(symbol)}</span></div><div class="metric-value">${esc(value)}<small>${unit}</small></div><div class="metric-bottom">${caption}</div></div>`;
}

function personalOverview() {
  const assigned = state.projects.filter(p=>myAssignment(p,viewWeek));
  const own = state.reports.filter(r=>r.user_id===state.user.id && r.week===viewWeek);
  const extras = state.non_project_entries.filter(r=>r.user_id===state.user.id && r.week===viewWeek);
  const projectEffort = own.reduce((sum,r)=>sum+r.effort,0), otherEffort = extras.reduce((sum,r)=>sum+r.effort,0);
  const coordination = own.filter(r=>r.coordination?.trim() && !r.coordination_completed);
  const metrics = state.permissions.stats_personal ? `<div class="metrics personal-metrics">${
    overviewMetric('参与项目',assigned.length,'个','folder','本周实际参与的项目') +
    overviewMetric('本周投入',number(projectEffort+otherEffort),'%','chart',own.length || extras.length ? `其中项目投入 ${number(projectEffort)}%` : '提交周报后自动汇总','tone-1') +
    overviewMetric('需要协调',coordination.length,'项','message',coordination.length?'记录在本周周报中':'本周暂无协调事项','tone-2')
  }</div>` : '';
  const submission = weekSubmission(state.user.id,viewWeek);
  const weekly = `<section class="my-week-card"><div class="my-week-heading"><span class="week-card-icon">${icon('edit')}</span><span>我的周报</span><span class="badge ${submission?'':'gray'}">${submission?'已更新':'待更新'}</span></div><h2>${submission?'这一周的努力，<br>已经被记录。':'这一周的进展，<br>值得被记录。'}</h2><p>${submission?`最近更新于 ${displayTime(submission.updated_at)}`:'写下成果与计划，让协作更轻松。'}</p>${state.permissions.report_submit?button(submission?'查看我的周报':'记录本周进展','go-selected-report','','arrow'):''}</section>`;
  const distribution = state.permissions.stats_personal ? `<section class="panel panel-pad personal-distribution"><div class="panel-head"><h3>${icon('chart')}本周投入分布</h3></div>${allocationChart(projectEffort,extras)}${submission?'':'<p class="hint">提交周报后，在这里看见本周的时间去向。</p>'}</section>` : '';
  const projects = assigned.length ? assigned.slice(0,4).map(p=>projectCard(p,true)).join('') : empty('本周暂无参与中的项目','在项目空间查看项目，或在周报中记录非项目工作。','',true);
  return overviewHeader()+metrics+coordinationSection(own.filter(r=>r.coordination?.trim()),'personal-coordination')+`<div class="home-grid personal-home"><section><div class="section-head"><h2>${icon('folder')}我的项目 <span class="count">${assigned.length}</span></h2><a class="text-button" href="#projects">查看全部${icon('right')}</a></div><div class="project-grid">${projects}</div></section><aside class="home-aside">${weekly}${distribution}</aside></div><section class="activity"><div class="section-head"><h2>${icon('clock')}本周的进展</h2><span class="small muted">${weekLabel(viewWeek)}</span></div><div class="panel">${own.length ? weeklyPreviews(own) : empty('每一步进展，都值得留下','填写周报后，你的成果和计划会出现在这里。','',true)}</div>${extras.length?`<div class="personal-extra-summary">${extras.map(r=>`<span>${icon(r.category==='leave'?'leaf':r.category==='learning'?'book':'calendar')}${categories[r.category]} <strong>${number(r.effort)}%</strong></span>`).join('')}</div>`:''}</section>`;
}

function allocationChart(projectEffort, extras) {
  const parts = [{label:'项目工作',value:projectEffort,color:'green'},...Object.entries(categories).map(([k,label])=>({label,value:extras.filter(r=>r.category===k).reduce((s,r)=>s+r.effort,0),color:k}))].filter(p=>p.value>0);
  const total = parts.reduce((s,p)=>s+p.value,0);
  return `<div class="allocation-bar" aria-label="工作投入分布">${parts.map(p=>`<span class="allocation-segment ${p.color}" style="width:${total?p.value/total*100:0}%" title="${p.label} ${number(p.value)}%"></span>`).join('')}</div><div class="allocation-legend">${parts.map(p=>`<div><span><i class="allocation-dot ${p.color}"></i>${p.label}</span><strong>${number(p.value)}%</strong></div>`).join('')}</div>`;
}

function weeklyPreviews(rows, showPerson=false) {
  return `<div class="weekly-previews">${rows.map(r=>`<button type="button" class="weekly-preview" data-action="weekly-detail" data-id="${r.id}"><span class="preview-symbol">${showPerson?avatar(person(r.user_id)):icon('check-circle')}</span><span class="preview-main"><strong>${showPerson?`${esc(person(r.user_id)?.name)} <span class="preview-project">· ${esc(project(r.project_id)?.name)}</span>`:esc(project(r.project_id)?.name)}</strong><span class="preview-note">${esc(r.note)}</span></span><span class="preview-effort">${number(r.effort)}%<small>本周投入</small></span>${icon('right')}</button>`).join('')}</div>`;
}

function teamOverview(stats) {
  const ids=new Set(stats.projects.map(p=>p.id)), roots = stats.projects.filter(p=>!ids.has(p.parent_id)), pending = stats.pending_ids, projectScope=stats.scope==='project';
  const metrics = `<div class="metrics ${projectScope?'personal-metrics':''}">${overviewMetric(projectScope?'负责项目':'推进中的项目',roots.filter(p=>!p.archived).length,'个','folder','包含下级项目的整体进展')+overviewMetric('项目投入',number(stats.project_effort/100),'人周','chart','1 人周 = 1 人一周的全部工作量','tone-1')+overviewMetric('周报已更新',`${stats.submitted} / ${stats.expected}`,'人','check-circle',pending.length?`${pending.length} 位同事仍待更新`:'本周周报已全部收齐')+(!projectScope?overviewMetric('等待跟进',pending.length,'人','clock',pending.length?'可以在待办中发送提醒':'本周暂无待更新周报','tone-2'):'')}</div>`;
  const scopedReports = state.reports.filter(r=>r.week===viewWeek && stats.members.some(m=>m.id===r.user_id) && (!projectScope || project(r.project_id)?.is_owned));
  const projectSummary = `<section class="panel panel-pad"><div class="section-head"><h2>${icon('folder')}项目投入</h2><span class="small muted">含子项目</span></div>${roots.length?`<div class="workload-list">${roots.slice().sort((a,b)=>b.rolled_effort-a.rolled_effort).slice(0,6).map(p=>workloadRow(p.name,p.rolled_effort,stats.project_effort,p.id)).join('')}</div>`:empty('本周暂无项目投入','团队更新周报后自动汇总。','',true)}${overviewDisclosure('projects','完整项目统计',projectStatistics(stats))}</section>`;
  const right = stats.scope==='company' ? `<section class="panel panel-pad"><div class="section-head"><h2>${icon('users')}部门投入</h2></div><div class="workload-list">${stats.departments.filter(d=>d.expected||d.project_effort).sort((a,b)=>b.project_effort-a.project_effort).map(d=>workloadRow(d.name,d.project_effort,stats.project_effort)).join('')}</div></section>` : projectScope ? `<section class="panel panel-pad"><div class="section-head"><h2>${icon('folder')}项目管理</h2></div><div class="managed-project-list">${treeRows(state.projects.filter(p=>p.is_owned && !p.effective_archived)).map(p=>`<div class="managed-project-row"><button type="button" class="text-button" data-action="detail" data-id="${p.id}" style="--depth:${Math.min(p.depth,5)}">${p.depth?'↳ ':''}${esc(p.name)}</button>${p.can_manage?`<button type="button" class="icon-button" data-action="edit-project" data-id="${p.id}" aria-label="配置${esc(p.name)}" title="配置项目与人员">${icon('edit')}</button>`:''}</div>`).join('') || '<p class="hint">暂无进行中的项目</p>'}</div></section>` : `<section class="panel panel-pad"><div class="section-head"><h2>${icon('chart')}团队投入分布</h2></div>${allocationChart(stats.project_effort,Object.entries(stats.categories).map(([category,effort])=>({category,effort})))}</section>`;
  const pendingPanel = `<section class="panel panel-pad pending-overview"><div class="section-head"><h2>${icon('clock')}待更新周报</h2><button type="button" class="text-button" data-action="board" data-value="followup">去跟进${icon('right')}</button></div>${pending.slice(0,4).map(id=>`<div class="pending-overview-person">${avatar(person(id))}<span>${esc(person(id)?.name)}<small>${esc(person(id)?.department || '未分配部门')}</small></span><i class="status-dot"></i></div>`).join('') || '<p class="hint">已全部更新，辛苦了。</p>'}${pending.length>4?`<p class="hint">还有 ${pending.length-4} 位同事等待更新</p>`:''}</section>`;
  const members = `<p class="hint">${projectScope?'这里只显示负责项目中的投入。周报已更新不代表一定在本项目有投入。':'工作量包括项目与非项目工作；可展开周报明细按成员查看。'}</p><div class="table-wrap"><table class="data-table"><thead><tr><th>成员</th><th>部门</th><th>${projectScope?'负责项目投入':'项目投入'}</th>${projectScope?'<th>项目记录</th>':'<th>休假</th><th>学习/研究/培训</th><th>其他</th>'}<th>周报</th></tr></thead><tbody>${stats.members.filter(m=>m.active||m.submitted).map(m=>`<tr><td>${esc(m.name)}</td><td>${esc(m.department||'未分配部门')}</td><td>${number(m.project_effort)}%</td>${projectScope?`<td>${m.project_reported?'已有记录':'暂无记录'}</td>`:`<td>${number(m.categories.leave)}%</td><td>${number(m.categories.learning)}%</td><td>${number(m.categories.other)}%</td>`}<td><span class="badge ${m.submitted?'':'gray'}">${!m.expected?'无需填报':m.submitted?'已更新':'待更新'}</span></td></tr>`).join('')}</tbody></table></div>`;
  const reports = managementReports(stats,scopedReports);
  const recent = `<section class="activity"><div class="section-head"><h2>${icon('edit')}团队最近的进展</h2><span class="small muted">本周 ${scopedReports.length} 条记录</span></div><div class="panel">${scopedReports.length?weeklyPreviews(scopedReports.slice(0,4),true):empty('等待团队的新进展','周报更新后，会自动出现在这里。','',true)}</div></section>`;
  return metrics+`<div class="team-overview-grid"><div>${projectSummary}${recent}</div><aside>${right}${pendingPanel}</aside></div><div class="overview-detail-grid">${overviewDisclosure('members','人员投入与更新状态',members)}${overviewDisclosure('reports','周报明细',reports)}</div>`;
}

function workloadRow(name,effort,total,id=null) {
  return `<div class="workload-row"><div>${id?`<button type="button" class="text-button" data-action="detail" data-id="${id}">${esc(name)}</button>`:`<span>${esc(name)}</span>`}<strong>${number(effort/100)}<small>人周</small></strong></div><div class="workload-track"><span style="width:${total?Math.min(100,effort/total*100):0}%"></span></div></div>`;
}
function overviewDisclosure(key,title,body) {
  return `<details class="overview-details" data-overview-section="${key}" ${overviewSections.has(key)?'open':''}><summary>${title}${icon('down')}</summary><div class="overview-details-body">${body}</div></details>`;
}
function projectStatistics(stats) {
  return `<div class="table-wrap"><table class="data-table"><thead><tr><th>项目</th><th>本项目投入</th><th>含下级投入</th><th>参与人数</th></tr></thead><tbody>${treeRows(stats.projects).map(p=>`<tr><td><button class="text-button wbs-label" style="--depth:${Math.min(p.depth,10)}" data-action="detail" data-id="${p.id}">${p.depth?'↳ ':''}${esc(p.name)}</button></td><td>${number(p.effort)}%</td><td>${number(p.rolled_effort)}%</td><td>${p.contributors}</td></tr>`).join('') || '<tr><td colspan="4">暂无投入记录</td></tr>'}</tbody></table></div>${stats.scope==='company'?`<h3 class="detail-subhead">项目与部门投入明细</h3><div class="table-wrap"><table class="data-table"><thead><tr><th>项目</th><th>部门</th><th>投入</th></tr></thead><tbody>${stats.project_departments.map(r=>`<tr><td>${esc(projectPath(project(r.project_id)))}</td><td>${esc(r.department)}</td><td>${number(r.effort)}%</td></tr>`).join('') || '<tr><td colspan="3">暂无投入记录</td></tr>'}</tbody></table></div>`:''}`;
}
function managementReports(stats,rows) {
  const members = stats.members.filter(m=>m.active||m.submitted);
  if (analysisMember) rows=rows.filter(r=>r.user_id===Number(analysisMember));
  if (analysisProject) {const ids=subtreeIds(Number(analysisProject));rows=rows.filter(r=>ids.has(r.project_id));}
  const extras=stats.scope==='project'?[]:state.non_project_entries.filter(r=>r.week===viewWeek&&stats.members.some(m=>m.id===r.user_id)&&(!analysisMember||r.user_id===Number(analysisMember)));
  return `<div class="toolbar report-filters"><label class="inline-field">成员<select id="analysis-member"><option value="">全部成员</option>${members.map(m=>`<option value="${m.id}" ${analysisMember===String(m.id)?'selected':''}>${esc(m.name)}</option>`).join('')}</select></label><label class="inline-field">项目<select id="analysis-project"><option value="">全部项目</option>${treeRows(stats.projects).map(p=>`<option value="${p.id}" ${analysisProject===String(p.id)?'selected':''}>${'　'.repeat(p.depth)}${esc(p.name)}</option>`).join('')}</select></label></div>${reportTable(rows)}${!analysisProject&&extras.length?`<div class="non-project-history">${extras.map(r=>`<div><strong>${esc(person(r.user_id)?.name)} · ${categories[r.category]} ${number(r.effort)}%</strong><p>${esc(r.note)}</p></div>`).join('')}</div>`:''}`;
}
function followupPanel(stats) {
  const asOf=viewWeek===state.week?state.today:dateAdd(viewWeek,6);
  const overdue=stats.projects.filter(p=>!p.context_only&&!p.archived&&p.due_date<asOf&&project(p.id)&&projectProgress(project(p.id))<100);
  const attention=stats.members.filter(m=>(m.active||m.submitted) && (m.projects>=4 || (stats.scope!=='project'&&m.submitted&&m.project_effort+m.non_project_effort!==100)));
  const attentionPanel=attention.length?`<section class="activity"><div class="section-head"><h2>${icon('users')}人员投入提示</h2><span class="small muted">供工作协调参考</span></div><div class="panel panel-pad attention-list">${attention.map(m=>`<div class="attention-row">${avatar(person(m.id))}<div><strong>${esc(m.name)}</strong><p>${m.projects} 个项目 · 项目投入 ${number(m.project_effort)}%${stats.scope==='project'?'（仅负责范围）':''}</p></div><span class="badge amber">${m.projects>=4?'多项目并行':'总量待核对'}</span></div>`).join('') || '<p class="hint">暂无需要关注的投入分配。仅提示多项目并行与已提交周报总量异常，不作为绩效判断。</p>'}</div></section>`:'';
  const metrics=`<div class="metrics personal-metrics">${overviewMetric('等待更新',stats.pending_ids.length,'人','clock','本周仍待更新的周报','tone-2')+overviewMetric('需要协调',stats.coordination.length,'项','message','来自团队本周周报','tone-1')+overviewMetric('项目逾期',overdue.length,'个','calendar','需要关注计划与进度')}</div>`;
  return metrics+attentionPanel+`<section class="panel panel-pad"><div class="section-head"><h2>${icon('clock')}待更新周报</h2>${stats.pending_ids.length?button('一键提醒','remind-all','','bell'):''}</div><div class="pending-grid">${stats.pending_ids.map(id=>`<div class="pending-person">${avatar(person(id))}<div><strong>${esc(person(id)?.name)}</strong><small>${esc(person(id)?.department||'未分配部门')}</small></div><button type="button" class="text-button" data-action="remind-one" data-id="${id}">提醒</button></div>`).join('') || '<p class="hint">本周周报已全部更新。</p>'}</div></section>${coordinationSection([...stats.coordination,...stats.coordination_completed],'team-coordination',true)}<section class="activity"><div class="section-head"><h2>${icon('calendar')}需要关注的项目</h2><span class="small muted">${overdue.length} 个逾期项目</span></div><div class="project-grid">${overdue.map(p=>projectCard(project(p.id))).join('') || empty('项目正有序推进','暂无计划逾期的项目。','',true)}</div></section>`;
}

function coordinationControls(r) {
  if(!r.coordination?.trim()) return '';
  const completed=Boolean(r.coordination_completed);
  const completion=completed?`${esc(person(r.coordination_completed_by)?.name || '成员')} · ${displayTime(r.coordination_completed_at)}`:'';
  return `<div class="coordination-status"><span class="badge ${completed?'':'amber'}">${completed?'已完成':'待协调'}</span>${completed?`<span class="small muted">${completion}</span>`:''}${r.can_manage_coordination?`<button type="button" class="text-button" data-action="coordination-toggle" data-id="${r.id}">${completed?'重新打开':'标记完成'}</button>`:''}</div>`;
}
function coordinationCards(rows) {
  return rows.map(r=>`<article class="panel coordination-card ${r.coordination_completed?'coordination-done':''}" data-coordination-id="${r.id}"><div class="coordination-author">${avatar(person(r.user_id))}<div><strong>${esc(person(r.user_id)?.name)}</strong><small>${esc(projectPath(project(r.project_id)))}</small></div></div><p>${esc(r.coordination)}</p>${coordinationControls(r)}<button type="button" class="text-button" data-action="weekly-detail" data-id="${r.id}">查看周报${icon('right')}</button></article>`).join('');
}
function coordinationSection(rows,key,always=false) {
  if(!rows.length&&!always) return '';
  const pending=rows.filter(r=>!r.coordination_completed), completed=rows.filter(r=>r.coordination_completed);
  return `<section class="activity coordination-section"><div class="section-head"><h2>${icon('message')}需要协调的工作</h2><span class="small muted">${pending.length} 项待协调</span></div><div class="coordination-grid">${coordinationCards(pending) || '<p class="hint">本周暂无待协调的事项。</p>'}</div>${completed.length?overviewDisclosure(key,`已完成的协调事项 · ${completed.length} 项`,`<div class="coordination-grid">${coordinationCards(completed)}</div>`):''}</section>`;
}

function weeklyModal(id) {
  const report=state.reports.find(r=>r.id===id);
  if (!report) return;
  openModal('周报详情',`${esc(person(report.user_id)?.name)} · ${weekLabel(report.week,true)}`,`<div class="modal-body"><div class="weekly-modal-project">${icon('folder')}<strong>${esc(projectPath(project(report.project_id)))}</strong><span class="badge">本周投入 ${number(report.effort)}%</span></div>${reportText(report)}</div>`);
}
document.addEventListener('toggle',event=>{
  const key=event.target.dataset?.overviewSection;
  if (!key) return;
  if(event.target.open) overviewSections.add(key); else overviewSections.delete(key);
},true);
