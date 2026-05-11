/* ============================================================
   SKILLS & HIRE — skills library, delegation, hire/fire,
                   bootCompanyLayer, openAgent override
   ============================================================ */

function renderSkillsTab() {
  var el = document.getElementById('skillsTabList');
  if (!el) return;
  var searchEl = document.getElementById('skillsTabSearch');
  var query = searchEl ? searchEl.value.toLowerCase() : '';
  var list = _skillsLibrary.filter(function(s) {
    if (!query) return true;
    return (s.name||'').toLowerCase().includes(query) ||
           (s.description||'').toLowerCase().includes(query) ||
           (s.source||'').toLowerCase().includes(query);
  });
  if (!list.length) {
    el.innerHTML = '<div style="text-align:center;padding:40px;font-family:var(--mono);font-size:12px;color:var(--text-dim);">' +
      '<div style="font-size:28px;margin-bottom:12px;">📦</div>' +
      (query ? 'No skills match that search.' : 'No skills loaded yet — bridge must be running.') +
      '<br><button class="btn btn-ghost" style="margin-top:12px;" onclick="loadSkillsLibrary(true).then(renderSkillsTab)">⟳ Fetch from skills.sh</button>' +
      '</div>';
    renderSkillDelegationPanel();
    return;
  }
  var html = '';
  list.forEach(function(s) {
    var name     = s.name || s.slug || s.id || '';
    var source   = s.source || '';
    var desc     = s.description || '';
    var installs = s.installs ? (s.installs > 999 ? Math.round(s.installs/1000) + 'K' : s.installs) + ' installs' : '';
    var skillId  = s.id || name;
    html += '<div style="display:flex;align-items:center;justify-content:space-between;padding:10px 14px;border-bottom:1px solid var(--border);gap:12px;">' +
      '<div style="flex:1;min-width:0;">' +
        '<div style="font-family:var(--mono);font-size:12px;font-weight:600;color:var(--accent);">' + name + '</div>' +
        (source ? '<div style="font-size:10px;color:var(--text-dim);margin-top:2px;">' + source + '</div>' : '') +
        (desc   ? '<div style="font-size:11px;color:var(--text-secondary);margin-top:3px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">' + desc + '</div>' : '') +
      '</div>' +
      '<div style="display:flex;align-items:center;gap:8px;flex-shrink:0;">' +
        (installs ? '<span style="font-family:var(--mono);font-size:10px;color:var(--text-dim);">' + installs + '</span>' : '') +
        '<button class="btn btn-ghost" style="padding:4px 10px;font-size:10px;" data-skill-id="' + skillId + '" onclick="copySkillId(this)">⎘ Copy ID</button>' +
      '</div>' +
    '</div>';
  });
  el.innerHTML = html;
  renderSkillDelegationPanel();
}

function copySkillId(btn) {
  var id = btn.getAttribute('data-skill-id');
  if (!id) return;
  if (navigator.clipboard) {
    navigator.clipboard.writeText(id).then(function() { toastOk('Copied: ' + id); });
  } else {
    toastOk('ID: ' + id);
  }
}


function skillPanelEscape(v) {
  return String(v == null ? '' : v).replace(/[&<>'"]/g, function(c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[c];
  });
}

