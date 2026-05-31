/* BOOT — startup, providers, models, team build, DOMContentLoaded */

function startPolling() {
  // Compatibility shim: older boot flow calls startPolling(), while this file now
  // performs bridge polling inside init(). Keep this no-op so boot never halts.
  return true;
}

async function fullBoot() {
  bootPGlite().then(function(ok) {
    if (ok) RockoCore.log('info', 'PGlite: storage active');
  });

  // ── 1. Hard-reset session — login required on every boot ────
  setSessionToken(null);
  _sessionToken = null;
  _currentUser  = null;

  // ── 2. Force app to dashboard view so the correct screen is
  //       behind the login overlay regardless of browser restore ─
  document.querySelectorAll('.view').forEach(function(x){ x.classList.remove('active'); });
  var _dashEl = document.getElementById('view-dashboard');
  if (_dashEl) _dashEl.classList.add('active');
  document.querySelectorAll('.nav-tab').forEach(function(t){
    t.classList.toggle('active', t.textContent.toLowerCase().trim() === 'dashboard');
  });

  // ── 3. Show login overlay on top of everything ───────────────
  showLoginOverlay();

  startPolling();
  initProviderSelection();
  init();
}

// Handle browser back-forward cache (bfcache) restores — when a user navigates
// back/forward the page is rehydrated from cache without re-running scripts.
// Force login again so the user can never skip auth via browser history.
window.addEventListener('pageshow', function(event) {
  if (event.persisted) {
    setSessionToken(null);
    _sessionToken = null;
    _currentUser  = null;
    document.querySelectorAll('.view').forEach(function(x){ x.classList.remove('active'); });
    var _d = document.getElementById('view-dashboard');
    if (_d) _d.classList.add('active');
    showLoginOverlay();
  }
});


async function testProvider(providerId) {
  showLoading('Testing ' + providerId + ' connection...');
  var r = await bridgePost('/models/providers/' + providerId + '/test', {});
  hideLoading();
  if (r && r.ok) {
    toastOk(providerId.toUpperCase() + ' connection successful');
  } else {
    toastErr((providerId.toUpperCase() + ': ') + (r && r.error ? r.error.slice(0,100) : 'Connection failed'));
  }
}

// ── NVIDIA model helper — populate model selector ─────────────
var NVIDIA_MODELS = [
  'nvidia/llama-3.1-nemotron-ultra-253b-v1',
  'meta/llama-3.1-70b-instruct',
  'meta/llama-3.3-70b-instruct',
  'deepseek-ai/deepseek-r1',
  'mistralai/mixtral-8x7b-instruct-v0.1',
  'google/gemma-3-27b-it',
  'microsoft/phi-4',
  'qwen/qwen2.5-72b-instruct',
];

function populateModelSelector(selectId, currentValue) {
  var sel = document.getElementById(selectId);
  if (!sel) return;
  bridgeGet('/models/providers').then(function(d) {
    if (!d) return;
    sel.innerHTML = '<option value="">— Use project default —</option>';
    Object.entries(d).forEach(function(pair) {
      var provId = pair[0], cfg = pair[1];
      var models = cfg.available_models || [];
      if (provId === 'anthropic') {
        ['claude-opus-4-20250514','claude-sonnet-4-20250514','claude-haiku-4-5-20251001'].forEach(function(m) {
          var opt = document.createElement('option');
          opt.value = m; opt.textContent = 'Anthropic: ' + m;
          if (m === currentValue) opt.selected = true;
          sel.appendChild(opt);
        });
      } else if (models.length) {
        models.forEach(function(m) {
          var opt = document.createElement('option');
          opt.value = m; opt.textContent = (cfg.display_name || provId) + ': ' + m;
          if (m === currentValue) opt.selected = true;
          sel.appendChild(opt);
        });
      }
    });
  });
}



