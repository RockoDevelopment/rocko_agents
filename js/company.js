/* COMPANY — companies, onboarding, projects, templates */

var COMPANIES_KEY = 'rockoagents_companies_v1';
var DELETED_COMPANIES_KEY = 'rockoagents_deleted_companies_v1';
var _currentCompanyLogo = null;
var _companyModalLogo   = null;

function getCompanies() {
  try { return JSON.parse(localStorage.getItem(COMPANIES_KEY) || '[]'); }
  catch { return []; }
}
function saveCompanies(list) {
  localStorage.setItem(COMPANIES_KEY, JSON.stringify(list));
  bridgePost('/data/save', {key: 'companies', data: list});
}
function getDeletedCompanyIds() {
  try {
    var raw = localStorage.getItem(DELETED_COMPANIES_KEY) || '[]';
    var arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch(e) { return []; }
}
function saveDeletedCompanyIds(ids) {
  var seen = {};
  var clean = (ids || []).filter(function(id){
    id = String(id || '').trim();
    if (!id || seen[id]) return false;
    seen[id] = true;
    return true;
  });
  localStorage.setItem(DELETED_COMPANIES_KEY, JSON.stringify(clean));
  try { bridgePost('/data/save', {key:'deleted_companies', data:clean}); } catch(e) {}
}
function markCompanyDeleted(id) {
  if (!id) return;
  var ids = getDeletedCompanyIds();
  if (ids.indexOf(id) === -1) ids.push(id);
  saveDeletedCompanyIds(ids);
}
function filterDeletedCompanies(list) {
  var deleted = getDeletedCompanyIds();
  if (!deleted.length) return list || [];
  return (list || []).filter(function(c){ return c && deleted.indexOf(c.id) === -1; });
}
function getCompanyProjectName(co) {
  if (!co) return null;
  return (co._manifest && co._manifest.project && co._manifest.project.name) ||
         (co.display_name || co.id || '').replace(/\s+/g, '_');
}
function getActiveCompanyProjectName() {
  return getCompanyProjectName(getActiveCompany()) || RockoCore.getActiveProject();
}
async function scrubDeletedCompanyEverywhere(id, projectName) {
  if (!id) return;
  try { await bridgeDelete('/companies/' + encodeURIComponent(id)); } catch(e) {}
  try { await bridgePost('/companies/delete', {id:id, project_name:projectName || ''}); } catch(e) {}
  try { await bridgePost('/data/delete_company', {id:id, project_name:projectName || ''}); } catch(e) {}
  try { await bridgePost('/data/save', {key:'deleted_companies', data:getDeletedCompanyIds()}); } catch(e) {}
  try { await bridgePost('/data/save', {key:'companies', data:filterDeletedCompanies(getCompanies())}); } catch(e) {}
  if (_pgReady && _pgdb) {
    try { await _pgDeleteCompany(id); } catch(e) {}
  }
}
function getActiveCompany() {
  return getCompanies().find(function(c){ return c.active; }) || null;
}
function setActiveCompany(id) {
  var list = getCompanies();
  list.forEach(function(c){ c.active = c.id === id; });
  saveCompanies(list);
  var co = list.find(function(c){ return c.id === id; });
  _skillDelegationAgentId = null;
  _skillDelegationCompanyId = co ? (co.id || getCompanyProjectName(co)) : null;
  if (co) _activateCompany(co);
}
function _activateCompany(co) {
  if (!co) return;

  var manifest = co._manifest || null;
  if (!manifest && co.project_path) {
    manifest = RockoCore.generateProjectManifest({
      name: (co.display_name || co.id || 'Company').replace(/\s+/g, '_'),
      rootPath: co.project_path,
      description: co.description || ''
    });
    co._manifest = manifest;
    var list = getCompanies();
    list.forEach(function(c) { if (c.id === co.id) c._manifest = manifest; });
    saveCompanies(list);
  }

  if (manifest) {
    var loaded = RockoCore.loadProject(manifest);
    if (!loaded || loaded.success === false) {
      toastErr('Company project failed to load: ' + (co.display_name || co.id));
      return;
    }

    var activeProjName = manifest.project ? manifest.project.name : co.id;
    RockoCore.setActiveProject(activeProjName);
    _skillDelegationAgentId = null;
    _skillDelegationCompanyId = co.id || activeProjName;
    _skillDelegationProjectName = activeProjName;
    _skillDelegationAgentId = null;

    if (typeof ensureCompanyCEO === 'function') {
      ensureCompanyCEO(activeProjName, co.display_name || co.id, co.description || '');
    }

    syncCompanyAgentCache(activeProjName);
    RockoCore.saveState();
  }

  renderCompanyRail();
  updateTopbarCompany(co);
  hideOnboarding();
  // Reset agent editor — old agent belongs to previous company
  var _wasOnAgentEditor = (function(){
    var v = document.getElementById('view-agents');
    return v && v.classList.contains('active');
  })();
  if(typeof currentAgentId !== 'undefined') currentAgentId = null;
  refreshAll();
  if(_wasOnAgentEditor){
    document.querySelectorAll('.view').forEach(function(x){x.classList.remove('active');});
    var _d = document.getElementById('view-dashboard');
    if(_d) _d.classList.add('active');
    document.querySelectorAll('.nav-tab').forEach(function(t){
      t.classList.toggle('active', t.textContent.toLowerCase().trim()==='dashboard');
    });
  }
  setTimeout(function(){ refreshAll(); }, 250);
  toastOk('Switched to ' + co.display_name);
}
function updateTopbarCompany(co) {
  var panel  = document.getElementById('sidebarCompany');
  var nameEl = document.getElementById('topbarCompanyName');
  var descEl = document.getElementById('topbarCompanyDesc');
  var logoEl = document.getElementById('topbarCompanyLogo');
  if (!co) { if (panel) panel.style.display = 'none'; return; }
  if (panel) panel.style.display = 'flex';
  if (nameEl) nameEl.textContent = co.display_name || co.id;
  if (descEl) descEl.textContent = co.description || '';
  if (logoEl) {
    var initial = (co.display_name || co.id).charAt(0).toUpperCase();
    logoEl.textContent = co.logo ? '' : initial;
    logoEl.style.backgroundImage = co.logo ? 'url(' + co.logo + ')' : '';
    logoEl.style.backgroundSize = 'cover';
  }
}

function renderCompanyRail() {
  var el = document.getElementById('companySlots');
  if (!el) return;
  var companies = getCompanies();
  var active    = getActiveCompany();
  el.innerHTML = '';
  companies.forEach(function(co) {
    var slot = document.createElement('div');
    slot.className = 'company-slot' + (co.active ? ' active' : '');
    slot.title     = co.display_name || co.id;
    slot.addEventListener('click', function() { setActiveCompany(co.id); });
    if (co.logo) {
      slot.innerHTML = '<img src="' + co.logo + '" alt="" style="width:100%;height:100%;object-fit:cover;display:block;"><span class="company-slot-tip">' + (co.display_name||co.id) + '</span>';
    } else {
      var init = (co.display_name || co.id).charAt(0).toUpperCase();
      slot.innerHTML = init + '<span class="company-slot-tip">' + (co.display_name||co.id) + '</span>';
    }
    var editBtn = document.createElement('div');
    editBtn.className = 'company-slot-edit';
    editBtn.title = 'Edit company';
    editBtn.textContent = '✎';
    (function(cid){ editBtn.addEventListener('click', function(e){ e.stopPropagation(); openEditCompany(cid); }); })(co.id);
    slot.appendChild(editBtn);
    el.appendChild(slot);
  });
}

function isCEOAgent(a){
  if(!a)return false;
  return (
    a.role === 'ceo' ||
    a.id === 'ceo' ||
    a.id === 'ceo_agent' ||
    String(a.name || '').trim().toLowerCase() === 'ceo' ||
    String(a.display_name || '').trim().toLowerCase() === 'ceo'
  );
}

function ensureCompanyCEO(projectName, companyName, description){
  projectName = projectName || RockoCore.getActiveProject();
  if(!projectName)return null;

  var manifest = RockoCore.getProject(projectName);
  var liveAgents = RockoCore.getAgents(projectName) || [];
  var manifestAgents = manifest && Array.isArray(manifest.agents) ? manifest.agents : [];

  var ceos = [];

  liveAgents.forEach(function(a){
    if(isCEOAgent(a))ceos.push(a);
  });

  manifestAgents.forEach(function(a){
    if(isCEOAgent(a) && !ceos.some(function(x){return x.id === a.id;})){
      var built = RockoCore.getAgent(a.id);
      ceos.push(built || a);
    }
  });

  var keeper =
    ceos.find(function(a){return a.id === 'ceo_agent';}) ||
    ceos.find(function(a){return a.status === 'active';}) ||
    ceos[0];

  if(!keeper){
    var instr = '# CEO\n\n'
      + 'Role: CEO / Strategy Orchestrator\n'
      + 'Company: ' + (companyName || projectName) + '\n'
      + (description ? 'Mission: ' + description + '\n' : '')
      + '\nYou are the CEO of ' + (companyName || projectName) + '. Orchestrate the agent team, review pipeline results, build the company agent roster, and make strategic decisions.';

    keeper = RockoCore.createAgent(projectName,{
      id:'ceo_agent',
      name:'CEO',
      display_name:'CEO',
      role:'ceo',
      type:'prompt',
      description:'Company orchestrator and decision maker.',
      instruction_file:'agents\\ceo\\AGENT.md',
      instructions:instr,
      _instructions:instr,
      _base_instructions:instr,
      status:'active',
      model_provider:'__company_default__',
      model_override:null,
      using_company_default:true,
      provider_status:'configure_later',
      pipeline_step:'ceo_agent_step',
      enabled:true,
      project_tools:['filesystem','http'],
      apis:[],
      skills:[]
    },instr);
  }

  keeper.id = keeper.id || 'ceo_agent';
  keeper.name = keeper.name || 'CEO';
  keeper.display_name = keeper.display_name || 'CEO';
  keeper.role = 'ceo';
  keeper.type = keeper.type || 'prompt';
  keeper.status = keeper.status === 'error' ? 'idle' : (keeper.status || 'active');
  keeper.pipeline_step = 'ceo_agent_step';
  keeper.project = projectName;

  // Use the public deleteAgent API — RockoCore._agents is private and inaccessible from outside the IIFE
  ceos.forEach(function(a){
    if(a.id !== keeper.id){
      RockoCore.deleteAgent(a.id);
    }
  });
  RockoCore.updateAgent(keeper.id, keeper);

  if(manifest){
    manifest.agents = (manifest.agents || []).filter(function(a){
      return a && !isCEOAgent(a);
    });

    manifest.agents.unshift({
      id:keeper.id,
      name:'CEO',
      display_name:'CEO',
      role:'ceo',
      type:'prompt',
      description:keeper.description || 'Company orchestrator and decision maker.',
      instruction_file:keeper.instruction_file || 'agents\\ceo\\AGENT.md',
      model_provider:keeper.model_provider || '__company_default__',
      model_override:keeper.model_override || null,
      pipeline_step:'ceo_agent_step',
      enabled:true,
      status:keeper.status || 'active',
      project_tools:keeper.project_tools || ['filesystem','http'],
      apis:keeper.apis || [],
      skills:keeper.skills || [],
      _instructions:keeper.instructions || keeper._instructions || keeper._base_instructions || ''
    });

    manifest.pipeline = manifest.pipeline || {};
    manifest.pipeline.execution_order = (manifest.pipeline.execution_order || []).filter(function(step){
      if(!step)return false;
      if(step.type !== 'agent')return true;
      if(step.agent_id === 'ceo' || step.agent_id === 'ceo_agent')return false;
      if(step.step_id === 'ceo_step' || step.step_id === 'ceo_agent_step')return false;
      return true;
    });

    manifest.pipeline.execution_order.unshift({
      step_id:'ceo_agent_step',
      label:'CEO',
      type:'agent',
      agent_id:'ceo_agent',
      requires_approval:true
    });
  }

  syncCompanyAgentCache(projectName);
  RockoCore.saveState();
  return keeper;
}


function repairCEOPipeline(projectName, ceo) {
  var manifest = RockoCore.getProject(projectName);
  if (!manifest || !ceo) return;

  manifest.agents = (manifest.agents || []).filter(function(a) {
    return a && !isCEOAgent(a);
  });

  manifest.agents.unshift({
    id: ceo.id,
    name: ceo.name || 'CEO',
    display_name: ceo.display_name || 'CEO',
    role: 'ceo',
    type: ceo.type || 'prompt',
    instruction_file: ceo.instruction_file || 'agents\\ceo\\AGENT.md',
    model_provider: ceo.model_provider || '__company_default__',
    model_override: ceo.model_override || null,
    pipeline_step: ceo.pipeline_step || 'ceo_agent_step',
    enabled: true,
    status: ceo.status || 'active',
    project_tools: ceo.project_tools || ['filesystem', 'http'],
    apis: ceo.apis || [],
    local_code: ceo.local_code || null,
    description: ceo.description || 'Company orchestrator and decision maker.',
    _instructions: ceo.instructions || ceo._instructions || ''
  });

  manifest.pipeline = manifest.pipeline || {};
  manifest.pipeline.execution_order = manifest.pipeline.execution_order || [];

  manifest.pipeline.execution_order = manifest.pipeline.execution_order.filter(function(step) {
    if (!step) return false;
    if (step.type !== 'agent') return true;
    if (step.agent_id === 'ceo' || step.agent_id === 'ceo_agent') return false;
    if (step.step_id === 'ceo_step' || step.step_id === 'ceo_agent_step') return false;
    return true;
  });

  manifest.pipeline.execution_order.unshift({
    step_id: ceo.pipeline_step || 'ceo_agent_step',
    label: ceo.name || 'CEO',
    type: 'agent',
    agent_id: ceo.id,
    requires_approval: true
  });
}

function syncCompanyAgentCache(projectName) {
  var list = getCompanies();
  var agents = RockoCore.getAgents(projectName) || [];
  var manifest = RockoCore.getProject(projectName);

  list.forEach(function(co) {
    var coProject =
      (co._manifest && co._manifest.project && co._manifest.project.name) ||
      (co.display_name || co.id || '').replace(/\s+/g, '_');

    if (coProject === projectName) {
      co.agents = agents.map(function(a) {
        return {
          id: a.id,
          name: a.name,
          role: a.role,
          status: a.status
        };
      });

      co._manifest = manifest || co._manifest;
    }
  });

  saveCompanies(list);
}

async function persistCompanyCreation(projectName, companyRecord) {
  try {
    RockoCore.saveState();

    var stateRaw = localStorage.getItem('rockoagents_v4');
    var parsed = stateRaw ? JSON.parse(stateRaw) : null;

    if (!parsed || !parsed.projects || !parsed.projects[projectName]) {
      throw new Error('Browser save verification failed for project: ' + projectName);
    }

    var payload = {
      active: RockoCore.getActiveProject(),
      projects: parsed.projects || {},
      agents: parsed.agents || {},
      tasks: parsed.tasks || {},
      taskSeq: parsed.taskSeq || 0,
      runHistory: parsed.runHistory || [],
      companies: getCompanies(),
      created_company: companyRecord || null,
      saved_at: new Date().toISOString()
    };

    var bridgeSaved = false;
    try {
      var res = await fetch((typeof BRIDGE_URL !== 'undefined' ? BRIDGE_URL : 'http://localhost:8787') + '/data/save', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({key: 'rockoagents_state', data: payload}),
        signal: AbortSignal.timeout(4000)
      });
      bridgeSaved = !!res.ok;
      if (!bridgeSaved) {
        console.warn('Bridge file save returned non-OK status:', res.status);
      }
    } catch (bridgeErr) {
      console.warn('Bridge file save failed:', bridgeErr.message);
    }

    if (bridgeSaved) {
      toastOk('Company saved to browser + bridge file memory.');
    } else {
      (typeof toastWarn === 'function' ? toastWarn : toastErr)('Company saved to browser memory. Bridge file save failed or bridge is offline.');
    }

    return {ok: true, browserSaved: true, bridgeSaved: bridgeSaved};
  } catch (err) {
    toastErr('Company save failed: ' + err.message);
    console.error('Company persistence failure:', err);
    return {ok: false, error: err.message};
  }
}

