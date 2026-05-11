/* ============================================================
   INTELLIGENCE — settings, bridge, workers, schedules,
                  orchestration, runtimes
   ============================================================ */

  var btn = document.getElementById('saveSettingsBtn');
  if (btn) { btn.textContent = '💾 Save Settings *'; btn.style.background = 'var(--yellow)'; btn.style.color = '#000'; }
}

function settingsProviderChanged() {
  var prov = (document.getElementById('cfg-provider')||{}).value || 'later';
  var row  = document.getElementById('cfg-base-url-row');
  if (row) row.style.display = (prov==='ollama'||prov==='lmstudio'||prov==='custom') ? '' : 'none';
  var modelMap = {anthropic:'claude-sonnet-4-20250514',openai:'gpt-4o',gemini:'gemini-2.0-flash',nvidia:'nvidia/llama-3.1-nemotron-ultra-253b-v1',ollama:'llama3.2',lmstudio:'local-model',later:''};
  var mInput = document.getElementById('cfg-model');
  if (mInput && !mInput.value) mInput.value = modelMap[prov] || '';
}

function loadSettingsForm() {
  var co = getActiveCompany(); if (!co) return;
  var _s = function(id, val) { var el=document.getElementById(id); if(el) el.value = val||''; };
  _s('cfg-company-name', co.display_name);
  _s('cfg-project-path', co.project_path);
  _s('cfg-description',  co.description);
  _s('cfg-provider',     co.default_provider || 'later');
  _s('cfg-model',        co.default_model    || '');
  _s('cfg-base-url',     co.base_url         || '');
  _s('cfg-email',        co.email            || '');
  _s('cfg-webhook',      co.webhook          || '');
  // Load stored API keys (from localStorage, never sent to server unencrypted)
  var keys = JSON.parse(localStorage.getItem('rocko_api_keys')||'{}');
  _s('cfg-key-anthropic', keys.anthropic || '');
  _s('cfg-key-openai',    keys.openai    || '');
  _s('cfg-key-gemini',    keys.gemini    || '');
  _s('cfg-key-nvidia',    keys.nvidia    || '');
  settingsProviderChanged();
  // CEO status
  var proj = RockoCore.getActiveProject();
  var m    = proj ? RockoCore.getProject(proj) : null;
  var hasCeo = m && (m.agents||[]).some(function(a){return a.role==='ceo';});
  var ceoLbl = document.getElementById('ceoStatusLabel');
  var ceoBtn = document.getElementById('createCeoBtn');
  if (ceoLbl) ceoLbl.textContent = hasCeo ? ('CEO active — ' + (co.display_name||'')) : 'No CEO agent — create one below';
  if (ceoBtn) ceoBtn.style.display = hasCeo ? 'none' : '';
  // Reset dirty state
  var saveBtn = document.getElementById('saveSettingsBtn');
  if (saveBtn) { saveBtn.textContent = '💾 Save Settings'; saveBtn.style.background=''; saveBtn.style.color=''; }
}

function saveSettingsForm() {
  var _g = function(id){ var el=document.getElementById(id); return el?el.value.trim():''; };
  var co = getActiveCompany(); if (!co) { toastErr('No active company'); return; }
  // Update company object
  co.display_name     = _g('cfg-company-name') || co.display_name;
  co.project_path     = _g('cfg-project-path') || co.project_path;
  co.description      = _g('cfg-description');
  co.default_provider = _g('cfg-provider');
  co.default_model    = _g('cfg-model');
  co.base_url         = _g('cfg-base-url');
  co.email            = _g('cfg-email');
  co.webhook          = _g('cfg-webhook');
  co.provider_status  = 'pending';
  // Save API keys to localStorage only
  var keys = {
    anthropic: _g('cfg-key-anthropic'),
    openai:    _g('cfg-key-openai'),
    gemini:    _g('cfg-key-gemini'),
    nvidia:    _g('cfg-key-nvidia'),
  };
  localStorage.setItem('rocko_api_keys', JSON.stringify(keys));
  // Persist company
  var list = getCompanies();
  var idx  = list.findIndex(function(c){ return c.id===co.id; });
  if (idx > -1) list[idx] = co;
  saveCompanies(list);
  bridgePost('/companies', {
    id: co.id, display_name: co.display_name, description: co.description,
    project_path: co.project_path, active: true,
  });
  var res = document.getElementById('settingsSaveResult');
  if (res) { res.textContent = '✓ Saved at ' + new Date().toLocaleTimeString(); res.style.color='var(--green)'; }
  var saveBtn = document.getElementById('saveSettingsBtn');
  if (saveBtn) { saveBtn.textContent = '💾 Save Settings'; saveBtn.style.background=''; saveBtn.style.color=''; }
  toastOk('Settings saved');
  updateTopbarCompany(co);
}