// -- Onboarding picker logic -----------------------------------
function showOnboardingPicker() {
  var pp=document.getElementById('onboardingPickerPanel'); if(pp)pp.style.display='';
  var cp=document.getElementById('onboardingCreatePanel'); if(cp)cp.style.display='none';
  renderOnboardingCompanyCards();
}
function showOnboardingCreate() {
  var pp2=document.getElementById('onboardingPickerPanel'); if(pp2)pp2.style.display='none';
  var cp2=document.getElementById('onboardingCreatePanel'); if(cp2)cp2.style.display='';
}
function getCompanyAgentCount(co) {
  if (!co) return 0;

  var projectName =
    (co._manifest && co._manifest.project && co._manifest.project.name) ||
    (co.display_name || co.id || '').replace(/\s+/g, '_');

  var liveAgents = [];
  try {
    liveAgents = RockoCore.getAgents(projectName) || [];
  } catch {
    liveAgents = [];
  }

  var manifestAgents =
    co._manifest && Array.isArray(co._manifest.agents)
      ? co._manifest.agents
      : [];

  var companyAgents = Array.isArray(co.agents) ? co.agents : [];

  return Math.max(liveAgents.length, manifestAgents.length, companyAgents.length);
}
function renderOnboardingCompanyCards() {
  var el = document.getElementById('onboardingCompanyCards');
  if (!el) return;

  el.innerHTML = '';

  getCompanies().forEach(function(co) {
    var card = document.createElement('div');
    card.className = 'ob-company-card';

    var logoHtml = co.logo
      ? '<img src="' + co.logo + '" class="ob-company-logo" style="width:72px;height:72px;border-radius:12px;object-fit:cover;" alt="">'
      : '<div class="ob-company-logo">' + (co.display_name || co.id).charAt(0).toUpperCase() + '</div>';

    var agents = getCompanyAgentCount(co);
    var metaStr = agents > 0 ? agents + ' agent' + (agents === 1 ? '' : 's') : 'no agents yet';

    card.innerHTML = logoHtml +
      '<div class="ob-company-name">' + (co.display_name || co.id) + '</div>' +
      (co.description ? '<div class="ob-company-desc">' + co.description.slice(0,80) + '</div>' : '') +
      '<div class="ob-company-meta">' + metaStr + '</div>';

    card.addEventListener('click', function() {
      setActiveCompany(co.id);
      hideOnboarding();
    });

    el.appendChild(card);
  });
}
// Override showOnboarding: picker for returning users, create form for new users
var _showOnboardingBase = showOnboarding;
showOnboarding = function() {
  var el = document.getElementById('onboardingOverlay');
  if (el) el.classList.add('show');
  if (getCompanies().length > 0) {
    showOnboardingPicker();
  } else {
    document.getElementById('onboardingPickerPanel').style.display = 'none';
    document.getElementById('onboardingCreatePanel').style.display = '';
  }
};


// -- Provider selection + validation ---------------------------

// ── Provider config helpers ───────────────────────────────────────────────────
function _buildProviderBlock(prov) {
  if (!prov || !prov.id || prov.id === 'later') return {};
  var block = {};
  var baseUrls = {
    anthropic: 'https://api.anthropic.com/v1',
    openai:    'https://api.openai.com/v1',
    gemini:    'https://generativelanguage.googleapis.com/v1beta/openai',
    nvidia:    'https://integrate.api.nvidia.com/v1',
    local:     'http://localhost:11434/v1',
    lmstudio:  'http://localhost:1234/v1',
  };
  block[prov.id] = {
    type:        prov.id === 'anthropic' ? 'anthropic' : 'openai_compatible',
    api_base:    prov.baseUrl || baseUrls[prov.id] || '',
    api_key_env: prov.envVar || null,
    temperature: 0.3,
    max_tokens:  2000,
  };
  return block;
}

function getCompanyDefaultProvider() {
  var co = getActiveCompany();
  if (!co || !co.default_provider) return null;
  return {
    id:    co.default_provider,
    model: co.default_model || '',
  };
}

function getAgentModelStatus(agent) {
  // Returns {label, status} for display on agent card
  var co = getActiveCompany();
  var provId = agent.model_provider;
  if (!provId || provId === '__company_default__') {
    provId = co ? co.default_provider : null;
  }
  if (!provId || provId === 'later') {
    return {label: 'Configure Later', status: 'configure_later', dot: 'unchecked'};
  }
  var prov = PROVIDERS.find(function(p){ return p.id === provId; });
  var name = prov ? prov.name : provId;
  var model = agent.model_override || (co ? co.default_model : '') || '';
  // Check connection status from last validation result (cached)
  var cached = _providerStatusCache[provId];
  if (!cached) return {label: name + (model ? ' - ' + model.split('/').pop() : ''), status: 'unchecked', dot: 'unchecked'};
  var dot = cached === 'connected' ? 'live' : cached === 'configure_later' ? 'unchecked' : 'offline';
  return {label: name + (model ? ' - ' + model.split('/').pop() : ''), status: cached, dot: dot};
}
var _providerStatusCache = {};