async function verifyCompanyPersistence(projectName) {
  try {
    var raw = localStorage.getItem('rockoagents_v4');
    var state = raw ? JSON.parse(raw) : {};
    var hasProject = !!(state.projects && state.projects[projectName]);
    var hasCEO = !!Object.values(state.agents || {}).find(function(a){
      return a.project === projectName && a.role === 'ceo';
    });

    if (!hasProject || !hasCEO) {
      return {
        ok: false,
        hasProject: hasProject,
        hasCEO: hasCEO,
        error: 'Saved state is missing project or CEO'
      };
    }

    return {
      ok: true,
      hasProject: true,
      hasCEO: true,
      saved_at: state.saved_at || null
    };
  } catch (err) {
    return {ok: false, error: err.message};
  }
}


// Onboarding

function restoreRockoStateOnBoot(){
  try{
    var loaded = RockoCore.loadState();
    var active = RockoCore.getActiveProject();

    if(loaded && active){
      // Deduplicate any ghost CEOs persisted in localStorage from before this fix
      if (typeof ensureCompanyCEO === 'function') {
        var activeCo = typeof getActiveCompany === 'function' ? getActiveCompany() : null;
        ensureCompanyCEO(
          active,
          (activeCo && activeCo.display_name) || active,
          (activeCo && activeCo.description) || ''
        );
      }
      syncCompanyAgentCache(active);
      RockoCore.saveState();
      console.log('Rocko state restored on boot:', active);
      return true;
    }
  }catch(err){
    console.warn('Rocko state restore skipped:', err.message);
  }

  return false;
}
function showOnboarding()  { var o = document.getElementById('onboardingOverlay'); if (o) o.classList.add('show'); }
function hideOnboarding()  { var o = document.getElementById('onboardingOverlay'); if (o) o.classList.remove('show'); }

