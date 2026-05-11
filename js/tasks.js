/* ============================================================
   TASKS — task system, run history, agent sync
   ============================================================ */

  _taskFilterVal=val;
  document.querySelectorAll('.filter-btn').forEach(b=>b.classList.remove('active'));
  btn.classList.add('active');
  refreshTasks();
}
function refreshTasks(){
  const proj=RockoCore.getActiveProject();
  const tasks=RockoCore.getTasks(proj,_taskFilterVal||null);
  const el=document.getElementById('taskListBody');
  if (!el) return;
  const countEl=document.getElementById('taskListCount');
  if(countEl)countEl.textContent=tasks.length;
  const sC=s=>({queued:'var(--text-dim)',running:'var(--green)',blocked:'var(--yellow)',complete:'var(--green)',failed:'var(--red)'})[s]||'var(--text-dim)';
  if(!tasks.length){
    el.innerHTML='<div style="font-family:var(--mono);font-size:11px;color:var(--text-dim);padding:20px;text-align:center;">No tasks'+(_taskFilterVal?' with status: '+_taskFilterVal:'')+'</div>';
    return;
  }
  el.innerHTML='';
  tasks.forEach(function(t){
    const agent=RockoCore.getAgent(t.agentId);
    const dur=t.durationMs?Math.round(t.durationMs/100)/10+'s':'--';
    const sCol=sC(t.status);
    const div=document.createElement('div');
    div.className='task-item'+(selectedTaskId===t.id?' selected':'');
    div.dataset.id=t.id;
    div.addEventListener('click',function(){selectTask(t.id);});
    const infoHtml=
      '<div class="task-status-dot ts-'+t.status+'"></div>'+
      '<div class="task-info">'+
        '<div class="task-name">'+t.name+'</div>'+
        '<div class="task-meta">'+
          '<span style="color:'+sCol+'">'+t.status.toUpperCase()+'</span>'+
          (agent?'<span>&#8594; '+agent.name+'</span>':'')+
          (t.durationMs?'<span>'+dur+'</span>':'')+
          (t.retryCount?'<span>retry #'+t.retryCount+'</span>':'')+
        '</div>'+
      '</div>';
    div.innerHTML=infoHtml;
    const actions=document.createElement('div');
    actions.className='task-actions';
    if(t.status==='queued'){
      const b=document.createElement('button');
      b.className='task-btn run';b.textContent='\u25b6';
      b.addEventListener('click',function(e){e.stopPropagation();runSingleTask(t.id);});
      actions.appendChild(b);
    }
    if(t.status==='failed'||t.status==='blocked'){
      const b=document.createElement('button');
      b.className='task-btn';b.textContent='\u21ba';
      b.addEventListener('click',function(e){e.stopPropagation();retrySingleTask(t.id);});
      actions.appendChild(b);
    }
    const del=document.createElement('button');
    del.className='task-btn del';del.textContent='\u2715';
    del.addEventListener('click',function(e){e.stopPropagation();deleteSingleTask(t.id);});
    actions.appendChild(del);
    div.appendChild(actions);
    el.appendChild(div);
  });
}