async function checkAndCacheProviderStatus(provId, baseUrl) {
  var r = await bridgePost('/models/providers/' + provId + '/validate', {base_url: baseUrl || ''});
  if (r && r.status) _providerStatusCache[provId] = r.status;
  return r;
}

var PROVIDERS = [
  {id:'free_proxy', name:'Free Tier — No API Key',  icon:'★', color:'#22c55e', envVar:null, local:true,  defaultModel:'nvidia_nim/moonshotai/kimi-k2-thinking', baseUrl:'http://localhost:8082', free:true},
  {id:'anthropic',  name:'Anthropic',               icon:'A', color:'#ff6b00', envVar:'ANTHROPIC_API_KEY', local:false, defaultModel:'claude-sonnet-4-20250514'},
  {id:'openai',     name:'OpenAI',                  icon:'O', color:'#00c864', envVar:'OPENAI_API_KEY',    local:false, defaultModel:'gpt-4o'},
  {id:'gemini',     name:'Google Gemini',           icon:'G', color:'#4285f4', envVar:'GEMINI_API_KEY',    local:false, defaultModel:'gemini-2.0-flash'},
  {id:'nvidia',     name:'NVIDIA',                  icon:'N', color:'#76b900', envVar:'NVIDIA_API_KEY',    local:false, defaultModel:'nvidia/llama-3.1-nemotron-ultra-253b-v1'},
  {id:'local',      name:'Ollama (Local)',          icon:'⬡', color:'#4782ff', envVar:null,                local:true,  defaultModel:'llama3.2', baseUrl:'http://localhost:11434/v1'},
  {id:'lmstudio',   name:'LM Studio (Local)',       icon:'S', color:'#8b5cf6', envVar:null,                local:true,  defaultModel:'local-model', baseUrl:'http://localhost:1234/v1'},
  {id:'custom',     name:'Custom Endpoint',         icon:'C', color:'#6b7280', envVar:null,                local:false, defaultModel:''},
  {id:'later',      name:'Configure Later',         icon:'?', color:'#4b5563', envVar:null,                local:false, defaultModel:''},
];

// Free proxy backends
var FREE_PROXY_BACKENDS = [
  {id:'nvidia_nim',   name:'NVIDIA NIM',  note:'40 req/min free — get key at build.nvidia.com', keyPlaceholder:'nvapi-...', models:{opus:'nvidia_nim/z-ai/glm4.7', sonnet:'nvidia_nim/moonshotai/kimi-k2-thinking', haiku:'nvidia_nim/stepfun-ai/step-3.5-flash'}},
  {id:'openrouter',   name:'OpenRouter',  note:'Many free models — get key at openrouter.ai',   keyPlaceholder:'sk-or-...',  models:{opus:'open_router/deepseek/deepseek-r1-0528:free', sonnet:'open_router/openai/gpt-oss-120b:free', haiku:'open_router/stepfun/step-3.5-flash:free'}},
  {id:'lmstudio_proxy',name:'LM Studio', note:'Fully local/offline — no key needed',            keyPlaceholder:'',           models:{opus:'lmstudio/unsloth/MiniMax-M2.5-GGUF', sonnet:'lmstudio/unsloth/Qwen3.5-35B-A3B-GGUF', haiku:'lmstudio/unsloth/GLM-4.7-Flash-GGUF'}},
];
var _selectedFreeBackend = 'nvidia_nim';
var _selectedProvider = null;
var _providerValidated = false;