function renderSettings(){
  const proj=RockoCore.getActiveProject(),m=RockoCore.getProject(proj);if(!m)return;
  const card=(k,v)=>'<div class="info-card"><div class="info-card-label">'+k+'</div><div class="info-card-val">'+(v||'—')+'</div></div>';
  const safeSetHTML=function(id,html){ var el=document.getElementById(id); if(el) el.innerHTML=html; };
  safeSetHTML('settingsProjectInfo',[card('ID',m.project&&m.project.id),card('Name',(m.project&&m.project.display_name)||(m.project&&m.project.name)),card('Version',m.project&&m.project.version),card('Schema',m.schema_version),card('Root',m.project&&m.project.root_path),card('Desc',m.project&&m.project.description)].join(''));
  var _mp=m.model||{};var _mProv=_mp.default_provider||'__unset__';var _mProvCfg=(_mp.providers||{})[_mProv]||{};
  var _ceoDef=(m.agents||[]).find(function(a){return a&&(a.role==='ceo'||a.id==='ceo_agent');})||null;
  if(!_ceoDef && typeof ensureCompanyCEO==='function') { _ceoDef=ensureCompanyCEO(proj,(m.project&&m.project.display_name)||(m.project&&m.project.name)||proj,(m.project&&m.project.description)||''); }
  var _ceoOverride=_ceoDef&&_ceoDef.model_override?_ceoDef.model_override:'using default';
  var hasCeo=!!_ceoDef;
  var ceoBtn=document.getElementById('createCeoBtn'); if(ceoBtn)ceoBtn.style.display=hasCeo?'none':'';
  var ceoLbl=document.getElementById('ceoStatusLabel');
  if(ceoLbl)ceoLbl.textContent=hasCeo?('CEO: '+(_ceoDef.display_name||_ceoDef.name)+' — '+(_ceoDef.provider_status||'configured')):'No CEO — click to create';
  safeSetHTML('settingsModelInfo',[card('Provider',_mProv==='__unset__'?'Not configured':_mProv),card('Default Model',_mp.default_model||'—'),card('Fallback',_mp.fallback_model||'—'),card('CEO Override',_ceoOverride),card('Temp',_mProvCfg.temperature!==undefined?_mProvCfg.temperature:'—'),card('Max Tokens',_mProvCfg.max_tokens!==undefined?_mProvCfg.max_tokens:'—')].join(''));
  const req=(m.env&&m.env.required)||[],opt=(m.env&&m.env.optional)||[];
  safeSetHTML('settingsEnvInfo','<div class="validation-panel"><div style="font-family:var(--mono);font-size:11px;margin-bottom:6px;color:var(--text-secondary)">Required: '+req.map(v=>'<span style="color:var(--yellow);margin-right:8px">'+v+'</span>').join('')+'</div><div style="font-family:var(--mono);font-size:11px;color:var(--text-dim)">Optional: '+opt.map(v=>'<span style="margin-right:8px">'+v+'</span>').join('')+'</div></div>');
  loadSettingsForm();
}
function runValidation(){
  const proj=RockoCore.getActiveProject(),m=RockoCore.getProject(proj);if(!m)return;
  const panel=document.getElementById('settingsValidation');
  if (!panel) return;
  panel.innerHTML='<div class="val-title">Validating...</div>';
  // Client-side health check
  const h=RockoCore.getProjectHealth(proj);
  let html='<div class="val-title">Validation — '+proj+'</div>';
  h.errors.forEach(function(e){html+='<div class="val-item err">✕ '+e+'</div>';});
  h.warns.forEach(function(w){html+='<div class="val-item warn">⚠ '+w+'</div>';});
  html+='<div class="val-title" style="margin-top:12px;">Agents ('+h.agents.length+')</div>';
  h.agents.forEach(function(a){
    const pT=a.permittedTools.map(function(t){return '<span class="perm-badge perm-ok">'+t+'</span>';}).join('');
    const pA=a.permittedApis.map(function(t){return '<span class="perm-badge perm-ok">'+t+'</span>';}).join('');
    html+='<div class="val-item '+(a.hasInstructions?'ok':'warn')+'">'+(a.hasInstructions?'✓':'⚠')+' '+a.name+' — instructions '+(a.hasInstructions?'loaded':'empty')+', status: '+a.status+'<br>'+
      '<span style="font-size:9px;color:var(--text-dim);">tools: '+(pT||'none')+' &nbsp; apis: '+(pA||'none')+'</span></div>';
  });
  if(h.executors.length){
    html+='<div class="val-title" style="margin-top:12px;">Executors ('+h.executors.length+')</div>';
    h.executors.forEach(function(e){html+='<div class="val-item info">◎ '+e.label+' — '+e.runMode+'</div>';});
  }
  if(!h.errors.length&&!h.warns.length) html+='<div class="val-item ok" style="margin-top:8px;">✓ Project healthy (client-side)</div>';
  panel.innerHTML=html;
  // Bridge validation — disk-level check (executor paths, AGENT.md files on disk)
  if(RockoCore.isBridgeOnline()){
    bridgeGet('/validate').then(function(bv){
      if(!bv) return;
      let bridgeHtml='<div class="val-title" style="margin-top:14px;">Bridge Disk Validation</div>';
      (bv.errors||[]).forEach(function(e){bridgeHtml+='<div class="val-item err">✕ '+e+'</div>';});
      (bv.warns||[]).forEach(function(w){bridgeHtml+='<div class="val-item warn">⚠ '+w+'</div>';});
      if(!(bv.errors||[]).length&&!(bv.warns||[]).length) bridgeHtml+='<div class="val-item ok">✓ All disk paths verified</div>';
      // Agent files on disk
      const ac=bv.checks?.agents||{};
      if(Object.keys(ac).length){
        bridgeHtml+='<div class="val-title" style="margin-top:10px;">AGENT.md on disk</div>';
        Object.entries(ac).forEach(function(pair){
          const id=pair[0],info=pair[1];
          bridgeHtml+='<div class="val-item '+(info.ok?'ok':'warn')+'">'+(info.ok?'✓':'⚠')+' '+id+': '+(info.ok?'found':'not found — '+info.path)+'</div>';
        });
      }
      panel.innerHTML+=bridgeHtml;
    });
  } else {
    panel.innerHTML+='<div class="val-item warn" style="margin-top:10px;">⚠ Bridge offline — disk-level validation skipped</div>';
  }
  RockoCore.log(h.errors.length?'error':'success','Validation: '+h.errors.length+' errors, '+h.warns.length+' warnings');
}


function resetToDefault(){if(!confirm('Reset to bundled config? All edits lost.'))return;RockoCore.clearState();const b=BUNDLED_PROJECTS[APP_CONFIG.default_project];if(b){try{RockoCore.loadProject(JSON.parse(b));}catch{}}refreshAll();RockoCore.log('warn','Reset to bundled defaults');}