function selectTask(id){
  selectedTaskId=id; refreshTasks();
  const task=RockoCore.getTask(id); if(!task)return;
  const agent=RockoCore.getAgent(task.agentId);
  const dp=document.getElementById('taskDetailPanel');
  if (!dp) return;
  dp.className='task-detail fade-in';
  const sC=s=>({queued:'var(--text-dim)',running:'var(--green)',blocked:'var(--yellow)',complete:'var(--green)',failed:'var(--red)'})[s]||'var(--text-dim)';
  const subtasks=RockoCore.getSubTasks(id);
  let html='<div class="field-group"><div class="field-label">Task</div>'+
    '<div style="font-family:var(--cond);font-size:20px;font-weight:700;letter-spacing:1px;">'+task.name+'</div></div>'+
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px;">'+
      '<div class="info-card"><div class="info-card-label">Status</div><div class="info-card-val" style="color:'+sC(task.status)+'">'+task.status.toUpperCase()+'</div></div>'+
      '<div class="info-card"><div class="info-card-label">Agent</div><div class="info-card-val">'+(agent?agent.name:task.agentId||'--')+'</div></div>'+
      '<div class="info-card"><div class="info-card-label">Priority</div><div class="info-card-val">'+task.priority+'</div></div>'+
      '<div class="info-card"><div class="info-card-label">Duration</div><div class="info-card-val">'+(task.durationMs?Math.round(task.durationMs)+'ms':'--')+'</div></div>'+
    '</div>'+
    (task.description?'<div class="field-group"><div class="field-label">Description</div><div style="font-size:13px;color:var(--text-secondary);line-height:1.6;">'+task.description+'</div></div>':'')+
    (task.error?'<div class="field-group"><div class="field-label">Error</div><div style="font-family:var(--mono);font-size:11px;color:var(--red);padding:10px;background:var(--red-dim);border:1px solid rgba(255,71,87,.3);">'+task.error+'</div></div>':'')+
    (task.output?'<div class="field-group"><div class="field-label">Output</div><div class="task-output-box">'+task.output+'</div></div>':'')+
    (subtasks.length?'<div class="field-group"><div class="field-label">Sub-Tasks ('+subtasks.length+')</div>'+subtasks.map(st=>'<div style="font-family:var(--mono);font-size:11px;padding:6px 10px;background:var(--bg-card);border:1px solid var(--border);margin-bottom:4px;color:var(--text-secondary);">'+st.name+' -- '+st.status+'</div>').join('')+'</div>':'');
  dp.innerHTML=html;
  const actRow=document.createElement('div');
  actRow.style.cssText='display:flex;gap:8px;flex-wrap:wrap;margin-top:4px;';
  if(task.status==='queued'){const b=document.createElement('button');b.className='btn btn-success';b.textContent='Run Now';b.onclick=()=>runSingleTask(id);actRow.appendChild(b);}
  if(task.status==='failed'||task.status==='blocked'){const b=document.createElement('button');b.className='btn btn-warn';b.textContent='Retry';b.onclick=()=>retrySingleTask(id);actRow.appendChild(b);}
  const bSub=document.createElement('button');bSub.className='btn btn-ghost';bSub.textContent='+ Sub-Task';bSub.onclick=()=>createSubTask(id);actRow.appendChild(bSub);
  const bDel=document.createElement('button');bDel.className='btn btn-danger';bDel.textContent='Delete';bDel.onclick=()=>deleteSingleTask(id);actRow.appendChild(bDel);
  dp.appendChild(actRow);
}

async function runSingleTask(id){await RockoCore.runTask(id);refreshTasks();if(selectedTaskId===id)selectTask(id);}
async function retrySingleTask(id){await RockoCore.retryTask(id);refreshTasks();if(selectedTaskId===id)selectTask(id);}
function deleteSingleTask(id){if(!confirm('Delete this task?'))return;RockoCore.deleteTask(id);if(selectedTaskId===id){selectedTaskId=null;document.getElementById('taskDetailPanel').className='task-detail-empty';document.getElementById('taskDetailPanel').textContent='Select a task';}refreshTasks();}
async function runAllQueued(){
  const proj=RockoCore.getActiveProject();
  const queued=RockoCore.getTasks(proj,'queued');
  if(!queued.length){RockoCore.log('info','No queued tasks');return;}
  RockoCore.log('system','Running '+queued.length+' queued tasks...');
  for(const t of queued){await RockoCore.runTask(t.id);}
}
function populateTaskAgentSelect(defaultId=''){
  const proj=RockoCore.getActiveProject();
  const agents=RockoCore.getAgents(proj);
  const sel=document.getElementById('taskAgentSelect');
  if (!sel) return;
  sel.innerHTML=agents.map(a=>'<option value="'+a.id+'"'+(a.id===defaultId?' selected':'')+'>'+a.name+'</option>').join('');
}
function openCreateTaskModal(){
  populateTaskAgentSelect();
  ['taskName','taskDesc','taskInput'].forEach(id=>document.getElementById(id).value='');
  document.getElementById('taskPriority').value='normal';
  openModal('createTaskModal');
}
function _buildTaskFromModal(){
  const name=document.getElementById('taskName').value.trim(); if(!name){alert('Name required');return null;}
  const agentId=document.getElementById('taskAgentSelect').value; if(!agentId){alert('Select an agent');return null;}
  let input={};const inp=document.getElementById('taskInput').value.trim();
  if(inp){try{input=JSON.parse(inp);}catch(e){alert('Invalid JSON in input: '+e.message);return null;}}
  return{name,agentId,projectName:RockoCore.getActiveProject(),description:document.getElementById('taskDesc').value,priority:document.getElementById('taskPriority').value,input};
}
function createTaskOnly(){const def=_buildTaskFromModal();if(!def)return;RockoCore.createTask(def);closeModal('createTaskModal');setTab('tasks',null);}
async function createAndRunTask(){const def=_buildTaskFromModal();if(!def)return;const t=RockoCore.createTask(def);closeModal('createTaskModal');setTab('tasks',null);await RockoCore.runTask(t.id);selectTask(t.id);}
function createSubTask(parentId){
  const parent=RockoCore.getTask(parentId);if(!parent)return;
  const name=prompt('Sub-task name:');if(!name)return;
  const t=RockoCore.createTask({name,agentId:parent.agentId,projectName:parent.projectName,description:'',input:{},parentId,priority:'normal'});
  selectTask(parentId);
}