function onboardingLogoPreview(input) {
  if (!input.files || !input.files[0]) return;
  var reader = new FileReader();
  reader.onload = function(e) {
    _currentCompanyLogo = e.target.result;
    var box = document.getElementById('onboardingLogoBox');
    if (box) box.innerHTML = '<img src="' + e.target.result + '" style="width:100%;height:100%;object-fit:cover;">' +
      '<input type="file" id="onboardingLogoInput" accept="image/*" style="display:none" onchange="onboardingLogoPreview(this)">';
  };
  reader.readAsDataURL(input.files[0]);
}
function companyModalLogoPreview(input) {
  if (!input.files || !input.files[0]) return;
  var reader = new FileReader();
  reader.onload = function(e) {
    _companyModalLogo = e.target.result;
    var box = document.getElementById('companyModalLogoBox');
    if (box) box.innerHTML = '<img src="' + e.target.result + '" style="width:100%;height:100%;object-fit:cover;">' +
      '<input type="file" id="companyModalLogoInput" accept="image/*" style="display:none" onchange="companyModalLogoPreview(this)">';
  };
  reader.readAsDataURL(input.files[0]);
}

function _createCompany(displayName, description, path, logo){
  var id = displayName.toLowerCase().replace(/[^a-z0-9]/g,'_') + '_' + Date.now().toString(36);
  var safeProjectName = displayName.replace(/\s+/g,'_');

  var selectedProvider = _obProviderConfig || {provider_id:'later',model:'',status:'configure_later',base_url:''};

  var providerId = selectedProvider.provider_id || 'later';
  var selectedModel = selectedProvider.model || '';

  var providerApiBase =
    selectedProvider.base_url ||
    (providerId === 'local' ? 'http://localhost:11434/v1' :
     providerId === 'lmstudio' ? 'http://localhost:1234/v1' :
     providerId === 'free_proxy' ? 'http://localhost:8082/v1' :
     providerId === 'anthropic' ? 'https://api.anthropic.com/v1' :
     '');

  var ceoInstructions = '# CEO\n\n'
    + 'Role: CEO / Strategy Orchestrator\n'
    + 'Company: ' + displayName + '\n'
    + (description ? 'Mission: ' + description + '\n' : '')
    + '\nYou are the CEO of ' + displayName + '. Orchestrate the agent team, review pipeline results, and make strategic decisions. '
    + 'When asked to build a team, respond with a hire_agent decision containing an agents[] array. '
    + 'Each agent must include: name, role, agent_id, description, instructions, and optional skills.';

  var proj = RockoCore.generateProjectManifest({
    name:safeProjectName,
    rootPath:path,
    description:description,
    agents:[{
      id:'ceo_agent',
      name:'CEO',
      display_name:'CEO',
      role:'ceo',
      type:'prompt',
      instruction_file:'agents\\ceo\\AGENT.md',
      model_provider:'__company_default__',
      model_override:null,
      pipeline_step:'ceo_agent_step',
      enabled:true,
      status:'active',
      project_tools:['filesystem','http'],
      apis:[],
      local_code:null,
      description:'Company orchestrator and decision maker.',
      _instructions:ceoInstructions
    }]
  });

  proj.model.default_provider = providerId;
  proj.model.default_model = selectedModel;
  proj.model.providers = {};
  proj.model.providers[providerId] = {
    type: providerId === 'anthropic' ? 'anthropic' : 'openai_compatible',
    api_base: providerApiBase,
    temperature: .3,
    max_tokens: 2000
  };

  var co = {
    id:id,
    display_name:displayName,
    description:description,
    project_path:path,
    logo:logo || null,
    active:false,
    created_at:new Date().toISOString(),
    default_provider:providerId,
    default_model:selectedModel,
    base_url:providerApiBase,
    provider_status:selectedProvider.status || 'selected',
    _manifest:proj
  };

  var list = getCompanies();
  list.forEach(function(c){c.active = false;});
  co.active = true;
  list.push(co);
  saveCompanies(list);

  var loaded = RockoCore.loadProject(proj);
  if(!loaded || loaded.success === false){
    toastErr('Company project failed to load — CEO was not created. Check project manifest validation.');
    return null;
  }

  RockoCore.setActiveProject(proj.project.name);
  ensureCompanyCEO(proj.project.name, displayName, description);

  syncCompanyAgentCache(proj.project.name);
  RockoCore.saveState();
  persistCompanyCreation(proj.project.name, co);

  bridgePost('/companies',{
      id:co.id,
      display_name:co.display_name,
      description:co.description,
      project_path:co.project_path,
      logo_path:'',
      active:true,
      default_provider:co.default_provider || '',
      default_model:co.default_model || '',
      base_url:co.base_url || ''
  });

  renderCompanyRail();
  updateTopbarCompany(co);
  hideOnboarding();
  closeModal('companyModal');
  refreshAll();

  return co;
}