// ─────────────────────────────────────────────────────────
// PROJECT SWITCHER + QUICK ADD
// ─────────────────────────────────────────────────────────
function renderProjectDropdown(){
  const active=RockoCore.getActiveProject();
  const albl=document.getElementById('activeProjectLabel'); if(albl) albl.textContent=active||'No Project';
  const pdItems=document.getElementById('projectDropdownItems');
  if(!pdItems) return;
  pdItems.innerHTML='';
  RockoCore.getProjects().forEach(function(p){
    const d=document.createElement('div');
    d.className='proj-opt'+(p===active?' active':'');
    d.innerHTML='<div class="project-dot" style="background:'+(p===active?'var(--green)':'var(--text-dim)')+'"></div>'+p;
    d.addEventListener('click',function(e){switchProject(p,e);});
    pdItems.appendChild(d);
  });
}
function toggleProjectDropdown(){document.getElementById('projectDropdown')?.classList.toggle('open');}
function switchProject(name,e){e.stopPropagation();RockoCore.setActiveProject(name);document.getElementById('projectDropdown')?.classList.remove('open');refreshAll();RockoCore.log('info','Switched to: '+name);}
document.addEventListener('click',function(e){ var _ps=document.getElementById('projectSwitcher'); if(_ps&&!_ps.contains(e.target)){document.getElementById('projectDropdown')?.classList.remove('open');} });
function openImportModal(e){if(e)e.stopPropagation();document.getElementById('projectDropdown')?.classList.remove('open');openModal('importProjectModal');}
function importProjectFromFile(){
  const file=document.getElementById('projectFileInput').files[0];if(!file){alert('Select a file');return;}
  const reader=new FileReader();
  reader.onload=e=>{try{const m=JSON.parse(e.target.result);const r=RockoCore.loadProject(m);if(r.success){RockoCore.setActiveProject(m.project.name);closeModal('importProjectModal');refreshAll();}}catch(err){RockoCore.log('error','Import: '+err.message);alert('Invalid JSON: '+err.message);}};
  reader.readAsText(file);
}
function newBlankProject(e){e.stopPropagation();document.getElementById('projectDropdown')?.classList.remove('open');const name=prompt('Project name:');if(!name?.trim())return;const clean=name.trim().replace(/\s+/g,'_');const m=RockoCore.generateProjectManifest({name:clean,rootPath:'C:\\Projects\\'+clean});const r=RockoCore.loadProject(m);if(r.success){RockoCore.setActiveProject(clean);refreshAll();}}
function openQuickAdd(e){if(e)e.stopPropagation();document.getElementById('projectDropdown')?.classList.remove('open');qaPage=0;qaAgents=[{id:'ceo',name:'CEO',role:'ceo',type:'prompt',description:'Orchestrator.'}];qaRenderPage();qaRenderAgentList();openModal('quickAddModal');}
function qaRenderPage(){document.querySelectorAll('.qa-page').forEach((p,i)=>p.classList.toggle('active',i===qaPage));document.querySelectorAll('.qa-step').forEach((s,i)=>{s.classList.toggle('active',i===qaPage);s.classList.toggle('done',i<qaPage);});const back=document.getElementById('qa-back'),next=document.getElementById('qa-next');back.style.display=qaPage>0?'':'none';next.textContent=qaPage===3?'✓ Import':'Next →';if(qaPage===2)qaGeneratePreview();if(qaPage===3)qaRunValidation();}
function qaNav(dir){if(dir===1&&qaPage===0&&(!document.getElementById('qa-name').value.trim()||!document.getElementById('qa-root').value.trim())){alert('Name and root path required');return;}if(dir===1&&qaPage===3){qaImport();return;}qaPage=Math.max(0,Math.min(3,qaPage+dir));qaRenderPage();}
function qaRenderAgentList(){document.getElementById('qa-agents-list').innerHTML=qaAgents.map((a,i)=>'<div style="display:flex;align-items:center;gap:8px;background:var(--bg-card);border:1px solid var(--border);padding:10px 12px;"><span style="font-family:var(--mono);font-size:11px;color:var(--accent);min-width:60px">'+a.role.toUpperCase()+'</span><span style="flex:1">'+a.name+'</span>'+(i>0?'<button onclick="qaRemoveAgent('+i+')" style="background:none;border:none;color:var(--red);cursor:pointer;">✕</button>':'')+'</div>').join('');}
function qaAddAgent(){const name=prompt('Agent name:');if(!name)return;const role=prompt('Role (analyst/engine/ceo/custom):','analyst')||'analyst';qaAgents.push({id:name.toLowerCase().replace(/[^a-z0-9]/g,'_'),name,role,type:'prompt',description:''});qaRenderAgentList();}
function qaRemoveAgent(i){qaAgents.splice(i,1);qaRenderAgentList();}
function qaGeneratePreview(){const name=document.getElementById('qa-name').value.trim(),root=document.getElementById('qa-root').value.trim(),desc=document.getElementById('qa-desc').value,model=document.getElementById('qa-model').value;const defs=qaAgents.map(a=>({id:a.id,name:a.name,display_name:a.name,role:a.role,type:'prompt',instruction_file:'agents\\'+a.id+'\\AGENT.md',model_provider:'__company_default__',model_override:null,pipeline_step:a.id+'_step',enabled:true,project_tools:['filesystem','http'],apis:[],local_code:null,description:a.description,requires_approval:a.role==='ceo'}));const manifest=RockoCore.generateProjectManifest({name,rootPath:root,description:desc,agents:defs,extras:{model:{default_provider:'anthropic',default_model:model,fallback_model:'claude-haiku-4-5-20251001',providers:{anthropic:{type:'anthropic',api_base:'https://api.anthropic.com/v1',api_key_env:'ANTHROPIC_API_KEY',temperature:.3,max_tokens:2000}}}}});document.getElementById('qa-preview-text').value=JSON.stringify(manifest,null,2);}
function qaRunValidation(){let m;try{m=JSON.parse(document.getElementById('qa-preview-text').value);}catch(e){document.getElementById('qa-validation-result').innerHTML='<div class="val-item err">✕ Invalid JSON: '+e.message+'</div>';return;}const v=RockoCore.validateManifest(m);const panel=document.getElementById('qa-validation-result');if(!panel)return;panel.innerHTML='<div class="val-title">Validation — '+(m?.project?.name||'?')+'</div>'+v.errors.map(e=>'<div class="val-item err">✕ '+e+'</div>').join('')+v.warns.map(w=>'<div class="val-item warn">⚠ '+w+'</div>').join('')+(v.valid?'<div class="val-item ok">✓ Ready to import</div>':'');}
function qaImport(){let m;try{m=JSON.parse(document.getElementById('qa-preview-text').value);}catch(e){alert('Invalid JSON: '+e.message);return;}const v=RockoCore.validateManifest(m);if(!v.valid){alert('Fix errors:\n'+v.errors.join('\n'));return;}const r=RockoCore.loadProject(m);if(r.success){RockoCore.setActiveProject(m.project.name);closeModal('quickAddModal');refreshAll();RockoCore.log('success','Quick Add: "'+m.project.name+'" imported');}}

