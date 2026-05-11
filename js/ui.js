/* UI — init, navigation, dashboard, sidebar, stats */

function init() {
  RockoCore.onLog(e=>appendLog(e));
  RockoCore.onRender('projectLoaded',        ()=>refreshAll());
  RockoCore.onRender('agentStatusChanged',   ()=>refreshAll());
  RockoCore.onRender('agentUpdated',         ()=>refreshAll());
  RockoCore.onRender('agentCreated',        d=>{refreshAll();openAgent(d.agent.id);});
  RockoCore.onRender('agentDeleted',         ()=>{refreshAll();setTab('dashboard',null);});
  RockoCore.onRender('activeProjectChanged', ()=>refreshAll());
  RockoCore.onRender('pipelineStarted',      ()=>{renderPipeline();clearRunPanel();});
  RockoCore.onRender('pipelineComplete',    d=>{renderPipeline();renderRunPanel(d.run_ctx);});
  RockoCore.onRender('pipelineHalted',      d=>{renderPipeline();renderRunPanel(RockoCore.getRunContext());});
  RockoCore.onRender('pipelineStepUpdate', d=>{updateRunPanelStep(d);});
  RockoCore.onRender('bridgeStatus',       d=>{updateBridgeUI(d);});
  RockoCore.onRender('taskCreated',          ()=>refreshTasks());
  RockoCore.onRender('taskUpdated',          ()=>refreshTasks());
  RockoCore.onRender('taskDeleted',          ()=>refreshTasks());
  RockoCore.onRender('taskStatusChanged',    ()=>refreshTasks());
  RockoCore.onRender('runHistoryUpdated',    ()=>renderRunHistory());
  RockoCore.onRender('agentsSynced',        d=>showSyncResult(d));
  RockoCore.onRender('approvalRequired',    d=>showApprovalGate(d));
  RockoCore.onRender('approvalResolved',    d=>hideApprovalGate());

  RockoCore.setBridgeUrl(APP_CONFIG.bridge_url);
  const restored = RockoCore.loadState();
  if (!restored && APP_CONFIG.auto_load_default_project) {
    const b = BUNDLED_PROJECTS[APP_CONFIG.default_project];
    if (b) { try { RockoCore.loadProject(JSON.parse(b)); } catch(e) { RockoCore.log('error','Load error: '+e.message); } }
  }
  checkBridgeNow();
  setInterval(checkBridgeNow, APP_CONFIG.bridge_poll_interval_ms);
  startClock();
}

function refreshAll() {
  renderSidebar(); renderDashboard(); renderPipeline(); renderProjectDropdown();
  updateStats();
  try { renderSettings(); } catch (e) { console.warn('renderSettings skipped:', e && e.message ? e.message : e); }
  renderRunHistory(); refreshTasks(); updateSavedLabel();
  try { renderSkillDelegationPanel(); } catch (e) {}
}

function startClock() {
  const el=document.getElementById('dashClock');
  if (!el) return;
  function t(){el.textContent=new Date().toLocaleTimeString('en-US',{hour12:false});} t();setInterval(t,1000);
}

function updateSavedLabel() {
  const s = RockoCore.getLastSaved();
  const el = document.getElementById('savedLabel');
  if (!el) return;
  if (s) {
    el.textContent = 'Saved ' + (s.split('T')[1]?.split('.')[0] || '');
    el.style.color = 'var(--green)';
  } else {
    el.textContent = 'Not saved';
    el.style.color = 'var(--text-dim)';
  }
}

// ─────────────────────────────────────────────────────────
// BRIDGE
// ─────────────────────────────────────────────────────────
async function checkBridgeNow() {
  const dot=document.getElementById('bridgeDot'), lbl=document.getElementById('bridgeLabel');
  if (!dot) return;
  dot.className='bridge-dot checking'; lbl.textContent='Checking...';
  await RockoCore.checkBridge();
}