function renderSkillDelegationPanel() {
  var panel = document.getElementById('skillDelegationPanel');
  if (!panel) return;
  var proj   = getActiveCompanyProjectName();
  var agents = (RockoCore.getAgents(proj) || []).filter(function(a){
    return a && a.status !== 'fired' && a.enabled !== false;
  });
  if (!agents.length) {
    var co = getActiveCompany ? getActiveCompany() : null;
    if (co && Array.isArray(co.agents)) {
      agents = co.agents.filter(function(a){ return a && a.status !== 'fired' && a.enabled !== false; });
    }
  }
  if (!agents.length) {
    panel.innerHTML = '<div style="padding:20px;font-family:var(--mono);font-size:11px;color:var(--text-dim);text-align:center;">No agents in this company.</div>';
    return;
  }
  var PALETTE = ['var(--accent)','var(--green)','var(--purple)','#e07b39','#39b4e0','#e039b4','#b4e039'];
  var html = '<div style="font-family:var(--mono);font-size:9px;letter-spacing:2px;color:var(--text-dim);text-transform:uppercase;margin-bottom:8px;">Agent Skill Delegation</div>';
  agents.forEach(function(agent, idx) {
    var aId   = skillPanelEscape(agent.id || agent.agent_id || '');
    var aName = agent.display_name || agent.name || aId;
    var isCEO = aId.toLowerCase().indexOf('ceo') !== -1 || (agent.role||'').toLowerCase() === 'ceo';
    var color = isCEO ? 'var(--yellow)' : PALETTE[idx % PALETTE.length];
    var skills = agent.skills || [];
    var chipsHtml = skills.length
      ? skills.map(function(s){
          var sid   = typeof s === 'string' ? s : (s.id || s.skill_id || s.name || '');
          var sname = typeof s === 'string' ? s : (s.name || s.skill_name || sid);
          return '<span style="display:inline-flex;align-items:center;font-family:var(--mono);font-size:9px;padding:1px 5px;background:rgba(71,130,255,.12);border:1px solid rgba(71,130,255,.3);border-radius:2px;margin:1px 2px 1px 0;">'
            + skillPanelEscape(sname)
            + '<span style="cursor:pointer;margin-left:3px;opacity:.6;" data-ds="' + skillPanelEscape(sid) + '" data-da="' + aId + '" onclick="delegDel(this)"> x</span>'
            + '</span>';
        }).join('')
      : '<span style="font-family:var(--mono);font-size:9px;color:var(--text-dim);">No skills assigned</span>';
    html +=
      '<div style="border:1px solid var(--border);border-left:3px solid ' + color + ';border-radius:3px;margin-bottom:6px;padding:6px 8px;">'
        + '<div style="font-family:var(--cond);font-size:12px;font-weight:700;color:' + color + ';margin-bottom:4px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;" title="' + aId + '">'
          + skillPanelEscape(aName)
          + (isCEO ? '<span style="font-size:8px;background:var(--yellow);color:#000;padding:0 4px;border-radius:2px;margin-left:5px;vertical-align:middle;">CEO</span>' : '')
        + '</div>'
        + '<div style="display:flex;gap:4px;margin-bottom:4px;">'
          + '<input id="di-' + aId + '" data-aid="' + aId + '" class="field-input" style="flex:1;height:22px;font-size:10px;font-family:var(--mono);padding:1px 6px;" placeholder="skill id…" onkeydown="if(event.key===\'Enter\'){delegAdd(this.getAttribute(\'data-aid\'));}" />'
          + '<button class="btn btn-success" data-aid="' + aId + '" style="height:22px;padding:0 8px;font-size:9px;white-space:nowrap;" onclick="delegAdd(this.getAttribute(\'data-aid\'))">ADD</button>'
        + '</div>'
        + '<div style="display:flex;flex-wrap:wrap;align-items:center;">' + chipsHtml + '</div>'
      + '</div>';
  });
  panel.innerHTML = html;
}



async function loadSkillsLibrary(forceRemote) {
  if (!forceRemote && _skillsLibrary.length) return;
  var d = null;
  try {
    d = await bridgeGet('/skills/browse');
    if (d && Array.isArray(d.skills) && d.skills.length) {
      _skillsLibrary = d.skills;
      _skillsSource  = d.source || 'skills.sh';
      RockoCore.log('info', 'Skills loaded from ' + _skillsSource + ': ' + d.skills.length + ' skill(s)');
      return;
    }
  } catch(e) {}
  try {
    d = await bridgeGet('/skills');
    if (d && Array.isArray(d.skills) && d.skills.length) {
      _skillsLibrary = d.skills;
      _skillsSource  = 'local';
      RockoCore.log('info', 'Skills loaded from local: ' + d.skills.length + ' skill(s)');
      return;
    }
  } catch(e) {}
  RockoCore.log('warn', 'No skills found. Bridge must be running and skills.sh must be reachable.');
}