// ─────────────────────────────────────────────────────────
// MODAL + LOG
// ─────────────────────────────────────────────────────────
function openModal(id){document.getElementById(id).classList.add('open');}
function closeModal(id){document.getElementById(id).classList.remove('open');}
function appendLog(entry){const s=document.getElementById('logScroll');if(!s)return;const l=document.createElement('div');l.className='log-line';l.innerHTML='<span class="log-ts">'+entry.ts+'</span><span class="log-msg '+entry.type+'">'+entry.msg+'</span>';s.appendChild(l);s.scrollTop=s.scrollHeight;while(s.children.length>(APP_CONFIG.max_log_lines||150))s.removeChild(s.firstChild);}
function clearLog(){document.getElementById('logScroll').innerHTML='';RockoCore.log('system','Log cleared');}

// ─────────────────────────────────────────────────────────
// BOOT
// ─────────────────────────────────────────────────────────
// PWA shortcut routing
(function(){
  const p = new URLSearchParams(window.location.search);
  const view = p.get('view');
  if (view) setTimeout(()=>setTab(view, null), 300);
})();

// ═══════════════════════════════════════════════════════════════
// AUTOMATION & ORCHESTRATION BRIDGE LAYER
// Bridge is source of truth. localStorage is UI cache only.
// ═══════════════════════════════════════════════════════════════

const BRIDGE = APP_CONFIG.bridge_url || 'http://127.0.0.1:8787';
let _pollTimers = [];
let _pendingApprovals = [];

async function bridgeGet(path) {
  try {
    const r = await fetch(BRIDGE + path, {signal: AbortSignal.timeout(5000)});
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}
async function bridgePost(path, body={}) {
  try {
    const r = await fetch(BRIDGE + path, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body), signal: AbortSignal.timeout(8000)
    });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}
async function bridgePatch(path, body={}) {
  try {
    const r = await fetch(BRIDGE + path, {
      method:'PATCH', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(body), signal: AbortSignal.timeout(5000)
    });
    return r.ok ? await r.json() : null;
  } catch { return null; }
}
async function bridgeDelete(path) {
  try {
    const r = await fetch(BRIDGE + path, {method:'DELETE', signal: AbortSignal.timeout(5000)});
    return r.ok;
  } catch { return false; }
}



// ═══════════════════════════════════════════════════════════════
// TASK WORKER
// ═══════════════════════════════════════════════════════════════
async function loadWorkerStatus() {
  const d = await bridgeGet('/tasks/worker/status');
  if (!d) return;
  const running = d.running && !d.paused;
  const paused  = d.paused;
  const badge = document.getElementById('workerStatusBadge');
  if (badge) {
    badge.textContent = running ? '● RUNNING' : paused ? '⏸ PAUSED' : '○ STOPPED';
    badge.style.color = running ? 'var(--green)' : paused ? 'var(--yellow)' : 'var(--text-dim)';
  }
  const set = (id, val, cls='') => {
    const el = document.getElementById(id); if (!el) return;
    el.textContent = val; if (cls) el.className = 'worker-stat-val ' + cls;
  };
  set('wStatStatus', running ? 'Running' : paused ? 'Paused' : 'Stopped',
      running ? 'green' : paused ? 'yellow' : '');
  set('wStatQueued',  d.queued_count || 0);
  set('wStatRunning', d.running_count || 0);
  set('wStatDone',    d.completed_count || 0, 'green');
  set('wStatFailed',  d.failed_count || 0, d.failed_count ? 'red' : '');
  set('wStatCurrent', d.current_task_id || 'none');
}

async function workerCtrl(action) {
  const res = await bridgePost('/tasks/worker/' + action);
  if (res) { RockoCore.log('info', 'Worker ' + action + ': ' + res.status); loadWorkerStatus(); }
  else RockoCore.log('error', 'Worker control failed — is bridge running?');
}

// ═══════════════════════════════════════════════════════════════
// SCHEDULES
// ═══════════════════════════════════════════════════════════════
let _schedules = [];

async function loadSchedules() {
  const d = await bridgeGet('/schedules');
  if (!d) return;
  _schedules = d.schedules || [];
  renderSchedules();
}