function renderProviderList(containerId) {
  var select = document.getElementById('ob-provider-select');
  if (!select) return;

  select.innerHTML = '<option value="">Select provider...</option>';

  PROVIDERS.forEach(function(prov) {
    var op = document.createElement('option');
    op.value = prov.id;

    var label = prov.name;

    if (prov.id === 'free_proxy') {
      label += ' — Free Tier';
    }

    if (prov.id === 'local') {
      var count = (window._ollamaModels || []).length;
      label += window._ollamaStatus === 'online'
        ? ' — Ollama Local (' + count + ' models)'
        : ' — Ollama Local';
    }

    op.textContent = label;
    select.appendChild(op);
  });
}

function onProviderDropdownChange() {
  var providerSelect = document.getElementById('ob-provider-select');
  var modelRow = document.getElementById('ob-model-row');
  var modelSelect = document.getElementById('ob-model');
  var customRow = document.getElementById('ob-custom-url');
  var statusEl = document.getElementById('ob-provider-status');
  var summary = document.getElementById('ob-selected-summary');

  var providerId = providerSelect ? providerSelect.value : '';
  var prov = PROVIDERS.find(function(p) { return p.id === providerId; }) || null;

  _selectedProvider = prov;
  _providerValidated = false;

  if (modelSelect) {
    modelSelect.innerHTML = '<option value="">Select model...</option>';
  }

  if (customRow) {
    customRow.style.display = prov && prov.id === 'custom' ? '' : 'none';
  }

  if (statusEl) {
    statusEl.style.display = 'none';
    statusEl.textContent = '';
  }

  if (summary) {
    summary.style.display = prov ? '' : 'none';
    summary.textContent = prov ? 'Selected provider: ' + prov.name : '';
  }

  if (!prov) {
    if (modelRow) modelRow.style.display = 'none';
    return;
  }

  var needsModel = !(prov.id === 'later' || prov.id === 'free_proxy');
  if (modelRow) modelRow.style.display = needsModel ? '' : 'none';

  if (needsModel && modelSelect) {
    var models = [];

    if (prov.id === 'local') {
      models = window._ollamaModels || [];
    } else if (prov.models) {
      models = Object.values(prov.models);
    } else if (prov.defaultModel) {
      models = [prov.defaultModel];
    }

    models.forEach(function(model) {
      if (!model) return;
      var op = document.createElement('option');
      op.value = model;
      op.textContent = model;
      modelSelect.appendChild(op);
    });

    if (modelSelect.options.length > 1) {
      modelSelect.selectedIndex = 1;
      window._selectedOllamaModel = modelSelect.value;
    }
  }

  if (prov.id === 'free_proxy' || prov.id === 'later') {
    _providerValidated = true;
  } else {
    setTimeout(function(){ validateSelectedProvider(); }, 250);
  }

  onModelDropdownChange();
}

function onModelDropdownChange() {
  var providerSelect = document.getElementById('ob-provider-select');
  var modelSelect = document.getElementById('ob-model');
  var customBase = document.getElementById('ob-custom-base');
  var summary = document.getElementById('ob-selected-summary');

  var providerId = providerSelect ? providerSelect.value : '';
  var prov = PROVIDERS.find(function(p) { return p.id === providerId; }) || null;
  var model = modelSelect ? modelSelect.value : '';

  window._selectedOllamaModel = model || '';

  if(prov){
    _obProviderConfig = {
      provider_id: prov.id,
      model: model || prov.defaultModel || '',
      status: prov.id === 'later' ? 'configure_later' : 'selected',
      base_url: prov.id === 'custom'
        ? ((customBase || {}).value || '')
        : (prov.baseUrl || '')
    };
  }else{
    _obProviderConfig = null;
  }

  if (summary && prov) {
    summary.style.display = '';
    summary.textContent = 'Selected: ' + prov.name + (model ? ' / ' + model : '');
  }
}

function renderProxyBackendDetail() {
  var detail = document.getElementById('ob-proxy-backend-detail');
  if (!detail) return;
  var b = FREE_PROXY_BACKENDS.find(function(x){ return x.id === _selectedFreeBackend; });
  if (!b) return;
  detail.innerHTML =
    '<div style="font-family:var(--mono);font-size:10px;color:var(--text-dim);margin-bottom:6px;">' + b.note + '</div>' +
    (b.keyPlaceholder ? '<input class="field-input" id="ob-proxy-key" placeholder="' + b.keyPlaceholder + '" style="font-size:11px;padding:7px 10px;" oninput="cacheProxyKey()">' : '<div style="color:#22c55e;font-family:var(--mono);font-size:10px;">No key required — just start LM Studio on port 1234</div>');
}