function getAgentSkills(agentId) {
  var agent = RockoCore.getAgent(agentId);
  return (agent && agent.skills) ? agent.skills : [];
}

function openSkillsModal(agentId) {
  _skillsTargetId = agentId || currentAgentId;
  if (!_skillsTargetId) { toastErr('Select an agent first'); return; }
  var agent  = RockoCore.getAgent(_skillsTargetId);
  var nameEl = document.getElementById('skillsModalAgentName');
  if (nameEl && agent) nameEl.textContent = agent.name;
  renderSkillsLibrary();
  openModal('skillsModal');
  // Refresh from skills.sh in background
  loadSkillsLibrary(true).then(function(){ renderSkillsLibrary(); });
}

function renderSkillsLibrary() {
  var el = document.getElementById('skillsLibraryList');
  if (!el) return;
  if (!_skillsLibrary.length) {
    el.innerHTML = '<div class="empty-state" style="text-align:center;">' +
      '<div style="font-size:24px;margin-bottom:8px;">📡</div>' +
      '<div>Loading skills from skills.sh...</div>' +
      '<div style="color:var(--text-dim);font-size:10px;margin-top:6px;">Bridge must be running to browse live skills</div>' +
      '<button class="btn btn-ghost" style="margin-top:10px;font-size:10px;" onclick="loadSkillsLibrary(true).then(renderSkillsLibrary)">Retry</button>' +
      '</div>';
    return;
  }
  var agentSkills   = getAgentSkills(_skillsTargetId);
  var agentSkillIds = agentSkills.map(function(s){ return s.id || s; });
  var sourceLabel   = _skillsSource === 'skills.sh' ? '● Live from skills.sh' : '● Local skills.json';
  var sourceColor   = _skillsSource === 'skills.sh' ? 'var(--green)' : 'var(--yellow)';
  el.innerHTML = '<div style="font-family:var(--mono);font-size:10px;color:' + sourceColor + ';margin-bottom:10px;">' + sourceLabel + '</div>';
  _skillsLibrary.forEach(function(skill) {
    var skillKey = skill.id || (skill.repo + '/' + skill.skill_name);
    var applied  = agentSkillIds.some(function(sid){ return sid === skillKey || sid === skill.id; });
    var item     = document.createElement('div');
    item.className = 'skill-library-item' + (applied ? ' applied' : '');
    var installsInfo = skill.installs ? '<span class="skill-cat" style="color:var(--green);">' + skill.installs + ' installs</span>' : '';
    var repoInfo     = skill.repo ? '<span class="skill-cat">' + skill.repo + '</span>' : '';
    item.innerHTML =
      '<div style="display:flex;align-items:flex-start;justify-content:space-between;gap:10px;">' +
        '<div style="flex:1;">' +
          '<div class="skill-name">' + (skill.name || skill.skill_name) + '</div>' +
          '<div class="skill-desc">' + (skill.description || '') + '</div>' +
          '<div style="margin-top:5px;display:flex;gap:6px;flex-wrap:wrap;">' + repoInfo + installsInfo + '</div>' +
        '</div>' +
        (applied
          ? '<button class="btn btn-danger" style="font-size:10px;padding:3px 8px;flex-shrink:0;" onclick="removeSkillFromAgent(\'' + skillKey + '\')">Remove</button>'
          : '<button class="btn btn-success" style="font-size:10px;padding:3px 8px;flex-shrink:0;" onclick="applySkillFromModal(\'' + skillKey + '\',\'' + (skill.repo||'') + '\',\'' + (skill.skill_name||skill.id||'') + '\')">Apply</button>') +
      '</div>';
    el.appendChild(item);
  });
}