function renderSchedules() {
  const el = document.getElementById('scheduleList');
  const cnt = document.getElementById('schedulerCount');
  if (!el) return;
  if (cnt) cnt.textContent = _schedules.length + ' schedule' + (_schedules.length !== 1 ? 's' : '');
  if (!_schedules.length) {
    el.innerHTML = '<div class="empty-state">No schedules. Click + Schedule to add one.</div>';
    return;
  }
  el.innerHTML = '';
  _schedules.forEach(function(s) {
    const div = document.createElement('div');
    div.className = 'sched-item';
    const enabled = s.enabled !== false;
    const lastStatus = s.last_status || 'never';
    const nextRun = s.next_run_at ? new Date(s.next_run_at).toLocaleTimeString() : '--';
    const lastRun = s.last_run_at ? new Date(s.last_run_at).toLocaleTimeString() : 'never';
    const schDesc = s.schedule_type === 'interval'
      ? 'every ' + (s.interval_seconds >= 3600 ? Math.round(s.interval_seconds/3600) + 'h' : Math.round(s.interval_seconds/60) + 'min')
      : s.cron || '--';
    div.innerHTML =
      '<div class="sched-info">' +
        '<div class="sched-name">' + s.name + '</div>' +
        '<div class="sched-meta">' +
          '<span>' + s.type.toUpperCase() + ': ' + s.target_id + '</span>' +
          '<span>' + schDesc + '</span>' +
          '<span>next: ' + nextRun + '</span>' +
          '<span>last: ' + lastRun + '</span>' +
        '</div>' +
      '</div>' +
      '<span class="sched-status ' + (lastStatus === 'error' ? 'error' : enabled ? 'enabled' : 'disabled') + '">' +
        (lastStatus === 'error' ? 'ERROR' : enabled ? 'ON' : 'OFF') + '</span>';
    const acts = document.createElement('div');
    acts.className = 'sched-actions';
    const runBtn = document.createElement('button');
    runBtn.className = 'icon-btn success'; runBtn.title = 'Run now'; runBtn.textContent = '▶';
    runBtn.addEventListener('click', function() { schedRunNow(s.id); });
    acts.appendChild(runBtn);
    const toggleBtn = document.createElement('button');
    toggleBtn.className = 'icon-btn'; toggleBtn.title = enabled ? 'Pause' : 'Resume';
    toggleBtn.textContent = enabled ? '⏸' : '▶';
    toggleBtn.addEventListener('click', function() { schedToggle(s.id, enabled); });
    acts.appendChild(toggleBtn);
    const delBtn = document.createElement('button');
    delBtn.className = 'icon-btn danger'; delBtn.title = 'Delete'; delBtn.textContent = '✕';
    delBtn.addEventListener('click', function() { schedDelete(s.id, s.name); });
    acts.appendChild(delBtn);
    div.appendChild(acts);
    el.appendChild(div);
  });
}

async function schedRunNow(id) {
  const r = await bridgePost('/schedules/' + id + '/run-now');
  if (r) { RockoCore.log('success', 'Schedule fired: ' + id); setTimeout(loadSchedules, 1000); }
}
async function schedToggle(id, currently_enabled) {
  const ep = currently_enabled ? '/schedules/' + id + '/pause' : '/schedules/' + id + '/resume';
  const r = await bridgePost(ep);
  if (r) { RockoCore.log('info', 'Schedule ' + (currently_enabled ? 'paused' : 'resumed')); loadSchedules(); }
}
async function schedDelete(id, name) {
  if (!confirm('Delete schedule "' + name + '"?')) return;
  const ok = await bridgeDelete('/schedules/' + id);
  if (ok) { RockoCore.log('warn', 'Schedule deleted: ' + name); loadSchedules(); }
}

function openCreateScheduleModal() { openModal('createScheduleModal'); }
function toggleSchedMode(mode) {
  document.getElementById('schedIntervalGroup').style.display = mode === 'interval' ? '' : 'none';
  document.getElementById('schedCronGroup').style.display = mode === 'cron' ? '' : 'none';
}
async function submitCreateSchedule() {
  const name   = document.getElementById('schedName').value.trim();
  const type   = document.getElementById('schedType').value;
  const target = document.getElementById('schedTarget').value.trim();
  const mode   = document.getElementById('schedMode').value;
  if (!name || !target) { alert('Name and Target ID are required'); return; }
  let inputData = {};
  const inp = document.getElementById('schedInput').value.trim();
  if (inp) { try { inputData = JSON.parse(inp); } catch(e) { alert('Invalid JSON in input: ' + e.message); return; } }
  const body = {
    name, type, target_id: target, schedule_type: mode,
    input: inputData, enabled: true
  };
  if (mode === 'interval') body.interval_seconds = parseInt(document.getElementById('schedInterval').value) || 1800;
  if (mode === 'cron') body.cron = document.getElementById('schedCron').value.trim();
  const r = await bridgePost('/schedules', body);
  if (r) {
    RockoCore.log('success', 'Schedule created: ' + name);
    closeModal('createScheduleModal');
    document.getElementById('schedName').value = '';
    document.getElementById('schedTarget').value = '';
    loadSchedules();
  } else RockoCore.log('error', 'Failed to create schedule — check bridge');
}

// ═══════════════════════════════════════════════════════════════
// CEO ORCHESTRATION
// ═══════════════════════════════════════════════════════════════
let _decisions = [];

async function loadOrchestrationDecisions() {
  const d = await bridgeGet('/orchestrate/decisions');
  if (!d) return;
  _decisions = d.decisions || [];
  renderDecisions();
}

async function loadOrchestrationStatus() {
  const d = await bridgeGet('/orchestrate/status');
  if (!d) return;
  const el = document.getElementById('orchestrationStatus');
  if (!el) return;
  const latest = d.latest_decision;
  if (!latest) { el.innerHTML = '<div class="empty-state">No active orchestration.</div>'; return; }
  const cls = {'approve':'approve','reject':'reject','hold':'hold','rerun':'rerun'}[latest.decision] || 'other';
  el.innerHTML =
    '<div class="decision-badge decision-' + cls + '">' + (latest.decision || '—').toUpperCase() + '</div>' +
    '<div class="decision-reason">' + (latest.reason || '') + '</div>' +
    '<div class="decision-meta">' +
      '<span>' + (latest.timestamp ? new Date(latest.timestamp).toLocaleTimeString() : '') + '</span>' +
      '<span>' + (latest.duration_ms || 0) + 'ms</span>' +
      (latest.fallback_used ? '<span style="color:var(--yellow)">fallback used</span>' : '') +
    '</div>';
}