function onboardingCreate() {
  var name = document.getElementById('ob-name').value.trim();
  var desc = document.getElementById('ob-desc').value.trim();
  var path = document.getElementById('ob-path').value.trim();

  if (!name) { toastErr('Company name is required'); return; }
  if (!path) { toastErr('Project folder path is required'); return; }

  var co = _createCompany(name, desc, path, _currentCompanyLogo);
  if (!co) return;

  toastOk('Company created: ' + name + ' — CEO ready');
  refreshAll();
}

function onboardingImport() {
  var path = prompt('Enter the path to your project.json:');
  if (!path) return;
  // Try to load via bridge
  bridgePost('/file/read', {path: path}).then(function(r) {
    if (!r || !r.content) { toastErr('Could not read project.json'); return; }
    try {
      var manifest = JSON.parse(r.content);
      var co = {
        id: manifest.project.name + '_' + Date.now().toString(36),
        display_name: manifest.project.display_name || manifest.project.name,
        description: manifest.project.description || '',
        project_path: manifest.project.root_path,
        logo: null, active: true,
        created_at: new Date().toISOString(),
        _manifest: manifest
      };
      var list = getCompanies();
      list.forEach(function(c){ c.active = false; });
      list.push(co);
      saveCompanies(list);
      RockoCore.loadProject(manifest);
      RockoCore.setActiveProject(manifest.project.name);
      renderCompanyRail();
      updateTopbarCompany(co);
      hideOnboarding();
      refreshAll();
      toastOk('Imported: ' + co.display_name);
    } catch(e) { toastErr('Invalid project.json: ' + e.message); }
  });
}