function cacheProxyKey() {
  var el = document.getElementById('ob-proxy-key');
  if (el) window._proxyKeyInput = el.value;
}

function renderOllamaModelList() {
  var providerSelect = document.getElementById('ob-provider-select');
  var modelSelect = document.getElementById('ob-model');

  if (!providerSelect || !modelSelect) return;
  if (providerSelect.value !== 'local') return;

  onProviderDropdownChange();
}

function selectOllamaModel(el, model) {
  document.querySelectorAll('.ollama-model-opt').forEach(function(o){ o.style.borderColor='transparent'; o.style.color=''; });
  el.style.borderColor = 'var(--accent)';
  el.style.color = 'var(--accent)';
  var mi = document.getElementById('ob-model');
  if (mi) mi.value = model;
  window._selectedOllamaModel = model;
}

async function detectOllama() {
  window._ollamaStatus = 'checking';
  try {
    var r = await fetch('http://localhost:11434/api/tags', {signal: AbortSignal.timeout(2000)});
    if (r.ok) {
      var data = await r.json();
      window._ollamaModels = (data.models||[]).map(function(m){ return m.name; });
      window._ollamaStatus = 'online';
    } else { window._ollamaStatus = 'offline'; window._ollamaModels = []; }
  } catch(e) { window._ollamaStatus = 'offline'; window._ollamaModels = []; }
  // Update any visible status notes
  var note = document.getElementById('ollama-status-note');
  if (note) {
    note.style.color = window._ollamaStatus === 'online' ? 'var(--green)' : 'var(--text-dim)';
    note.textContent = window._ollamaStatus === 'online'
      ? 'ONLINE — ' + window._ollamaModels.length + ' models detected'
      : 'OFFLINE — install Ollama to enable';
  }
  renderOllamaModelList();
}

async function detectFreeProxy() {
  try {
    var r = await fetch('http://localhost:8082/health', {signal: AbortSignal.timeout(1500)});
    window._proxyStatus = r.ok ? 'online' : 'offline';
  } catch(e) { window._proxyStatus = 'offline'; }
}

async function validateSelectedProvider() {
  if (!_selectedProvider) return;
  var statusEl = document.getElementById('ob-provider-status');
  if (!statusEl) return;
  statusEl.style.display = '';
  statusEl.textContent = 'Checking ' + _selectedProvider.name + '...';
  statusEl.style.color = 'var(--accent)';
  _providerValidated = false;

  var baseUrl = _selectedProvider.id === 'custom'
    ? (document.getElementById('ob-custom-base') || {}).value || ''
    : (_selectedProvider.baseUrl || '');

  var r = await bridgePost('/models/providers/' + _selectedProvider.id + '/validate', {base_url: baseUrl});
  if (!r) {
    statusEl.textContent = 'Bridge offline - cannot validate. Create company anyway or start bridge first.';
    statusEl.style.color = 'var(--yellow)';
    _providerValidated = true; // allow creation with warning
    return;
  }
  _providerValidated = r.ok || r.status === 'configure_later' || r.status === 'offline';
  // Cache result for agent status display
  if (r.status) _providerStatusCache[_selectedProvider.id] = r.status;
  var icon = r.ok ? 'CONNECTED' : (r.status === 'offline' ? 'OFFLINE' : r.status === 'missing_key' ? 'KEY MISSING' : 'ERROR');
  statusEl.textContent = icon + ' - ' + r.message;
  statusEl.style.color = r.ok ? 'var(--green)' : (r.status === 'offline' ? 'var(--yellow)' : 'var(--red)');
}

async function recheckProxyStatus() {
  var dot   = document.getElementById('settings-proxy-status-dot');
  var label = document.getElementById('settings-proxy-status-label');
  if (label) { label.textContent = 'Checking...'; label.style.color = 'var(--accent)'; }
  await detectFreeProxy();
  var online = window._proxyStatus === 'online';
  if (dot)   dot.style.background   = online ? 'var(--green)' : 'var(--red)';
  if (label) { label.textContent = online ? 'Proxy running on localhost:8082' : 'Proxy offline — start it with the command above'; label.style.color = online ? 'var(--green)' : 'var(--yellow)'; }
}