function renderDecisions() {
  const el = document.getElementById('decisionsList');
  const cnt = document.getElementById('decisionsCount');
  if (!el) return;
  if (cnt) cnt.textContent = _decisions.length + ' decision' + (_decisions.length !== 1 ? 's' : '');
  if (!_decisions.length) { el.innerHTML = '<div class="empty-state">No CEO decisions yet.</div>'; return; }
  el.innerHTML = '';
  _decisions.slice(0, 10).forEach(function(d) {
    const div = document.createElement('div');
    div.className = 'decision-card';
    const cls = {'approve':'approve','reject':'reject','hold':'hold','rerun':'rerun'}[d.decision] || 'other';
    div.innerHTML =
      '<div class="decision-badge decision-' + cls + '">' + (d.decision || '—').toUpperCase() + '</div>' +
      '<div class="decision-reason">' + (d.reason || d.error || '') + '</div>' +
      '<div class="decision-meta">' +
        '<span>' + (d.timestamp ? new Date(d.timestamp).toLocaleTimeString() : '') + '</span>' +
        '<span>' + (d.duration_ms || 0) + 'ms</span>' +
        (d.created_tasks && d.created_tasks.length ? '<span style="color:var(--accent)">' + d.created_tasks.length + ' task(s) created</span>' : '') +
      '</div>';
    el.appendChild(div);
  });
}

function triggerOrchestrationNow() { openModal('orchestrateModal'); }

async function submitOrchestrate() {
  let ctx = {};
  const raw = document.getElementById('orchestrateCtx').value.trim();
  if (raw) { try { ctx = JSON.parse(raw); } catch(e) { alert('Invalid JSON: ' + e.message); return; } }
  else {
    const runCtx = RockoCore.getRunContext();
    if (runCtx) ctx = runCtx;
  }
  RockoCore.log('system', 'CEO orchestration call...');
  const r = await bridgePost('/orchestrate', {pipeline_context: ctx});
  if (r) {
    RockoCore.log('success', 'CEO decision: ' + (r.decision || 'unknown').toUpperCase() + ' — ' + (r.reason || ''));
    closeModal('orchestrateModal');
    loadOrchestrationDecisions();
    loadOrchestrationStatus();
  } else RockoCore.log('error', 'Orchestration failed — check bridge + CEO agent');
}

// ═══════════════════════════════════════════════════════════════
// APPROVAL QUEUE (bridge-side pending approvals)
// ═══════════════════════════════════════════════════════════════
async function loadApprovals() {
  const el = document.getElementById('approvalsList');
  const cnt = document.getElementById('approvalsCount');
  if (!el) return;
  // Local pending approval (from platform.js approval gate)
  const pending = RockoCore.getPendingApproval();
  if (pending) {
    if (cnt) cnt.textContent = '1';
    el.innerHTML = '';
    const div = document.createElement('div');
    div.className = 'approval-item';
    div.innerHTML =
      '<div class="approval-step">⏸ ' + (pending.step?.label || 'Approval Required') + '</div>' +
      '<div class="approval-ts">' + new Date(pending.timestamp).toLocaleString() + '</div>' +
      '<div class="approval-ctx">' + JSON.stringify(pending.context, null, 2).slice(0, 800) + '</div>';
    const acts = document.createElement('div');
    acts.className = 'approval-acts';
    const appBtn = document.createElement('button');
    appBtn.className = 'btn-approve'; appBtn.textContent = '✓ Approve';
    appBtn.addEventListener('click', function() { doApprove(); loadApprovals(); });
    const rejBtn = document.createElement('button');
    rejBtn.className = 'btn-reject'; rejBtn.textContent = '✕ Reject';
    rejBtn.addEventListener('click', function() { doReject(); loadApprovals(); });
    acts.appendChild(rejBtn); acts.appendChild(appBtn);
    div.appendChild(acts);
    el.appendChild(div);
  } else {
    if (cnt) cnt.textContent = '0';
    el.innerHTML = '<div class="empty-state">No pending approvals.</div>';
  }
}

// ═══════════════════════════════════════════════════════════════
// MODEL STATUS
// ═══════════════════════════════════════════════════════════════
async function loadModelStatus() {
  var el = document.getElementById('modelProviderStatus');
  if (!el) return;
  var d = await bridgeGet('/models/providers');
  if (!d) {
    el.innerHTML = '<div style="font-family:var(--mono);font-size:11px;color:var(--text-dim)">Bridge offline — provider status unavailable</div>';
    return;
  }
  var cnt = document.getElementById('providerCount');
  if (cnt) cnt.textContent = Object.keys(d).length + ' provider(s)';
  el.innerHTML = '';
  var iconMap = {anthropic:'A', openai:'O', nvidia:'N', local:'L'};
  var clsMap  = {anthropic:'anthropic', openai:'openai', nvidia:'nvidia', local:'local'};
  Object.entries(d).forEach(function(pair) {
    var provId = pair[0], cfg = pair[1];
    var isLocal = cfg.type === 'local' || !cfg.key_required;
    var keyOk   = isLocal || cfg.key_present;
    var card    = document.createElement('div');
    card.className = 'provider-card' + (cfg.configured_in_project ? ' active-provider' : '');
    var models = cfg.available_models ? cfg.available_models.slice(0,4).join(', ') + (cfg.available_models.length > 4 ? '...' : '') : '';
    var noteHtml = cfg.note ? '<div class="provider-note">&#8505; ' + cfg.note + '</div>' : '';
    var userBadge = cfg.user_owned ? '<span style="font-family:var(--mono);font-size:9px;padding:1px 6px;background:rgba(118,185,0,.1);border:1px solid rgba(118,185,0,.3);color:#76b900;margin-left:6px;">USER-OWNED KEY</span>' : '';
    card.innerHTML =
      '<div class="provider-header">' +
        '<div class="provider-icon ' + (clsMap[provId]||'custom') + '">' + (iconMap[provId]||provId.charAt(0).toUpperCase()) + '</div>' +
        '<div style="flex:1;">' +
          '<div class="provider-name">' + (cfg.display_name || provId.toUpperCase()) + userBadge + '</div>' +
          '<div class="provider-base-url">' + (cfg.api_base || '') + '</div>' +
        '</div>' +
      '</div>' +
      '<div class="provider-key-status">' +
        '<div class="key-dot ' + (keyOk ? 'present' : 'missing') + '"></div>' +
        (isLocal
          ? '<span style="color:var(--green)">No API key required</span>'
          : keyOk
            ? '<span style="color:var(--green)">API key detected</span> <span style="color:var(--text-dim);font-size:10px;">' + (cfg.env_var||'') + '</span>'
            : '<span style="color:var(--red)">Key missing</span> <span style="color:var(--text-dim);font-size:10px;">Set ' + (cfg.env_var||'?') + ' in .env</span>') +
      '</div>' +
      (models ? '<div class="provider-models">Models: ' + models + '</div>' : '') +
      noteHtml +
      '<div class="provider-actions">' +
        (keyOk ? '<button class="btn btn-ghost" style="font-size:10px;padding:4px 10px;" onclick="testProvider(\'' + provId + '\')">Test Connection</button>' : '') +
      '</div>';
    el.appendChild(card);
  });
  // Also update agent model list
  var agEl = document.getElementById('agentModelList');
  if (agEl) {
    var proj = RockoCore.getActiveProject();
    var agents = RockoCore.getAgents(proj);
    var manifest = RockoCore.getProject(proj);
    var defaultModel = manifest && manifest.model ? (manifest.model.default_model || '--') : '--';
    agEl.innerHTML = '';
    if (!agents.length) { agEl.innerHTML = '<div class="empty-state">No agents loaded.</div>'; return; }
    agents.forEach(function(a) {
      var row = document.createElement('div');
      row.className = 'model-row';
      var override = a.model_override;
      var provLabel = '';
      if (override) {
        Object.keys(d).forEach(function(pid) {
          var prov = d[pid];
          if (prov.available_models && prov.available_models.includes(override)) {
            provLabel = '<span style="color:' + (pid==='nvidia'?'#76b900':'var(--accent)') + ';font-size:9px;margin-left:4px;">' + pid.toUpperCase() + '</span>';
          }
        });
      }
      row.innerHTML =
        '<span class="model-key">' + a.name + '</span>' +
        '<span class="model-val" style="font-family:var(--mono);font-size:10px;">' +
          (override
            ? '<span style="color:var(--yellow)">' + override + '</span>' + provLabel
            : '<span style="color:var(--text-dim)">' + defaultModel + '</span>') +
        '</span>';
      agEl.appendChild(row);
    });
  }
}