// ── LLM App Templates ────────────────────────────────────────────────────────
var COMPANY_TEMPLATES = [
  {
    id: 'research_team',
    icon: '🔬',
    name: 'Research & Analysis Team',
    desc: 'Multi-agent team that researches topics, synthesizes findings, and produces structured reports.',
    tag: 'Research',
    tagColor: '#4782ff',
    agents: [
      {id:'researcher', name:'Researcher', role:'analyst', desc:'Deep dives into topics, finds sources, extracts key data.', instructions:'# Researcher\n\nRole: Research Analyst\n\nYou gather information on assigned topics. Search broadly, evaluate source quality, and extract the most relevant facts, statistics, and insights. Output structured findings with sources.'},
      {id:'synthesizer', name:'Synthesizer', role:'analyst', desc:'Combines research findings into coherent analysis.', instructions:'# Synthesizer\n\nRole: Research Synthesizer\n\nYou take raw research findings from multiple sources and combine them into a coherent, well-structured analysis. Identify patterns, conflicts, and key conclusions.'},
      {id:'writer', name:'Report Writer', role:'analyst', desc:'Turns analysis into polished reports and summaries.', instructions:'# Report Writer\n\nRole: Content Writer\n\nYou take synthesized research and produce clear, polished reports. Adapt tone and format to the audience. Prioritize clarity, accuracy, and actionable takeaways.'},
    ]
  },
  {
    id: 'trading_team',
    icon: '📈',
    name: 'Algorithmic Trading Team',
    desc: 'Pipeline of agents for market analysis, signal generation, risk management, and trade execution decisions.',
    tag: 'Finance',
    tagColor: '#22c55e',
    agents: [
      {id:'market_analyst', name:'Market Analyst', role:'analyst', desc:'Reads market conditions, news, and macro signals.', instructions:'# Market Analyst\n\nRole: Market Intelligence\n\nYou monitor market conditions, macro events, and sentiment signals. Output a structured market context block with scores, flags, and a brief narrative for the trading team.'},
      {id:'signal_generator', name:'Signal Generator', role:'engine', desc:'Generates trade signals from market data and indicators.', instructions:'# Signal Generator\n\nRole: Quant Signal Engine\n\nYou process market data and technical indicators to generate trade signals. Output specific entry/exit levels, direction, timeframe, and confidence score for each signal.'},
      {id:'risk_manager', name:'Risk Manager', role:'analyst', desc:'Evaluates risk, sizes positions, approves or blocks trades.', instructions:'# Risk Manager\n\nRole: Risk & Position Sizing\n\nYou evaluate proposed trades against portfolio risk limits. Calculate position size using Kelly criterion or fixed fractional. Approve, reduce, or block trades based on drawdown, exposure, and correlation limits.'},
    ]
  },
  {
    id: 'content_team',
    icon: '✍️',
    name: 'Content Creation Agency',
    desc: 'Full content pipeline from ideation through writing, editing, and SEO optimization.',
    tag: 'Content',
    tagColor: '#f59e0b',
    agents: [
      {id:'strategist', name:'Content Strategist', role:'analyst', desc:'Plans content calendar and topics based on goals.', instructions:'# Content Strategist\n\nRole: Content Strategy\n\nYou plan content based on audience, goals, and trends. Produce a content brief for each piece including target audience, key messages, format, and success metrics.'},
      {id:'copywriter', name:'Copywriter', role:'analyst', desc:'Writes high-quality drafts from briefs.', instructions:'# Copywriter\n\nRole: Content Writer\n\nYou write compelling drafts based on content briefs. Match the brand voice, structure content for the format, and write for both humans and search engines.'},
      {id:'editor', name:'Editor', role:'analyst', desc:'Reviews, refines, and finalizes all content.', instructions:'# Editor\n\nRole: Content Editor\n\nYou review drafts for clarity, accuracy, tone consistency, and quality. Return a revised version with tracked changes and a brief note on what was improved.'},
      {id:'seo_analyst', name:'SEO Analyst', role:'analyst', desc:'Optimizes content for search and distribution.', instructions:'# SEO Analyst\n\nRole: SEO & Distribution\n\nYou analyze content for search optimization opportunities. Suggest keywords, meta descriptions, headings, and internal linking strategies to maximize organic reach.'},
    ]
  },
  {
    id: 'dev_team',
    icon: '💻',
    name: 'Software Development Team',
    desc: 'Agents for architecture planning, code generation, review, testing, and documentation.',
    tag: 'Engineering',
    tagColor: '#8b5cf6',
    agents: [
      {id:'architect', name:'Architect', role:'analyst', desc:'Designs system architecture and technical specs.', instructions:'# Architect\n\nRole: Software Architect\n\nYou design system architecture, define component boundaries, and produce technical specifications. Consider scalability, maintainability, and security in every design decision.'},
      {id:'developer', name:'Developer', role:'engine', desc:'Implements features based on specs.', instructions:'# Developer\n\nRole: Software Engineer\n\nYou implement features from technical specifications. Write clean, well-documented code. Follow the architecture decisions and flag any blockers or design conflicts.'},
      {id:'reviewer', name:'Code Reviewer', role:'analyst', desc:'Reviews code for quality, bugs, and security.', instructions:'# Code Reviewer\n\nRole: Code Quality\n\nYou review code for correctness, performance, security vulnerabilities, and maintainability. Provide specific, actionable feedback with line-level comments where needed.'},
      {id:'tester', name:'QA Engineer', role:'engine', desc:'Generates and runs tests, reports issues.', instructions:'# QA Engineer\n\nRole: Quality Assurance\n\nYou write test cases, identify edge cases, and verify that implementations match specifications. Report issues with clear reproduction steps and expected vs actual behavior.'},
    ]
  },
  {
    id: 'customer_support',
    icon: '🎧',
    name: 'Customer Support Team',
    desc: 'Tiered support pipeline with triage, resolution, escalation, and satisfaction tracking.',
    tag: 'Support',
    tagColor: '#06b6d4',
    agents: [
      {id:'triage', name:'Triage Agent', role:'analyst', desc:'Categorizes and prioritizes incoming tickets.', instructions:'# Triage Agent\n\nRole: Support Triage\n\nYou categorize incoming support requests by type, urgency, and complexity. Route tickets to the appropriate handler and flag critical issues for immediate escalation.'},
      {id:'support_agent', name:'Support Specialist', role:'analyst', desc:'Resolves standard customer issues.', instructions:'# Support Specialist\n\nRole: Customer Support\n\nYou resolve customer issues using the knowledge base and available tools. Be empathetic, clear, and thorough. Escalate when the issue is beyond your authority or expertise.'},
      {id:'escalation', name:'Escalation Manager', role:'analyst', desc:'Handles complex or sensitive cases.', instructions:'# Escalation Manager\n\nRole: Senior Support\n\nYou handle escalated cases that require authority, complexity, or sensitivity. Review the full customer history before responding. Document all resolutions for the knowledge base.'},
    ]
  },
  {
    id: 'marketing_team',
    icon: '📣',
    name: 'Marketing & Growth Team',
    desc: 'Agents for campaign planning, ad copy, social media, analytics, and growth experiments.',
    tag: 'Marketing',
    tagColor: '#ec4899',
    agents: [
      {id:'growth_strategist', name:'Growth Strategist', role:'analyst', desc:'Plans campaigns and growth experiments.', instructions:'# Growth Strategist\n\nRole: Growth & Marketing Strategy\n\nYou design marketing campaigns and growth experiments. Define target audiences, channels, messaging, and success metrics. Prioritize high-leverage activities with clear ROI potential.'},
      {id:'ad_copywriter', name:'Ad Copywriter', role:'analyst', desc:'Writes ad copy, headlines, and CTAs.', instructions:'# Ad Copywriter\n\nRole: Performance Copywriter\n\nYou write direct-response ad copy, headlines, and calls to action. Optimize for clicks, conversions, and relevance score. Produce multiple variations for A/B testing.'},
      {id:'social_manager', name:'Social Media Manager', role:'analyst', desc:'Creates and schedules social content.', instructions:'# Social Media Manager\n\nRole: Social Media\n\nYou create platform-native content for social channels. Adapt tone and format to each platform. Monitor engagement signals and adjust content strategy based on performance data.'},
      {id:'analytics', name:'Analytics Agent', role:'engine', desc:'Tracks metrics and surfaces insights.', instructions:'# Analytics Agent\n\nRole: Marketing Analytics\n\nYou track campaign performance, identify trends, and surface actionable insights. Report on key metrics with clear context on what is working, what is not, and what to do next.'},
    ]
  },
  {
    id: 'data_team',
    icon: '📊',
    name: 'Data Analysis Pipeline',
    desc: 'Agents for data ingestion, cleaning, analysis, visualization planning, and insight reporting.',
    tag: 'Data',
    tagColor: '#f97316',
    agents: [
      {id:'data_engineer', name:'Data Engineer', role:'engine', desc:'Ingests and prepares raw data for analysis.', instructions:'# Data Engineer\n\nRole: Data Preparation\n\nYou ingest raw data, validate quality, handle missing values, and structure it for analysis. Document all transformations and flag anomalies for review.'},
      {id:'analyst_data', name:'Data Analyst', role:'analyst', desc:'Runs analysis and identifies patterns.', instructions:'# Data Analyst\n\nRole: Statistical Analysis\n\nYou analyze prepared datasets to identify patterns, trends, and anomalies. Apply appropriate statistical methods and validate findings before reporting.'},
      {id:'insight_reporter', name:'Insight Reporter', role:'analyst', desc:'Translates data findings into actionable narratives.', instructions:'# Insight Reporter\n\nRole: Data Storytelling\n\nYou translate analytical findings into clear, actionable narratives for decision-makers. Lead with the most important insight, provide supporting evidence, and always end with a recommended action.'},
    ]
  },
  {
    id: 'competitive_intel',
    icon: '🕵️',
    name: 'Competitive Intelligence Team',
    desc: 'Monitors competitors, tracks market moves, and delivers weekly intelligence briefings.',
    tag: 'Research',
    tagColor: '#4782ff',
    agents: [
      {id:'monitor', name:'Market Monitor', role:'engine', desc:'Continuously scans for competitor and market signals.', instructions:'# Market Monitor\n\nRole: Competitive Monitoring\n\nYou track competitor websites, social channels, press releases, job postings, and product changes. Flag significant signals and categorize them by type and urgency.'},
      {id:'intel_analyst', name:'Intelligence Analyst', role:'analyst', desc:'Analyzes signals and identifies strategic implications.', instructions:'# Intelligence Analyst\n\nRole: Competitive Analysis\n\nYou analyze competitive signals and identify strategic implications for the business. Connect dots across multiple data points and surface non-obvious insights.'},
      {id:'briefing_writer', name:'Briefing Writer', role:'analyst', desc:'Produces weekly competitive intelligence reports.', instructions:'# Briefing Writer\n\nRole: Intelligence Reporting\n\nYou produce clear, concise competitive intelligence briefings. Lead with the most important developments, explain why they matter, and include recommended responses.'},
    ]
  },
];