async function applySkillFromModal(skillId, repo, skillName) {
  if (!_skillsTargetId) return;
  showLoading('Fetching skill from ' + (repo || 'library') + '...');
  var skillData = null;
  // If from skills.sh (has repo), fetch the SKILL.md live from GitHub
  if (repo && skillName) {
    var r = await bridgeGet('/skills/fetch?repo=' + encodeURIComponent(repo) + '&skill=' + encodeURIComponent(skillName));
    if (r && r.skill) {
      skillData = r.skill;
    }
  }
  // Fallback: use from local library
  if (!skillData) {
    skillData = _skillsLibrary.find(function(s){ return s.id === skillId; });
  }
  hideLoading();
  if (!skillData) { toastErr('Could not load skill content'); return; }
  applySkillToAgent(skillId, skillData);
}

function applySkillToAgent(skillId, skillData) {
  if (!_skillsTargetId) return;
  var proj = getActiveCompanyProjectName ? getActiveCompanyProjectName() : RockoCore.getActiveProject();
  var agent = (RockoCore.getAgents(proj) || []).find(function(a){ return a.id === _skillsTargetId; }) || RockoCore.getAgent(_skillsTargetId);
  if (!agent) return;
  var skills = agent.skills ? JSON.parse(JSON.stringify(agent.skills)) : [];
  if (skills.find(function(s){ return (s.id||s) === skillId; })) { toastWarn('Skill already applied'); return; }
  skills.push({
    id:         skillId,
    name:       skillData.name || skillId,
    repo:       skillData.repo || '',
    skill_name: skillData.skill_name || skillId,
    applied_at: new Date().toISOString(),
    source:     skillData.source || 'local',
  });
  // Append skill instructions
  var base  = agent._base_instructions || agent.instructions || '';
  if (!agent._base_instructions) RockoCore.updateAgent(_skillsTargetId, {_base_instructions: base});
  var newInstr = base + (base ? '\n\n' : '') + (skillData.instructions || skillData.raw || '');
  RockoCore.updateAgent(_skillsTargetId, {skills: skills, instructions: newInstr});
  // Also notify bridge to save skill locally
  if (skillData.repo && skillData.skill_name) {
    bridgePost('/skills/assign', {repo: skillData.repo, skill_name: skillData.skill_name, agent_id: _skillsTargetId});
  }
  toastOk('Skill applied: ' + (skillData.name || skillId));
  renderSkillsLibrary();
  renderAgentAppliedSkills();
}

function removeSkillFromAgent(skillId) {
  if (!_skillsTargetId) return;
  var proj = getActiveCompanyProjectName ? getActiveCompanyProjectName() : RockoCore.getActiveProject();
  var agent = (RockoCore.getAgents(proj) || []).find(function(a){ return a.id === _skillsTargetId; }) || RockoCore.getAgent(_skillsTargetId);
  if (!agent) return;
  var skills    = (agent.skills || []).filter(function(s){ return (s.id||s) !== skillId; });
  var baseInstr = agent._base_instructions || agent.instructions || '';
  // Rebuild instructions from remaining skills
  var newInstr  = baseInstr;
  skills.forEach(function(s) {
    var lib = _skillsLibrary.find(function(l){ return l.id === (s.id||s); });
    if (lib && lib.instructions) newInstr += (newInstr ? '\n\n' : '') + lib.instructions;
  });
  RockoCore.updateAgent(_skillsTargetId, {skills: skills, instructions: newInstr});
  toastOk('Skill removed');
  renderSkillsLibrary();
  renderAgentAppliedSkills();
}