async function runSystemTest() {
  showLoading('Running system verification...');
  try {
    const r = await fetch(BRIDGE + '/system/test', {signal: AbortSignal.timeout(30000)});
    const d = r.ok ? await r.json() : null;
    hideLoading();
    if (!d) { toastErr('System test failed — bridge unreachable'); return; }
    const panel = document.getElementById('systemTestResult');
    if (!panel) return;
    const overall = d.overall || 'unknown';
    const overallCol = overall === 'pass' ? 'var(--green)' : overall === 'warn' ? 'var(--yellow)' : 'var(--red)';
    let html = '<div class="val-title">System Test — <span style="color:' + overallCol + '">' + overall.toUpperCase() + '</span></div>';
    ['task_worker','scheduler','pipeline','orchestration','approval_gate','recovery'].forEach(function(key) {
      const item = d[key]; if (!item) return;
      const col = item.status === 'pass' ? 'ok' : item.status === 'fail' ? 'err' : 'warn';
      const sym = item.status === 'pass' ? '✓' : item.status === 'fail' ? '✕' : '⚠';
      html += '<div class="val-item ' + col + '">' + sym + ' ' + key.replace('_',' ').toUpperCase() + ': ' + (item.detail || item.status) + '</div>';
    });
    panel.innerHTML = html;
    if (overall === 'pass') toastOk('All systems verified');
    else if (overall === 'warn') toastWarn('System test passed with warnings');
    else toastErr('System test: one or more failures');
  } catch(e) {
    hideLoading();
    toastErr('System test error: ' + e.message);
  }
}

// ── Sidebar worker badge (polls bridge worker status) ─────────────────────────
function updateSidebarWorkerBadge() {
  if (!RockoCore.isBridgeOnline()) return;
  bridgeGet('/tasks/worker/status').then(function(d) {
    if (!d) return;
    const badge = document.getElementById('sidebarTaskBadge');
    const total = (d.queued_count || 0) + (d.running_count || 0);
    if (badge) {
      badge.textContent = total;
      badge.style.display = total > 0 ? '' : 'none';
      badge.style.background = d.running_count > 0 ? 'var(--green)' : 'var(--yellow)';
    }
  });
}
setInterval(updateSidebarWorkerBadge, 6000);




function openLogModal() {
  openModal('logModal');
  setTimeout(function() {
    var s = document.getElementById('logScroll');
    if (s) s.scrollTop = s.scrollHeight;
  }, 50);
}


// ═══════════════════════════════════════════════════════════════
// RUNTIMES
// ═══════════════════════════════════════════════════════════════
var _runtimes = [];

async function loadRuntimes() {
  const d = await bridgeGet('/runtimes');
  if (!d) return;
  _runtimes = d.runtimes || [];
  renderRuntimes();
}