async function recheckOllamaStatus() {
  var dot   = document.getElementById('settings-ollama-dot');
  var label = document.getElementById('settings-ollama-label');
  var mlist = document.getElementById('settings-ollama-models');
  if (label) { label.textContent = 'Checking...'; label.style.color = 'var(--accent)'; }
  await detectOllama();
  var online = window._ollamaStatus === 'online';
  if (dot)   dot.style.background = online ? 'var(--green)' : 'var(--border)';
  if (label) { label.textContent = online ? 'Online — ' + (window._ollamaModels||[]).length + ' models available' : 'Offline — install Ollama or run: ollama serve'; label.style.color = online ? 'var(--green)' : 'var(--text-dim)'; }
  if (mlist) { mlist.textContent = online && window._ollamaModels.length ? 'Available: ' + window._ollamaModels.join(', ') : ''; }
}

// Run both checks when settings tab opens
function refreshSettingsExternalStatus() {
  recheckProxyStatus();
  recheckOllamaStatus();
}

var _obProviderConfig = null;

// Init provider list on boot
function initProviderSelection() {
  detectOllama();
  detectFreeProxy();
  renderProviderList('ob-provider-list');
}


// ── CEO Team Building ─────────────────────────────────────────────────────────
var _pendingTeam = null;

async function runBuildTeamTask() {
  var co = getActiveCompany();
  if (!co) { toastErr('Create a company first'); return; }
  var mission = prompt('Enter the company mission — CEO will build the team:\n\nExample: "Build a research company that monitors Solana DEX markets."');
  if (!mission) return;
  showLoading('CEO is building your team...');
  var r = await bridgePost('/tasks', {
    title:        'Build team for: ' + co.display_name,
    assigned_to:  'ceo_agent',
    type:         'agent',
    instructions: 'Build the team you need to accomplish this mission: ' + mission + '\n\nUse the hire_agent decision with an agents[] array. For each agent include: name, role, agent_id (snake_case), description, complete AGENT.md instructions, and relevant skills from skills.sh.',
    input:        { mission: mission, company: co.display_name },
    priority:     'high',
  });
  hideLoading();
  if (r && r.id) {
    toastOk('CEO task queued — building team...');
    pollForTeamResult(r.id);
  } else {
    toastErr('Could not queue task — is bridge running?');
  }
}

async function pollForTeamResult(taskId) {
  var attempts = 0;
  var iv = setInterval(async function() {
    attempts++;
    if (attempts > 60) { clearInterval(iv); toastErr('CEO task timed out'); return; }
    var r = await bridgeGet('/tasks/' + taskId);
    if (!r) return;
    if (r.status === 'complete') { clearInterval(iv); toastOk('CEO completed team proposal'); refreshAll(); }
    else if (r.status === 'failed') { clearInterval(iv); toastErr('CEO task failed: ' + (r.error||'unknown')); }
  }, 3000);
}

function showTeamReview(agents, reason) {
  _pendingTeam = { agents: agents, reason: reason };
  var reasonEl = document.getElementById('teamReviewReason');
  var agentsEl = document.getElementById('teamReviewAgents');
  if (reasonEl) reasonEl.textContent = reason || 'CEO proposes the following team:';
  if (agentsEl) {
    agentsEl.innerHTML = '';
    (agents||[]).forEach(function(a) {
      var card = document.createElement('div');
      card.className = 'team-agent-card';
      var skills = (a.skills||[]).map(function(s){ return '<span style="font-family:var(--mono);font-size:9px;padding:2px 6px;background:rgba(71,130,255,.1);border:1px solid rgba(71,130,255,.3);color:var(--accent);">' + (s.skill_name||s) + '</span>'; }).join(' ');
      card.innerHTML =
        '<div class="team-agent-name">' + (a.name||a.agent_id||'Agent') + '</div>' +
        '<div class="team-agent-role">' + (a.role||'analyst') + '</div>' +
        '<div class="team-agent-desc">' + (a.description||'') + '</div>' +
        '<div class="team-agent-meta">' +
          '<span>ID: ' + (a.agent_id||'') + '</span>' +
          '<span>File: agents/' + (a.agent_id||'') + '/AGENT.md</span>' +
          (skills ? '<span>' + skills + '</span>' : '') +
        '</div>';
      agentsEl.appendChild(card);
    });
  }
  openModal('teamReviewModal');
}