function renderAgentAppliedSkills() {
  var el = document.getElementById('agentAppliedSkills');
  if (!el || !currentAgentId) return;
  var agent  = RockoCore.getAgent(currentAgentId);
  var skills = (agent && agent.skills) ? agent.skills : [];
  if (!skills.length) {
    el.innerHTML = '<span style="font-family:var(--mono);font-size:10px;color:var(--text-dim);">No skills applied — CEO can assign from skills.sh</span>';
    return;
  }
  el.innerHTML = skills.map(function(s) {
    var id   = s.id || s;
    var name = s.name || id;
    var src  = s.source === 'skills.sh' ? ' <span style="color:var(--green);font-size:9px;">●</span>' : '';
    return '<span class="skill-chip">' + name + src +
      '<span class="remove-skill" onclick="removeSkillFromAgent(\'' + id + '\')">✕</span></span>';
  }).join('');
}


// ═══════════════════════════════════════════════════════════════
// HIRE / FIRE SYSTEM
// ═══════════════════════════════════════════════════════════════
var _hireRole = 'analyst';

function openHireModal() {
  _hireRole = 'analyst';
  var fields = ['hireAgentName','hireAgentDesc','hireAgentInstructions'];
  fields.forEach(function(id){ var el=document.getElementById(id); if(el) el.value=''; });
  document.querySelectorAll('.hire-role-option').forEach(function(el){ el.classList.remove('selected'); });
  var def = document.querySelector('.hire-role-option[data-role="analyst"]');
  if (def) def.classList.add('selected');
  openModal('hireModal');
}

function selectHireRole(el, role) {
  _hireRole = role;
  document.querySelectorAll('.hire-role-option').forEach(function(o){ o.classList.remove('selected'); });
  el.classList.add('selected');
}

function submitHireAgent() {
  var name  = document.getElementById('hireAgentName').value.trim();
  var desc  = document.getElementById('hireAgentDesc').value.trim();
  var instr = document.getElementById('hireAgentInstructions').value.trim();
  if (!name) { toastErr('Agent name is required'); return; }
  var proj  = RockoCore.getActiveProject();
  var id    = name.toLowerCase().replace(/[^a-z0-9]/g,'_') + '_' + Date.now().toString(36).slice(-4);
  var _co = getActiveCompany();
  var agent = {
    id:           id,
    name:         name,
    display_name: name,
    role:         _hireRole,
    type:         'prompt',
    description:  desc,
    instructions: instr,
    _base_instructions: instr,
    skills:       [],
    hired_at:     new Date().toISOString(),
    pipeline_step: id + '_step',
    enabled:      true,
    status:       'active',
    model_provider:  '__company_default__',
    model_override:  null,
    using_company_default: true,
  };
  if (proj) RockoCore.createAgent(proj, agent, instr);
  closeModal('hireModal');
  toastOk('Agent hired: ' + name);
  refreshAll();
  RockoCore.log('success', 'HIRED: ' + name + ' (' + _hireRole + ')');
}

function fireAgent(agentId) {
  var agent = RockoCore.getAgent(agentId);
  if (!agent) return;
  if (!confirm('Fire agent "' + agent.name + '"?\n\nThe agent will be deactivated and removed from the active pipeline. Run history is preserved.')) return;
  RockoCore.updateAgent(agentId, {
    status:    'fired',
    fired_at:  new Date().toISOString(),
    enabled:   false,
  });
  toastWarn('Agent fired: ' + agent.name);
  refreshAll();
  RockoCore.log('warn', 'FIRED: ' + agent.name + ' (' + agentId + ')');
}

function rehireAgent(agentId) {
  var agent = RockoCore.getAgent(agentId);
  if (!agent) return;
  RockoCore.updateAgent(agentId, { status: 'active', enabled: true, fired_at: null });
  toastOk('Agent reinstated: ' + agent.name);
  refreshAll();
  RockoCore.log('info', 'REINSTATED: ' + agent.name);
}

