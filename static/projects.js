'use strict';

const expandedProjects = new Set();
let projectRelation = '';
function projectRelations() {
  return [
    ...(state.capabilities.project_management?[['owned','我负责的']]:[]),
    ...((state.permissions.stats_department||state.capabilities.department_management)?[['department','部门项目']]:[]),
    ['participating','我参与的'],['all','全部可见']
  ];
}

function filteredProjects() {
  const items=state.projects.filter(p=>(projectRelation==='all'||(projectRelation==='owned'&&p.is_owned)||(projectRelation==='department'&&p.is_department)||(projectRelation==='participating'&&p.is_participant)) && (projectFilter==='all'||Boolean(p.effective_archived)===(projectFilter==='archived')) &&
    `${projectPath(p)} ${person(p.owner_id)?.name || ''}`.toLowerCase().includes(search.toLowerCase()));
  const included=new Map(items.map(p=>[p.id,p]));
  for(const p of items) {
    let ancestor=project(p.parent_id),seen=new Set();
    while(ancestor&&!seen.has(ancestor.id)) {
      seen.add(ancestor.id);included.set(ancestor.id,ancestor);ancestor=project(ancestor.parent_id);
    }
  }
  return [...included.values()];
}

function projectsPage() {
  const relations=projectRelations();
  if(!relations.some(([key])=>key===projectRelation)) projectRelation=state.user.role==='admin'?'all':state.capabilities.department_management?'department':state.capabilities.project_management?'owned':'all';
  return `<div class="page-head"><div><div class="eyebrow">A SPACE FOR EVERY PROJECT</div><h1>项目空间</h1><p>把目标与阶段安排清楚，按负责范围协作推进。</p></div><div class="head-actions">${state.capabilities.create_project?button('新建项目','new-project','primary','plus'):''}</div></div><div class="project-relations" aria-label="项目范围">${relations.map(([key,label])=>`<button type="button" class="relation-chip ${projectRelation===key?'selected':''}" data-action="project-relation" data-value="${key}" aria-pressed="${projectRelation===key}">${label}</button>`).join('')}</div><div class="toolbar"><div class="tabs">${[['active','进行中'],['archived','已归档'],['all','全部状态']].map(([key,label])=>`<button type="button" class="tab ${projectFilter===key?'active':''}" data-action="filter" data-value="${key}">${label}</button>`).join('')}</div><div class="search">${icon('search')}<input id="project-search" placeholder="搜索项目或负责人" aria-label="搜索项目" value="${esc(search)}"></div></div><div id="project-list">${projectTree(filteredProjects())}</div>`;
}

function projectTree(items) {
  if(!items.length) return empty('暂无匹配项目','调整搜索或项目状态，或新建一个项目。');
  const ids=new Set(items.map(p=>p.id)), children=new Map();
  for(const p of items) {
    const parent=ids.has(p.parent_id)?p.parent_id:null;
    if(!children.has(parent)) children.set(parent,[]);
    children.get(parent).push(p);
  }
  function node(p,depth,seen) {
    if(seen.has(p.id)) return '';
    const nextSeen=new Set([...seen,p.id]),nested=(children.get(p.id)||[]).sort((a,b)=>a.id-b.id);
    const expanded=nested.length>0&&(expandedProjects.has(p.id)||Boolean(search.trim()));
    const action=nested.length?'toggle-project':'detail';
    return `<div class="project-node ${depth?'nested-project':'root-project'}" data-project-node="${p.id}" role="treeitem" ${nested.length?`aria-expanded="${expanded}"`:''}><div class="project-tree-row"><div class="project-tree-title"><button type="button" class="project-title-button" data-action="${action}" data-id="${p.id}" ${nested.length?`aria-expanded="${expanded}" aria-controls="project-children-${p.id}"`:''}><span class="project-expander ${expanded?'expanded':''}">${nested.length?icon('right'):'<i></i>'}</span><span class="project-symbol ${depth?'tone-1':''}">${icon(nested.length?'folder':'code')}</span><span class="project-name-group"><strong>${esc(p.name)}</strong><small>${p.context_only?'上级项目':nested.length?`${nested.length} 个子项目`:depth?'项目阶段':'独立项目'}</small></span></button>${projectBadge(p)}</div><div class="project-tree-owner">${avatar(person(p.owner_id))}<span>${esc(person(p.owner_id)?.name)}</span></div><div class="project-tree-date">${icon('calendar')}<span>${p.due_date.slice(5).replace('-','/')} 截止</span></div><div class="project-tree-actions"><button type="button" class="icon-button" data-action="detail" data-id="${p.id}" aria-label="查看${esc(p.name)}详情" title="查看详情">${icon('info')}</button>${p.can_manage?`<button type="button" class="icon-button" data-action="edit-project" data-id="${p.id}" aria-label="配置${esc(p.name)}" title="配置项目">${icon('edit')}</button>${!p.effective_archived?`<button type="button" class="icon-button" data-action="new-child" data-id="${p.id}" aria-label="为${esc(p.name)}添加子项目" title="添加子项目">${icon('plus')}</button>`:''}`:''}</div></div>${expanded?`<div id="project-children-${p.id}" class="project-children" role="group">${nested.map(child=>node(child,depth+1,nextSeen)).join('')}</div>`:''}</div>`;
  }
  return `<div class="project-tree" role="tree" aria-label="项目层级">${(children.get(null)||[]).sort((a,b)=>b.id-a.id).map(p=>node(p,0,new Set())).join('')}</div>`;
}

function revealProject(id) {
  const seen=new Set();
  let p=project(id);
  while(p?.parent_id&&!seen.has(p.id)) {
    seen.add(p.id);expandedProjects.add(p.parent_id);p=project(p.parent_id);
  }
}