var _activeTemplate = null;
var _cmCurrentTab = 'blank';

function cmSetTab(tab) {
  _cmCurrentTab = tab;
  var blankBtn = document.getElementById('cm-tab-blank');
  var tplBtn   = document.getElementById('cm-tab-template');
  var blankPanel = document.getElementById('cm-blank-panel');
  var tplPanel   = document.getElementById('cm-template-panel');
  if (tab === 'blank') {
    if (blankBtn) { blankBtn.style.background = 'var(--accent)'; blankBtn.style.color = '#fff'; }
    if (tplBtn)   { tplBtn.style.background = 'var(--bg-card)'; tplBtn.style.color = 'var(--text-dim)'; }
    if (blankPanel) blankPanel.style.display = '';
    if (tplPanel)   tplPanel.style.display   = 'none';
  } else {
    if (tplBtn)   { tplBtn.style.background = 'var(--accent)'; tplBtn.style.color = '#fff'; }
    if (blankBtn) { blankBtn.style.background = 'var(--bg-card)'; blankBtn.style.color = 'var(--text-dim)'; }
    if (blankPanel) blankPanel.style.display = 'none';
    if (tplPanel)   tplPanel.style.display   = '';
    renderTemplateGallery();
  }
}

function renderTemplateGallery() {
  var gallery = document.getElementById('cm-template-gallery');
  if (!gallery) return;
  gallery.innerHTML = '';
  COMPANY_TEMPLATES.forEach(function(tpl) {
    var card = document.createElement('div');
    card.style.cssText = 'padding:14px;border:1px solid var(--border);cursor:pointer;transition:all .15s;background:var(--bg-card);';
    card.innerHTML =
      '<div style="display:flex;align-items:center;gap:10px;margin-bottom:8px;">' +
        '<span style="font-size:22px;">' + tpl.icon + '</span>' +
        '<div>' +
          '<div style="font-family:var(--cond);font-size:14px;font-weight:700;letter-spacing:.5px;">' + tpl.name + '</div>' +
          '<span style="font-family:var(--mono);font-size:9px;padding:1px 6px;border:1px solid ' + tpl.tagColor + ';color:' + tpl.tagColor + ';">' + tpl.tag + '</span>' +
        '</div>' +
      '</div>' +
      '<div style="font-family:var(--mono);font-size:10px;color:var(--text-dim);line-height:1.6;margin-bottom:8px;">' + tpl.desc + '</div>' +
      '<div style="font-family:var(--mono);font-size:10px;color:var(--text-secondary);">' + (tpl.agents.length + 1) + ' agents incl. CEO</div>';
    card.addEventListener('mouseenter', function(){ card.style.borderColor = 'var(--accent)'; card.style.background = 'rgba(71,130,255,.04)'; });
    card.addEventListener('mouseleave', function(){ card.style.borderColor = 'var(--border)'; card.style.background = 'var(--bg-card)'; });
    card.addEventListener('click', function(){ selectTemplate(tpl.id); });
    gallery.appendChild(card);
  });
}