async function approveTeamCreation() {
  if (!_pendingTeam) return;
  closeModal('teamReviewModal');
  await executeTeamCreation(_pendingTeam.agents, _pendingTeam.reason);
  _pendingTeam = null;
}

async function executeTeamCreation(agents, reason) {
  showLoading('Creating ' + agents.length + ' agent(s)...');
  var r = await bridgePost('/agents/create_team', { agents: agents, reason: reason, auto_approve: true });
  hideLoading();
  if (r && r.ok) {
    toastOk(r.count + ' agent(s) created');
    refreshAll();
  } else {
    toastErr('Team creation failed');
  }
}

function handleCEOHireDecision(decision) {
  var agents = decision.agents || [];
  if (!agents.length) return;
  var auto = decision.auto_create || false;
  var co   = getActiveCompany();
  if (co && co.policy && co.policy.require_approval_for_hire) auto = false;
  if (auto) { executeTeamCreation(agents, decision.reason||''); }
  else       { showTeamReview(agents, decision.reason||''); }
}

function showOrchestratorResult(result) {
  if (!result) return;
  if (result.action === 'hire_agent' && result.agents && result.agents.length) {
    setTimeout(function(){ handleCEOHireDecision(result); }, 400);
  }
}

fullBoot();




document.addEventListener('DOMContentLoaded', function(){
  if (typeof restoreRockoStateOnBoot === 'function') restoreRockoStateOnBoot();
  // After state is restored, rehydrate company manifests from bridge.
  // This means _manifest never needs to live in localStorage — the bridge is the source of truth.
  _rehydrateCompaniesFromBridge();
});

async function _rehydrateCompaniesFromBridge() {
  try {
    var res = await fetch('http://127.0.0.1:8787/companies', {signal: AbortSignal.timeout(4000)});
    if (!res.ok) return;
    var d = await res.json();
    var bridgeCompanies = d.companies || d;
    if (!Array.isArray(bridgeCompanies) || !bridgeCompanies.length) return;

    // Merge bridge data into the local company list, updating manifests
    var local = getCompanies();
    var merged = bridgeCompanies.map(function(bc) {
      var existing = local.find(function(lc){ return lc.id === bc.id; }) || {};
      // Bridge company is authoritative for manifest/project data;
      // local is authoritative for active state and logo
      return Object.assign({}, bc, {
        active: existing.active || bc.active || false,
        logo: existing.logo || bc.logo || null
      });
    });

    // Preserve active flag from local if set
    var localActive = local.find(function(c){ return c.active; });
    if (localActive) {
      merged.forEach(function(c){ c.active = c.id === localActive.id; });
    } else if (merged.length) {
      merged[0].active = true;
    }

    // Write slim version (no _manifest) to localStorage
    var slim = merged.map(function(c){
      var s = Object.assign({}, c);
      delete s._manifest;
      return s;
    });
    try { localStorage.setItem('rockoagents_companies_v1', JSON.stringify(slim)); } catch(e) {}

    // Keep _manifest in memory cache only
    if (typeof _companiesCache !== 'undefined') {
      _companiesCache = merged;
    }

    // If no company is currently active in RockoCore, activate the first one
    var activeCo = merged.find(function(c){ return c.active; }) || merged[0];
    if (activeCo && typeof _activateCompany === 'function') {
      if (!RockoCore.getActiveProject()) {
        _activateCompany(activeCo);
      }
    }

    RockoCore.log('info', 'Companies rehydrated from bridge: ' + merged.length);
    if (typeof renderCompanyRail === 'function') renderCompanyRail();
    if (activeCo && typeof updateTopbarCompany === 'function') updateTopbarCompany(activeCo);
  } catch(e) {
    // Bridge offline — silently continue with whatever localStorage has
  }
}