// ─────────────────────────────────────────────────────────
// RUN HISTORY
// ─────────────────────────────────────────────────────────
function renderRunHistory(){
  const proj=RockoCore.getActiveProject();
  const runs=RockoCore.getRunHistory(proj);
  const el=document.getElementById('historyList');
  const countEl=document.getElementById('historyCount');
  if(!el) return;
  if(countEl)countEl.textContent=runs.length+' run'+(runs.length!==1?'s':'');
  if(!runs.length){
    el.innerHTML='<div style="font-family:var(--mono);font-size:11px;color:var(--text-dim);padding:20px;text-align:center;">No runs yet.</div>';
    return;
  }
  const sC=s=>({complete:'var(--green)',halted:'var(--yellow)',rejected:'var(--red)',failed:'var(--red)'})[s]||'var(--text-dim)';
  el.innerHTML='';
  runs.forEach(r=>{
    const stepsArr=Object.values(r.steps||{});
    const stepDots=stepsArr.map(s=>'<div class="history-step-dot" style="background:'+(s.status==='complete'?'var(--green)':s.status==='error'||s.status==='rejected'?'var(--red)':'var(--text-dim)')+'"></div>').join('');
    const dur=r.completed_at&&r.started_at?Math.round((new Date(r.completed_at)-new Date(r.started_at))/1000)+'s':'—';
    const div=document.createElement('div');
    div.className='history-item'+(selectedRunId===r.run_id?' selected':'');
    div.dataset.runid=r.run_id;
    div.onclick=()=>selectRun(r.run_id);
    div.innerHTML=
      '<div style="flex:1">'+
        '<div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">'+
          '<div class="history-run-id">'+r.run_id.slice(-12)+'</div>'+
          '<div class="history-status" style="color:'+sC(r.status)+'">'+r.status.toUpperCase()+'</div>'+
          '<div class="history-time">'+(r.archived_at?new Date(r.archived_at).toLocaleTimeString():dur)+'</div>'+
        '</div>'+
        '<div class="history-steps">'+stepDots+'</div>'+
      '</div>';
    el.appendChild(div);
  });
}