function updateBridgeUI(d) {
  const dot=document.getElementById('bridgeDot'), lbl=document.getElementById('bridgeLabel');
  if (!dot) return;
  const info=document.getElementById('settingsBridgeInfo');
  if(d.ok){
    dot.className='bridge-dot online'; lbl.textContent='Bridge: '+(d.data?.project_name||'Online'); lbl.style.color='var(--green)';
    if(info)info.innerHTML='<span style="color:var(--green)">● Bridge Online</span> — '+APP_CONFIG.bridge_url+'<br>Project: '+(d.data?.project_name||'—')+'<br>Executors: '+(d.data?.executors||[]).join(', ')||'none';
  }else{
    dot.className='bridge-dot offline'; lbl.textContent='Bridge: Offline'; lbl.style.color='var(--text-dim)';
    if(info)info.innerHTML='<span style="color:var(--text-dim)">○ Bridge Offline</span> — LLM agents still work<br><br>Start bridge:<br><span style="color:var(--yellow)">cd bridge && python bridge.py</span>';
  }
}

// ─────────────────────────────────────────────────────────
// VIEWS
// ─────────────────────────────────────────────────────────
function showView(v){document.querySelectorAll('.view').forEach(x=>x.classList.remove('active'));const el=document.getElementById('view-'+v);if(el)el.classList.add('active');}
function setTab(view,btn){
  document.querySelectorAll('.nav-tab').forEach(t=>t.classList.remove('active'));
  if(btn)btn.classList.add('active');
  else document.querySelectorAll('.nav-tab').forEach(t=>{if(t.textContent.toLowerCase().trim()===view)t.classList.add('active');});
  showView(view);
  if(view==='tasks')refreshTasks();
  if(view==='history')renderRunHistory();
  if(view==='skills'){loadSkillsLibrary().then(renderSkillsTab);renderSkillDelegationPanel();}
  if(view==='settings'){renderSettings();refreshSettingsExternalStatus();setTimeout(function(){if(RockoCore.isBridgeOnline())bridgeGet('/validate').then(function(d){if(d&&window._lastValidateResult!==JSON.stringify(d)){window._lastValidateResult=JSON.stringify(d);}});},300);}
}

// ─────────────────────────────────────────────────────────
// SIDEBAR
// ─────────────────────────────────────────────────────────
function renderSidebar(){
  const proj=RockoCore.getActiveProject();
  const agents=RockoCore.getAgents(proj).sort((a,b)=>String(a.pipeline_step).localeCompare(String(b.pipeline_step)));
  const rT=r=>r||'—';
  document.getElementById('agentList').innerHTML=`
    ${agents.map(a=>`
      <div class="agent-item ${currentAgentId===a.id?'active':''}" onclick="openAgent('${a.id}')">
        <div class="agent-status-dot status-${a.status}"></div>
        <div class="agent-name">${a.name}</div>
        <div class="agent-role-tag tag-${a.role}">${rT(a.role)}</div>
      </div>`).join('')}`;
  document.getElementById('agentCount').textContent=agents.length;
}

// ─────────────────────────────────────────────────────────
// DASHBOARD
// ─────────────────────────────────────────────────────────
function updateStats(){
  const proj=RockoCore.getActiveProject(), agents=RockoCore.getAgents(proj);
  const manifest=RockoCore.getProject(proj);
  const tasks=RockoCore.getTasks(proj).filter(t=>['queued','running','blocked'].includes(t.status));
  document.getElementById('statTotal').textContent=agents.length;
  document.getElementById('statActive').textContent=agents.filter(a=>a.status==='active').length;
  document.getElementById('statTasks').textContent=tasks.length;
  document.getElementById('statRuns').textContent=RockoCore.getRunHistory(proj).length;
  // statProjects removed — not in current stat grid
  document.getElementById('dashProjectTitle').textContent=manifest?.project?.display_name||proj||'Overview';
  document.getElementById('logProject').textContent=proj?'['+proj+']':'';
  document.getElementById('taskCount').textContent=tasks.length;
  const badge=document.getElementById('sidebarTaskBadge');
  if (!badge) return;
  if(tasks.length>0){badge.textContent=tasks.length;badge.style.display='';}else{badge.style.display='none';}
  if(proj){
    const h=RockoCore.getProjectHealth(proj);
    const banner=document.getElementById('healthBanner');
    if (!banner) return;
    if(h.errors.length||h.warns.length){
      banner.style.display='block';
      banner.innerHTML='<div style="font-family:var(--mono);font-size:10px;letter-spacing:2px;color:var(--text-dim);text-transform:uppercase;margin-bottom:8px;">Project Health</div>'+
        h.errors.map(e=>'<div style="font-family:var(--mono);font-size:11px;color:var(--red);margin-bottom:3px;">✕ '+e+'</div>').join('')+
        (h.warns.length?'<div style="font-family:var(--mono);font-size:11px;color:var(--yellow);">⚠ '+h.warns.length+' warnings — Settings → Validate</div>':'');
    }else{banner.style.display='none';}
    document.getElementById('persistBadge').textContent=h.bridgeNeeded?(RockoCore.isBridgeOnline()?'🟢 Bridge':'🔴 Bridge needed'):'';
  }
}