function renderRuntimes() {
  const el = document.getElementById('runtimesList');
  if (!el) return;
  if (!_runtimes.length) {
    el.innerHTML = '<div class="empty-state">No runtimes defined.<br>Add a "runtimes" section to your project.json to connect external systems.</div>';
    return;
  }
  el.innerHTML = '';
  _runtimes.forEach(function(rt) {
    const card = document.createElement('div');
    card.className = 'runtime-card';
    const riskCls = 'risk-' + (rt.risk_level || 'read_only');
    const typeCls = 'runtime-type-' + (rt.type || 'cli');
    const lastStatus = rt.last_status || 'never';
    const lastCol = lastStatus === 'success' ? 'var(--green)' : lastStatus === 'error' ? 'var(--red)' : 'var(--text-dim)';
    const approvalNote = rt.requires_approval ? '<span style="color:var(--yellow);font-family:var(--mono);font-size:9px;margin-left:8px;">⏸ APPROVAL REQUIRED</span>' : '';
    card.innerHTML =
      '<div class="runtime-card-header">' +
        '<span class="runtime-type-badge ' + typeCls + '">' + (rt.type || '?') + '</span>' +
        '<span class="runtime-name">' + rt.id + '</span>' +
        approvalNote +
        '<span class="runtime-risk ' + riskCls + '">' + (rt.risk_level || 'read_only').replace('_', ' ').toUpperCase() + '</span>' +
      '</div>' +
      (rt.description ? '<div style="font-size:12px;color:var(--text-secondary);margin-bottom:8px;">' + rt.description + '</div>' : '') +
      '<div class="runtime-meta">' +
        '<span>type: ' + (rt.type || '?') + '</span>' +
        (rt.allowed_agents && rt.allowed_agents.length ? '<span>agents: ' + rt.allowed_agents.join(', ') + '</span>' : '<span style="color:var(--text-dim)">all agents permitted</span>') +
        (rt.last_run_at ? '<span>last: ' + new Date(rt.last_run_at).toLocaleTimeString() + '</span>' : '') +
      '</div>' +
      '<div class="runtime-last" style="color:' + lastCol + '">Status: ' + lastStatus.toUpperCase() + '</div>' +
      (rt.last_error ? '<div class="runtime-error">Last error: ' + rt.last_error.slice(0, 150) + '</div>' : '');
    const acts = document.createElement('div');
    acts.style.cssText = 'display:flex;gap:6px;';
    const testBtn = document.createElement('button');
    testBtn.className = 'btn btn-ghost';
    testBtn.style.cssText = 'padding:5px 12px;font-size:10px;';
    testBtn.textContent = '⚡ Test (dry run)';
    testBtn.addEventListener('click', function() { runtimeTest(rt.id); });
    acts.appendChild(testBtn);
    if (!rt.requires_approval) {
      const runBtn = document.createElement('button');
      runBtn.className = 'btn btn-warn';
      runBtn.style.cssText = 'padding:5px 12px;font-size:10px;';
      runBtn.textContent = '▶ Run';
      runBtn.addEventListener('click', function() { runtimeRun(rt.id); });
      acts.appendChild(runBtn);
    } else {
      const gateNote = document.createElement('span');
      gateNote.style.cssText = 'font-family:var(--mono);font-size:10px;color:var(--yellow);padding:5px 8px;';
      gateNote.textContent = '⏸ Requires approval before run';
      acts.appendChild(gateNote);
    }
    card.appendChild(acts);
    el.appendChild(card);
  });
}

async function runtimeTest(id) {
  showLoading('Testing runtime: ' + id + '...');
  const r = await bridgePost('/runtimes/' + id + '/test', {});
  hideLoading();
  if (r) {
    if (r.ok) toastOk('Runtime test passed: ' + id);
    else toastWarn('Runtime test: ' + (r.error || 'check bridge logs'));
  } else toastErr('Test failed — bridge unreachable');
  loadRuntimes();
}

async function runtimeRun(id) {
  if (!confirm('Run runtime "' + id + '"? This will execute the external system.')) return;
  showLoading('Running runtime: ' + id + '...');
  const proj = RockoCore.getActiveProject();
  const runCtx = RockoCore.getRunContext();
  const r = await bridgePost('/runtimes/' + id + '/run', {
    context: runCtx ? runCtx.current_context : {},
    agent_id: null
  });
  hideLoading();
  if (!r) { toastErr('Run failed — bridge unreachable'); return; }
  if (r.requires_approval) { toastWarn('Runtime requires human approval — use approval gate'); return; }
  if (r.permission_denied) { toastErr('Permission denied: ' + r.message); return; }
  if (r.ok) toastOk('Runtime completed: ' + id + ' (' + (r.duration_ms || 0) + 'ms)');
  else toastErr('Runtime error: ' + (r.error || 'unknown'));
  loadRuntimes();
}

// Wire setTab for runtimes
var _setTabForRuntimes = setTab;
setTab = function(view, btn) {
  _setTabForRuntimes(view, btn);
  if (view === 'runtimes') loadRuntimes();
};

// Poll runtimes every 30s when on the tab
setInterval(function() {
  if (document.getElementById('view-runtimes')?.classList.contains('active')) loadRuntimes();
}, 30000);


// ═══════════════════════════════════════════════════════════════
// RUNTIMES
// ═══════════════════════════════════════════════════════════════
var _runtimes = [];





async function testRuntime(id) {
  showLoading('Testing runtime: ' + id + '...');
  var r = await bridgePost('/runtimes/' + id + '/test');
  hideLoading();
  if (r && r.ok) toastOk('Runtime test OK: ' + id + ' (' + r.duration_ms + 'ms)');
  else toastErr('Runtime test failed: ' + (r && r.error ? r.error : 'bridge offline'));
  loadRuntimes();
}

async function runRuntime(id) {
  if (!confirm('Run runtime "' + id + '"? This will execute the external system.')) return;
  showLoading('Running runtime: ' + id + '...');
  var r = await bridgePost('/runtimes/' + id + '/run', {context: {}, input: {}});
  hideLoading();
  if (r && r.ok) toastOk('Runtime completed: ' + id);
  else {
    if (r && r.requires_approval) toastWarn(id + ' requires human approval — approve in Orchestration tab');
    else toastErr('Runtime failed: ' + (r && r.error ? r.error.slice(0, 80) : 'unknown'));
  }
  loadRuntimes();
}

function refreshRuntimes() { loadRuntimes(); }

// Runtimes tab wired via _setTabForRuntimes override above

// Poll runtimes every 30s if tab active
setInterval(function() {
  if (document.getElementById('view-runtimes') && document.getElementById('view-runtimes').classList.contains('active')) {
    loadRuntimes();
  }
}, 30000);


// ═══════════════════════════════════════════════════════════════
// COMPANY LAYER
// Company = user-facing workspace. Project = technical config.
// ═══════════════════════════════════════════════════════════════
var COMPANIES_KEY = 'rockoagents_companies_v1';
var DELETED_COMPANIES_KEY = 'rockoagents_deleted_companies_v1';
var _currentCompanyLogo = null;
var _companyModalLogo   = null;

function getCompanies() {