function selectRun(runId){
  selectedRunId=runId; renderRunHistory();
  const run=RockoCore.getRunById(runId);if(!run)return;
  const el=document.getElementById('historyDetail');
  if (!el) return;
  const sC=s=>({complete:'var(--green)',error:'var(--red)',skipped:'var(--yellow)',rejected:'var(--red)',pending_approval:'var(--yellow)'})[s]||'var(--text-dim)';
  const dur=run.completed_at&&run.started_at?Math.round((new Date(run.completed_at)-new Date(run.started_at))/1000)+'s':'--';
  let html='<div style="font-family:var(--cond);font-size:18px;font-weight:700;margin-bottom:14px;color:var(--accent)">'+run.run_id+'</div>'+
    '<div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:16px;">'+
    '<div class="info-card"><div class="info-card-label">Status</div><div class="info-card-val">'+run.status.toUpperCase()+'</div></div>'+
    '<div class="info-card"><div class="info-card-label">Duration</div><div class="info-card-val">'+dur+'</div></div>'+
    '<div class="info-card"><div class="info-card-label">Started</div><div class="info-card-val">'+(run.started_at?run.started_at.split('T')[1].split('.')[0]:'--')+'</div></div>'+
    '<div class="info-card"><div class="info-card-label">Steps</div><div class="info-card-val">'+Object.keys(run.steps||{}).length+'</div></div>'+
    '</div>'+
    '<div style="font-family:var(--mono);font-size:10px;letter-spacing:2px;color:var(--text-dim);text-transform:uppercase;margin-bottom:8px;">Steps</div>';
  Object.entries(run.steps||{}).forEach(function(pair){
    var sid=pair[0],step=pair[1];
    html+='<div style="background:var(--bg-card);border:1px solid var(--border);padding:10px 12px;margin-bottom:6px;">'+
      '<div style="display:flex;align-items:center;gap:10px;margin-bottom:4px;">'+
        '<div style="font-family:var(--mono);font-size:11px;color:var(--accent);flex:1">'+sid+'</div>'+
        '<div style="font-family:var(--mono);font-size:10px;color:'+sC(step.status)+'">'+step.status.toUpperCase()+'</div>'+
        '<div style="font-family:var(--mono);font-size:10px;color:var(--text-dim);">'+(step.duration_ms||0)+'ms</div>'+
      '</div>'+
      (step.error?'<div style="font-family:var(--mono);font-size:10px;color:var(--red);margin-top:4px;">'+step.error.slice(0,200)+'</div>':'')+
    '</div>';
  });
  el.innerHTML=html;
  var expBtn=document.createElement('button');
  expBtn.className='btn btn-ghost';expBtn.style.marginTop='12px';expBtn.textContent='Export Report';
  expBtn.addEventListener('click',function(){exportRunReport(runId);});
  el.appendChild(expBtn);
}



// ─────────────────────────────────────────────────────────
// AGENT SYNC
// ─────────────────────────────────────────────────────────
async function syncAgentsNow(){
  const proj=RockoCore.getActiveProject();
  RockoCore.log('system','Syncing agents from project manifest...');
  const result=await RockoCore.syncAgentsFromManifest(proj);
  showSyncResult({projectName:proj,results:result.results});
  refreshAll();
}
function showSyncResult(d){
  const el=document.getElementById('syncResult'); if(!el)return;
  const r=d.results||{};
  el.className='sync-result show';
  el.innerHTML='<div style="color:var(--green);margin-bottom:4px;">✓ Sync complete — '+d.projectName+'</div>'+
    '<div style="color:var(--green)">+ '+r.created?.length+' created</div>'+
    '<div style="color:var(--accent)">~ '+r.updated?.length+' updated</div>'+
    (r.missing_files?.length?'<div style="color:var(--yellow);margin-top:6px;">⚠ Missing AGENT.md files ('+r.missing_files.length+'):</div>'+r.missing_files.map(f=>'<div style="color:var(--text-dim);padding-left:8px;">'+f.name+' → '+f.file+'</div>').join(''):'');
}

// ─────────────────────────────────────────────────────────
// SETTINGS
// ─────────────────────────────────────────────────────────
function createMissingCEO() {
  var co   = getActiveCompany();
  var proj = RockoCore.getActiveProject();

  if (!co || !proj) {
    toastErr('No active company');
    return;
  }

  ensureCompanyCEO(proj, co.display_name || co.id || proj, co.description || '');
  syncCompanyAgentCache(proj);
  RockoCore.saveState();

  toastOk('CEO repaired for ' + (co.display_name || co.id || proj));

  var btn = document.getElementById('createCeoBtn');
  if (btn) btn.style.display = 'none';

  refreshAll();
  setTimeout(function(){ refreshAll(); }, 300);
}

// ── Settings form ─────────────────────────────────────────────
function settingsMarkDirty() {