function selectTemplate(id) {
  var tpl = COMPANY_TEMPLATES.find(function(t){ return t.id === id; });
  if (!tpl) return;
  _activeTemplate = tpl;
  // Show confirmation form, hide gallery
  var gallery  = document.getElementById('cm-template-gallery');
  var selected = document.getElementById('cm-template-selected');
  if (gallery)  gallery.style.display  = 'none';
  if (selected) selected.style.display = '';
  // Populate template info
  var icon  = document.getElementById('cm-tpl-icon');
  var title = document.getElementById('cm-tpl-title');
  var desc  = document.getElementById('cm-tpl-desc-label');
  var agLbl = document.getElementById('cm-tpl-agents-label');
  if (icon)  icon.textContent  = tpl.icon;
  if (title) title.textContent = tpl.name;
  if (desc)  desc.textContent  = tpl.desc;
  if (agLbl) agLbl.textContent = 'CEO + ' + tpl.agents.map(function(a){ return a.name; }).join(', ');
  // Pre-fill hidden desc for use on submit, clear name so user types their own
  var nameEl = document.getElementById('cm-tpl-name');
  var pathEl = document.getElementById('cm-tpl-path');
  if (nameEl) { nameEl.value = ''; nameEl.focus(); }
  if (pathEl && !pathEl.value) pathEl.value = 'C:\\Users\\brian\\Documents\\' + tpl.id.replace(/_/g,'');
}

function cmClearTemplate() {
  _activeTemplate = null;
  var gallery  = document.getElementById('cm-template-gallery');
  var selected = document.getElementById('cm-template-selected');
  if (gallery)  gallery.style.display  = 'grid';
  if (selected) selected.style.display = 'none';
}

function openCompanyModal() {
  _companyModalLogo = null;
  _activeTemplate   = null;
  _cmCurrentTab     = 'blank';
  var box = document.getElementById('companyModalLogoBox');
  if (box) box.innerHTML = '<span style="font-size:22px;">🏢</span><input type="file" id="companyModalLogoInput" accept="image/*" style="display:none" onchange="companyModalLogoPreview(this)">';
  // Reset tabs to blank
  var blankBtn  = document.getElementById('cm-tab-blank');
  var tplBtn    = document.getElementById('cm-tab-template');
  var blankPanel = document.getElementById('cm-blank-panel');
  var tplPanel   = document.getElementById('cm-template-panel');
  if (blankBtn)  { blankBtn.style.background = 'var(--accent)'; blankBtn.style.color = '#fff'; }
  if (tplBtn)    { tplBtn.style.background = 'var(--bg-card)'; tplBtn.style.color = 'var(--text-dim)'; }
  if (blankPanel) blankPanel.style.display = '';
  if (tplPanel)   tplPanel.style.display   = 'none';
  // Clear inputs
  ['cm-name','cm-desc','cm-path','cm-tpl-name','cm-tpl-path'].forEach(function(id){ var el=document.getElementById(id); if(el) el.value=''; });
  openModal('companyModal');
}
function submitCompanyModal() {
  var name = '';
  var path = '';
  var desc = '';

  if (_activeTemplate) {
    name = (document.getElementById('cm-tpl-name') || {}).value || '';
    path = (document.getElementById('cm-tpl-path') || {}).value || '';
    desc = _activeTemplate.desc || '';
  } else {
    name = (document.getElementById('cm-name') || {}).value || '';
    path = (document.getElementById('cm-path') || {}).value || '';
    desc = (document.getElementById('cm-desc') || {}).value || '';
  }

  name = name.trim();
  path = path.trim();
  desc = desc.trim();

  if (!name) { toastErr('Company name is required'); return; }
  if (!path) { toastErr('Project path is required'); return; }

  var co = _createCompany(name, desc, path, _companyModalLogo);
  if (!co) return;

  var _proj = RockoCore.getActiveProject();

  if (_activeTemplate && _activeTemplate.agents && _activeTemplate.agents.length) {
    _activeTemplate.agents.forEach(function(a) {
      if (!a || !a.id || a.role === 'ceo' || a.id === 'ceo' || a.id === 'ceo_agent') return;

      var agentDef = {
        id: a.id,
        name: a.name,
        display_name: a.name,
        role: a.role,
        type: 'prompt',
        description: a.desc,
        instructions: a.instructions,
        _base_instructions: a.instructions,
        status: 'idle',
        model_provider: '__company_default__',
        model_override: null,
        using_company_default: true,
        provider_status: 'configure_later',
        pipeline_step: a.id + '_step',
        enabled: true,
        skills: [],
        projectName: _proj
      };

      if (_proj && !RockoCore.getAgent(a.id)) {
        RockoCore.createAgent(_proj, agentDef, a.instructions);
      }
    });

    toastOk('Company created: ' + name + ' — CEO + ' + _activeTemplate.agents.length + ' template agents ready');
  } else {
    toastOk('Company created: ' + name + ' — CEO ready');
  }

  _activeTemplate = null;
  refreshAll();
  setTimeout(function(){ refreshAll(); }, 400);
}

// ── Edit / Delete Company ────────────────────────────────────────────────────
var _editCompanyId = null;
var _editCompanyLogo = null;

function openEditCompany(id) {
  var list = getCompanies();
  var co = list.find(function(c){ return c.id === id; });
  if (!co) return;
  _editCompanyId = id;
  _editCompanyLogo = co.logo || null;
  document.getElementById('ec-name').value = co.display_name || '';
  document.getElementById('ec-desc').value = co.description || '';
  var box = document.getElementById('editCompanyLogoBox');
  if (box) {
    if (co.logo) {
      box.innerHTML = '<img src="' + co.logo + '" style="width:100%;height:100%;object-fit:cover;border-radius:10px;">'
        + '<input type="file" id="editCompanyLogoInput" accept="image/*" style="display:none" onchange="editCompanyLogoPreview(this)">';
    } else {
      box.innerHTML = '<span style="font-size:22px;">🏢</span>'
        + '<input type="file" id="editCompanyLogoInput" accept="image/*" style="display:none" onchange="editCompanyLogoPreview(this)">';
    }
  }
  openModal('editCompanyModal');
}