// Override openAgent to render skills panel when agent opens
var _openAgentBase = openAgent;
openAgent = function(id) {
  _openAgentBase(id);
  setTimeout(function() {
    renderAgentAppliedSkills();
    // Add fire/rehire button to agent editor header if not there
    var hdr = document.querySelector('#view-agents .dash-header .dash-title');
    if (hdr) {
      var agent = RockoCore.getAgent(id);
      var existingFireBtn = document.getElementById('agentFireBtn');
      if (existingFireBtn) existingFireBtn.remove();
      if (agent) {
        var btn = document.createElement('button');
        btn.id = 'agentFireBtn';
        btn.style.cssText = 'margin-left:8px;font-size:10px;padding:4px 10px;';
        if (agent.status === 'fired') {
          btn.className = 'btn btn-success';
          btn.textContent = '↩ Reinstate';
          btn.addEventListener('click', function(){ rehireAgent(id); });
        } else {
          btn.className = 'btn btn-danger';
          btn.textContent = '⊘ Fire';
          btn.addEventListener('click', function(){ fireAgent(id); });
        }
        hdr.parentElement.appendChild(btn);
      }
    }
  }, 50);
};


// Company + Skills boot sequence — smart detection for returning users
async function bootCompanyLayer() {
  loadSkillsLibrary();

  // ── Primary: load companies from bridge (source of truth, tied to account) ─
  var bridgeCompanies = null;
  try {
    var resp = await bridgeGet('/companies');
    if (resp && resp.companies && resp.companies.length > 0) {
      bridgeCompanies = resp.companies;
    }
  } catch(e) { /* bridge offline — fall through to localStorage */ }

  if (bridgeCompanies) {
    bridgeCompanies = filterDeletedCompanies(bridgeCompanies);
    getDeletedCompanyIds().forEach(function(deletedId){ scrubDeletedCompanyEverywhere(deletedId); });
  }

  if (bridgeCompanies && bridgeCompanies.length) {
    // Merge logos from localStorage (stored browser-side only)
    var localList = getCompanies();
    bridgeCompanies = bridgeCompanies.map(function(co) {
      var local = localList.find(function(l){ return l.id === co.id; });
      if (local && local.logo) co.logo = local.logo;
      return co;
    });
    // Cache to localStorage (without _manifest)
    var localList = getCompanies();
    var merged = bridgeCompanies.map(function(c) {
        var local = localList.find(function(l){ return l.id === c.id; });
        var copy = Object.assign({}, c);
        if (local) {
            if (local._manifest)        copy._manifest        = local._manifest;
            if (local.default_provider) copy.default_provider = local.default_provider;
            if (local.default_model)    copy.default_model    = local.default_model;
            if (local.base_url)         copy.base_url         = local.base_url;
            if (local.logo)             copy.logo             = local.logo;
        }
        return copy;
    });
    try { localStorage.setItem(COMPANIES_KEY, JSON.stringify(merged.map(function(c){ var x = Object.assign({},c); delete x._manifest; return x; }))); } catch(e) {}
    _companiesCache = merged;
    // Always show onboarding — returning users see picker, new users see create form
    renderCompanyRail();
    showOnboarding();
    return;
  }

  // ── Fallback: bridge offline — use localStorage cache ─────────────────────
  var companies = filterDeletedCompanies(getCompanies());
  if (companies.length !== getCompanies().length) saveCompanies(companies);
  if (companies.length > 0) {
    renderCompanyRail();
    showOnboarding();
    return;
  }

  // ── No companies anywhere — show create form ──────────────────────────────
  showOnboarding();
}


// ═══════════════════════════════════════════════════════════════
// AUTH SYSTEM — Login / Signup / Session
// Session token stored in localStorage.
// Bridge validates token and scopes companies to logged-in user.
// ═══════════════════════════════════════════════════════════════
var SESSION_KEY   = 'rockoagents_session_v1';
var _currentUser  = null;
var _sessionToken = null;