function _agentColor(id){
  // Deterministic unique color per agent from their ID string
  var palette=['#4782ff','#06b6d4','#10b981','#f59e0b','#a78bfa','#f43f5e','#fb923c','#34d399','#38bdf8','#c084fc','#fbbf24','#60a5fa'];
  var hash=0; for(var i=0;i<(id||'').length;i++){hash=(hash*31+id.charCodeAt(i))>>>0;}
  return palette[hash%palette.length];
}
function renderDashboard(){
  const proj=RockoCore.getActiveProject();
  const agents=RockoCore.getAgents(proj).sort((a,b)=>String(a.pipeline_step).localeCompare(String(b.pipeline_step)));
  // Role icons cover all known and unknown roles gracefully
  const rE=r=>{
    const rl=(r||'').toLowerCase();
    if(rl==='ceo'||rl==='director')return'★';
    if(rl==='analyst'||rl==='research')return'◎';
    if(rl==='engine'||rl==='executor')return'⚙';
    if(rl==='custom')return'✦';
    if(rl==='agent')return'◆';
    if(rl==='support')return'♦';
    if(rl==='writer'||rl==='content')return'✏';
    if(rl==='risk')return'⚑';
    if(rl==='trader'||rl==='trading')return'◈';
    // First letter of role as fallback — never show ?
    return (r||'?').charAt(0).toUpperCase();
  };
  const sC=s=>({active:'var(--green)',idle:'var(--text-dim)',paused:'var(--yellow)',error:'var(--red)',fired:'var(--red)'})[s]||'var(--text-dim)';
  document.getElementById('agentCardsGrid').innerHTML=agents.map(function(a){
    var col=_agentColor(a.id);
    var role=a.role||'agent';
    var status=a.status||'idle';
    var isCeo=role==='ceo';
    return '<div class="agent-card '+(isCeo?'ceo':'')+' fade-in" style="border-top:2px solid '+col+';cursor:pointer" onclick="openAgent(\'' +a.id+ '\')">'
      +'<div class="agent-card-header">'
      +'<div class="card-icon" style="border-color:'+col+';color:'+col+';background:rgba(0,0,0,.18);font-size:15px;font-weight:700;">'+rE(role)+'</div>'
      +'<div style="flex:1">'
      +'<div class="agent-card-name">'+a.name+'</div>'
      +'<div style="font-family:var(--mono);font-size:9px;color:'+sC(status)+';letter-spacing:1px">&#9679; '+status.toUpperCase()+'</div>'
      +'</div>'
      +'<div style="font-family:var(--mono);font-size:9px;padding:2px 6px;border-radius:3px;background:'+col+'22;color:'+col+';border:1px solid '+col+'44;">'+role.toUpperCase()+'</div>'
      +'</div>'
      +'<div class="agent-card-desc">'+(a.description||'No description.')+'</div>'
      +'<div class="agent-card-meta"><span>'+(a.model||'&mdash;')+'</span><span style="color:var(--border)">|</span><span>'+(a.provider||'&mdash;')+'</span></div>'
      +'<div class="card-actions" onclick="event.stopPropagation()">'
      +'<button class="card-btn run" style="border-color:'+col+'66;color:'+col+'" onclick="runAgentCard(\'' +a.id+ '\')">'+(status==='active'?'&#9208; Pause':'&#9654; Run')+'</button>'
      +'<button class="card-btn" onclick="openAgent(\'' +a.id+ '\')">&#9998; Edit</button>'
      +'<button class="card-btn del" onclick="confirmDelete(\'' +a.id+ '\')">&#x2715;</button>'
      +'</div>'
      +'</div>';
  }).join('');
}

// ─────────────────────────────────────────────────────────
// PIPELINE
// ─────────────────────────────────────────────────────────