function editCompanyLogoPreview(input) {
  if (!input.files || !input.files[0]) return;
  var reader = new FileReader();
  reader.onload = function(e) {
    _editCompanyLogo = e.target.result;
    var box = document.getElementById('editCompanyLogoBox');
    if (box) box.innerHTML = '<img src="' + e.target.result + '" style="width:100%;height:100%;object-fit:cover;border-radius:10px;">'
      + '<input type="file" id="editCompanyLogoInput" accept="image/*" style="display:none" onchange="editCompanyLogoPreview(this)">';
  };
  reader.readAsDataURL(input.files[0]);
}

function submitEditCompany() {
  if (!_editCompanyId) return;
  var name = document.getElementById('ec-name').value.trim();
  var desc = document.getElementById('ec-desc').value.trim();
  if (!name) { toastErr('Company name is required'); return; }
  var list = getCompanies();
  var co = list.find(function(c){ return c.id === _editCompanyId; });
  if (!co) return;
  co.display_name = name;
  co.description = desc;
  if (_editCompanyLogo !== null) co.logo = _editCompanyLogo;
  saveCompanies(list);
  renderCompanyRail();
  if (co.active) updateTopbarCompany(co);
  closeModal('editCompanyModal');
  toastOk('Company updated: ' + name);
}

async function deleteCompany(id) {
  var list = getCompanies();
  var co = list.find(function(c){ return c.id === id; });
  if (!co) return;

  if (!confirm('Delete "' + (co.display_name || co.id) + '" permanently?\n\nThis removes the company everywhere RockoAgents knows about it: UI cache, PGlite, bridge registry, active project state, cached agents, and recovered company lists.')) return;

  var projectName = getCompanyProjectName(co);

  markCompanyDeleted(id);

  var newList = filterDeletedCompanies(list.filter(function(c){ return c.id !== id; }));
  newList.forEach(function(c, i){ c.active = i === 0; });

  if (projectName && RockoCore._agents) {
    Object.keys(RockoCore._agents).forEach(function(agentId){
      var a = RockoCore._agents[agentId];
      if (a && a.project === projectName) delete RockoCore._agents[agentId];
    });
  }

  if (projectName && RockoCore._projects && RockoCore._projects[projectName]) {
    delete RockoCore._projects[projectName];
  }

  if (RockoCore.getActiveProject && RockoCore.getActiveProject() === projectName) {
    var nextProjectName = getCompanyProjectName(newList[0]);
    if (nextProjectName && RockoCore._projects && RockoCore._projects[nextProjectName]) {
      RockoCore.setActiveProject(nextProjectName);
    } else if (RockoCore._active !== undefined) {
      RockoCore._active = null;
    }
  }

  _companiesCache = newList;
  try { localStorage.setItem(COMPANIES_KEY, JSON.stringify(newList.map(function(c){ var x=Object.assign({},c); delete x._manifest; return x; }))); } catch(e) {}

  if (_pgReady && _pgdb) {
    try { await _pgDeleteCompany(id); } catch(e) {}
  }

  await scrubDeletedCompanyEverywhere(id, projectName);

  saveCompanies(newList);
  RockoCore.saveState();
  try { pgWrite('rockoagents_v4', RockoCore.exportState ? RockoCore.exportState() : localStorage.getItem('rockoagents_v4')); } catch(e) {}

  if (newList.length) {
    var next = newList[0];
    if (next._manifest) {
      RockoCore.loadProject(next._manifest);
      RockoCore.setActiveProject(next._manifest.project ? next._manifest.project.name : next.id);
      _skillDelegationAgentId = null;
      _skillDelegationCompanyId = next.id || getCompanyProjectName(next);
    }
    updateTopbarCompany(next);
  } else {
    localStorage.setItem(COMPANIES_KEY, '[]');
    _companiesCache = [];
    _skillDelegationAgentId = null;
    _skillDelegationCompanyId = null;
    showOnboarding();
  }

  renderCompanyRail();
  renderSkillDelegationPanel();
  refreshAll();
  closeModal('editCompanyModal');
  toastOk('Company permanently deleted everywhere.');
}

// Workspace gating — intercept setTab to require company
var _setTabBase2 = setTab;
setTab = function(view, btn) {
  var gated = ['agents','pipeline','tasks','history','automation','orchestration','runtimes','settings'];
  if (gated.includes(view) && !getActiveCompany()) {
    // Check if we can auto-recover before blocking
    var proj = RockoCore.getActiveProject();
    if (proj && RockoCore.getProject(proj)) {
      autoMigrateExistingProject();
    }
    if (!getActiveCompany()) {
      showOnboarding();
      return;
    }
  }
  _setTabBase2(view, btn);
};

// Auto-migrate ThePaperTeam on first load
function autoMigrateExistingProject() {
  var companies = getCompanies();
  if (companies.length > 0) return;  // already have companies
  var proj = RockoCore.getActiveProject();
  if (!proj) return;
  var manifest = RockoCore.getProject(proj);
  if (!manifest) return;
  // Migrate the existing loaded project as first company
  var co = {
    id: proj.toLowerCase().replace(/[^a-z0-9]/g,'_') + '_migrated',
    display_name: manifest.project.display_name || manifest.project.name || proj,
    description:  manifest.project.description  || '',
    project_path: manifest.project.root_path    || '',
    logo:         null,
    active:       true,
    created_at:   new Date().toISOString(),
    _manifest:    manifest
  };
  companies.push(co);
  saveCompanies(companies);
  renderCompanyRail();
  updateTopbarCompany(co);
  RockoCore.log('info', 'Auto-migrated existing project as company: ' + co.display_name);
}


// ═══════════════════════════════════════════════════════════════
// SKILLS SYSTEM — powered by skills.sh (Vercel's open skills directory)
// CEO agent can browse skills.sh and assign skills to other agents.
// Skills are SKILL.md files from GitHub repositories.
// ═══════════════════════════════════════════════════════════════
var _skillsLibrary  = [];
var _skillsTargetId = null;
var _skillsSource   = 'local';
// skill delegation reads live from active company on every render