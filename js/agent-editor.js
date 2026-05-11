/* ============================================================
   AGENT EDITOR — open, save, delete, run, create
   ============================================================ */

function _setText(id,v){var el=document.getElementById(id);if(el)el.textContent=v||'';}
function openAgent(id){
  const a=RockoCore.getAgent(id); if(!a)return;
  currentAgentId=id; editorStatus=a.status||'idle'; editorConns=[...(a.connections||[])];
  const manifest=RockoCore.getProject(a.project);
  const _co = typeof getActiveCompany === 'function' ? getActiveCompany() : null;
  // Navigate first so elements are guaranteed visible/rendered
  setTab('agents',null);
  _setVal('editorName',a.name);
  _setVal('editorRole',a.role);
  _setVal('editorModel',(_co&&_co.default_model)||manifest?.model?.default_model||a.model||'');
  _setVal('editorModelOverride',a.model_override||'');
  _setVal('editorDesc',a.description);
  _setVal('editorInstructions',a.instructions||a._instructions||'');
  _setVal('editorStep',a.pipeline_step||'');
  _setVal('editorScriptPath',a.local_code?.script_path||'');
  _setVal('editorEntryPoint',a.local_code?.entry||a.local_code?.entry_point||'');
  _setVal('editorTags',(a.skills||a.tags||[]).join(', '));
  _setText('metaId',a.id);
  _setText('metaProject',a.project);
  _setText('metaProvider',a.provider_key||a.provider||'—');
  _setText('metaType',a.type||'prompt');
  const tKeys=Object.keys(a.tools||{});
  var tl=document.getElementById('toolsList');
  if(tl)tl.innerHTML=tKeys.length?tKeys.map(k=>'<span class="perm-badge '+(a.tools[k].enabled?'perm-ok':'perm-blocked')+'">'+k+'</span>').join(''):'<span style="color:var(--text-dim)">none</span>';
  const aKeys=Object.keys(a.apis||{});
  var al=document.getElementById('apisList');
  if(al)al.innerHTML=aKeys.length?aKeys.map(k=>'<span class="perm-badge perm-ok">'+k+'</span>').join(''):'<span style="color:var(--text-dim)">none</span>';
  document.querySelectorAll('.status-btn').forEach(b=>b.classList.toggle('sel',b.dataset.val===a.status));
  renderConnChips(editorConns);
  var sh=document.getElementById('saveHint');
  if(sh){sh.textContent='Saved';sh.style.color='';}
  renderSidebar();
}
function renderConnChips(conns){
  const area=document.getElementById('editorConnections');
  if(!area)return;
  const proj=RockoCore.getActiveProject();
  area.innerHTML=conns.map(c=>{
    const agent=RockoCore.getAgent(c);
    const label=agent?agent.name+' <span style="opacity:.5;font-size:10px">('+c+')</span>':c;
    return '<div class="conn-chip"><span>→ '+label+'</span><span class="remove" onclick="removeChip(&apos;'+c+'&apos;)">✕</span></div>';
  }).join('')+'<button class="conn-add" onclick="addConn()">+ Add Output</button>';
}
function removeChip(n){editorConns=editorConns.filter(c=>c!==n);renderConnChips(editorConns);}
function addConn(){
  const proj=RockoCore.getActiveProject();
  const agents=RockoCore.getAgents(proj).filter(a=>a.id!==currentAgentId);
  if(!agents.length){ alert('No other agents in this project to connect to.'); return; }
  const options=agents.map((a,i)=>(i+1)+'. '+a.name+' ('+a.id+')').join('\n');
  const input=prompt('Connect output to agent:\n\n'+options+'\n\nEnter agent ID:');
  const match=agents.find(a=>a.id===input||a.name.toLowerCase()===input?.toLowerCase());
  const val=match?match.id:input;
  if(val&&!editorConns.includes(val)){
    editorConns.push(val);
    renderConnChips(editorConns);
  }
}
function setEditorStatus(val,btn){editorStatus=val;document.querySelectorAll('.status-btn').forEach(b=>b.classList.remove('sel'));btn.classList.add('sel');markUnsaved();}
function markUnsaved(){document.getElementById('saveHint').textContent='● Unsaved';document.getElementById('saveHint').style.color='var(--yellow)';}
function _gEl(id){return document.getElementById(id);}
function saveAgent(){
  if(!currentAgentId)return;

  const h=_gEl('saveHint');
  const pipelineStep=(_gEl('editorStep')||{value:''}).value.trim();
  const instructions=(_gEl('editorInstructions')||{value:''}).value;
  const tagsRaw=(_gEl('editorTags')||{value:''}).value;
  const tagList=tagsRaw.split(',').map(s=>s.trim()).filter(Boolean);

  // Build updated local_code from editor fields (was entirely missing before)
  const scriptPath=(_gEl('editorScriptPath')||{value:''}).value.trim();
  const entryPoint=(_gEl('editorEntryPoint')||{value:''}).value.trim();
  const existingAgent=RockoCore.getAgent(currentAgentId);
  const existingLc=existingAgent&&existingAgent.local_code?existingAgent.local_code:{};
  const updatedLc=(scriptPath||entryPoint)
    ?{...existingLc,
       type:existingLc.type||'python_module',
       script_path:scriptPath||existingLc.script_path||null,
       entry:entryPoint||existingLc.entry||null,
       entry_point:entryPoint||existingLc.entry_point||null}
    :(existingAgent?existingAgent.local_code:null);

  RockoCore.updateAgent(currentAgentId,{
    name:(_gEl('editorName')||{value:''}).value,
    role:(_gEl('editorRole')||{value:''}).value,
    model_override:(_gEl('editorModelOverride')||{value:''}).value.trim()||null,
    description:(_gEl('editorDesc')||{value:''}).value,
    instructions:instructions,
    _instructions:instructions,
    pipeline_step:pipelineStep,
    status:editorStatus,
    connections:editorConns,
    outputs_to:editorConns,
    local_code:updatedLc,
    skills:tagList,
    tags:tagList,
    apis:[],
    project_tools:['filesystem','http']
  });

  // Patch manifest agent def so export/rebuild sees fresh instructions + local_code
  const proj=RockoCore.getActiveProject();
  const manifest=RockoCore.getProject(proj);
  if(manifest&&Array.isArray(manifest.agents)){
    const def=manifest.agents.find(d=>d&&d.id===currentAgentId);
    if(def){
      def._instructions=instructions;
      if(scriptPath||entryPoint) def.local_code=updatedLc;
    }
  }
  // Sync pipeline DAG
  if(manifest&&manifest.pipeline&&Array.isArray(manifest.pipeline.execution_order)){
    const step=manifest.pipeline.execution_order.find(s=>s.agent_id===currentAgentId);
    if(step){step.outputs_to=editorConns;if(pipelineStep)step.step_id=pipelineStep;}
  }

  if(h){h.textContent='✓ Saved';h.style.color='var(--green)';}
  refreshAll();
  setTimeout(()=>{if(h){h.textContent='All saved';h.style.color='';}},2000);
}
function deleteCurrentAgent(){
  const a = RockoCore.getAgent(currentAgentId);
  if(!a || !confirm('Delete "' + a.name + '"?')) return;

  const projectName = a.project || RockoCore.getActiveProject();

  RockoCore.deleteAgent(currentAgentId);
  currentAgentId = null;

  if(projectName){
    syncCompanyAgentCache(projectName);
    RockoCore.saveState();
  }

  refreshAll();
}
function confirmDelete(id){
  const a = RockoCore.getAgent(id);
  if(!a || !confirm('Delete "' + a.name + '"?')) return;

  const projectName = a.project || RockoCore.getActiveProject();

  RockoCore.deleteAgent(id);

  if(currentAgentId === id) currentAgentId = null;

  if(projectName){
    syncCompanyAgentCache(projectName);
    RockoCore.saveState();
  }

  refreshAll();
}
function duplicateAgent(){
  const a=RockoCore.getAgent(currentAgentId);if(!a)return;
  const def={id:'agent_'+Date.now(),name:a.name+' (Copy)',role:a.role,type:a.type,instruction_file:a.instruction_file,
    pipeline_step:a.pipeline_step,status:'idle',project_tools:Object.keys(a.tools||{}),apis:Object.keys(a.apis||{}),description:a.description,created:new Date().toISOString().split('T')[0]};
  RockoCore.createAgent(a.project,def,a.instructions);
}
function createTaskFromAgent(){
  if(!currentAgentId)return;
  const a=RockoCore.getAgent(currentAgentId);if(!a)return;
  populateTaskAgentSelect(currentAgentId);
  document.getElementById('taskName').value='Task for '+a.name;
  document.getElementById('taskDesc').value='';
  openModal('createTaskModal');
}
function taskOk(msg) {
  if (typeof toastOk === 'function') toastOk(msg || 'Done');
  else console.log(msg || 'Done');
}

function taskErr(msg) {
  if (typeof toastErr === 'function') toastErr(msg || 'Error');
  else console.error(msg || 'Error');
}
function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}
function extractAgentText(result){
  if(!result)return '';

  if(typeof result === 'string')return result;

  if(Array.isArray(result.content)){
    var block = result.content.find(function(b){return b && b.type === 'text';});
    if(block && block.text)return block.text;
  }

  return result.text || result.response || result.output || result.message || '';
}

function buildAgentRunPrompt(agent,userText){
  var projectName = agent.project || RockoCore.getActiveProject() || 'Unknown project';
  var agents = RockoCore.getAgents(projectName) || [];
  var company = typeof getActiveCompany === 'function' ? getActiveCompany() : null;

  var context = {
    company: company ? {
      name: company.display_name || company.id || projectName,
      description: company.description || '',
      default_provider: company.default_provider || '',
      default_model: company.default_model || ''
    } : {
      name: projectName,
      description: '',
      default_provider: '',
      default_model: ''
    },
    active_agent: {
      id: agent.id,
      name: agent.name,
      role: agent.role,
      provider: agent.provider,
      model: agent.model,
      pipeline_step: agent.pipeline_step
    },
    current_agents: agents.map(function(a){
      return {
        id:a.id,
        name:a.name,
        role:a.role,
        status:a.status,
        pipeline_step:a.pipeline_step
      };
    })
  };

  return [
    'You are responding inside the RockoAgents agent chat UI.',
    '',
    'CRITICAL RESPONSE RULES:',
    '- Do not invent product candidates, scores, spend, ROAS, campaign results, run history, approvals, or budget numbers.',
    '- Only use facts present in the context below or in the user message.',
    '- If information is missing, say exactly what is missing.',
    '- Return a clean human-readable message, not raw JSON, unless the user specifically asks for JSON.',
    '- Keep the answer useful, direct, and operational.',
    '',
    'REQUIRED FORMAT:',
    'Status:',
    'Known Facts:',
    'Missing Information:',
    'Next Action:',
    'Needs Approval:',
    '',
    'CURRENT ROCKOAGENTS CONTEXT:',
    JSON.stringify(context,null,2),
    '',
    'USER MESSAGE:',
    userText
  ].join('\n');
}

function renderAgentThinking(log,agentName){
  if(!log)return null;

  var id='agentThinking_'+Date.now();

  log.innerHTML += ''
    + '<div class="agent-chat-msg agent" id="'+id+'">'
    + '<b>'+escapeHtml(agentName || 'Agent')+'</b><br>'
    + '<div style="margin-top:8px;color:var(--text-dim);">Thinking...</div>'
    + '<div style="height:6px;border:1px solid var(--border);margin-top:10px;overflow:hidden;background:var(--bg-card);">'
    + '<div style="height:100%;width:35%;background:var(--green);animation:agentLoadBar 1s infinite alternate;"></div>'
    + '</div>'
    + '</div>';

  log.scrollTop=log.scrollHeight;
  return document.getElementById(id);
}

function setAgentRunButtonsBusy(isBusy){
  document.querySelectorAll('button[onclick="runAgentNow()"]').forEach(function(btn){
    btn.disabled=!!isBusy;
    btn.classList.toggle('btn-loading',!!isBusy);
  });
}

async function runAgentNow(){
  if(!currentAgentId)return;

  var a = RockoCore.getAgent(currentAgentId);
  if(!a)return;

  var box = document.getElementById('agentChatInput');
  var log = document.getElementById('agentChatLog');

  var visibleMsg = box && box.value.trim()
    ? box.value.trim()
    : 'Give me your current status using only known RockoAgents context.';

  var modelMsg = buildAgentRunPrompt(a,visibleMsg);

  if(box)box.value = '';

  if(log){
    log.innerHTML += '<div class="agent-chat-msg user"><b>You</b><br>' + escapeHtml(visibleMsg) + '</div>';
    log.scrollTop = log.scrollHeight;
  }

  var thinkingEl = renderAgentThinking(log,a.name || currentAgentId);
  setAgentRunButtonsBusy(true);

  try{
    var t0 = Date.now();

    RockoCore.updateAgent(currentAgentId,{status:'active'});
    refreshAll();

    var result = await RockoCore.callAgent(currentAgentId,modelMsg);
    var text = extractAgentText(result);

    if(!text){
      throw new Error('Agent returned no response. Check selected provider/model configuration.');
    }

    RockoCore.updateAgent(currentAgentId,{status:'idle'});

    if(thinkingEl){
      thinkingEl.outerHTML = '<div class="agent-chat-msg agent"><b>' + escapeHtml(a.name || currentAgentId) + '</b><br>' + escapeHtml(text) + '</div>';
    }else if(log){
      log.innerHTML += '<div class="agent-chat-msg agent"><b>' + escapeHtml(a.name || currentAgentId) + '</b><br>' + escapeHtml(text) + '</div>';
    }

    if(log)log.scrollTop = log.scrollHeight;

    taskOk((a.name || 'Agent') + ' responded in ' + (Date.now() - t0) + 'ms');
  }catch(err){
    RockoCore.updateAgent(currentAgentId,{status:'idle'});

    if(thinkingEl){
      thinkingEl.outerHTML = '<div class="agent-chat-msg error"><b>Error</b><br>' + escapeHtml(err.message || String(err)) + '</div>';
    }else if(log){
      log.innerHTML += '<div class="agent-chat-msg error"><b>Error</b><br>' + escapeHtml(err.message || String(err)) + '</div>';
    }

    if(log)log.scrollTop = log.scrollHeight;

    taskErr('Agent run failed: ' + (err.message || err));
  }

  setAgentRunButtonsBusy(false);
  refreshAll();
}

async function runAgentCard(id){
  var a = RockoCore.getAgent(id);
  if(!a)return;

  openAgent(id);
  setTimeout(function(){
    runAgentNow();
  },100);
}
function openNewAgentModal(){
  ['newAgentName','newAgentRole','newAgentDesc','newAgentStep'].forEach(function(id){
    var el = document.getElementById(id);
    if(el) el.value = '';
  });
  openModal('newAgentModal');
}
function createNewAgent(){
  const name = document.getElementById('newAgentName').value.trim();
  if(!name){
    alert('Name required');
    return;
  }

  const roleInput = document.getElementById('newAgentRole');
  const descInput = document.getElementById('newAgentDesc');
  const stepInput = document.getElementById('newAgentStep');

  const role = (roleInput && roleInput.value.trim()) ? roleInput.value.trim() : 'Agent';

  const desc = descInput ? descInput.value.trim() : '';
  const slug = name.toLowerCase().replace(/[^a-z0-9]/g,'_').replace(/^_+|_+$/g,'') || 'agent';
  const proj = RockoCore.getActiveProject();

  const def = {
    id:'agent_' + Date.now(),
    name:name,
    display_name:name,
    role:role,
    type:'prompt',
    instruction_file:'agents\\' + slug + '\\AGENT.md',
    pipeline_step:(stepInput && stepInput.value.trim()) ? stepInput.value.trim() : slug + '_step',
    status:'idle',
    description:desc,
    project_tools:[],
    apis:[],
    created:new Date().toISOString().split('T')[0]
  };

  const instructions =
    '# ' + name + '\n\n' +
    '## Role\n' + role + '\n\n' +
    '## Inputs\n\n' +
    '## Outputs\n\n' +
    '## Behavior\n\n' +
    '## Integration';

  RockoCore.createAgent(proj,def,instructions);
  closeModal('newAgentModal');
  refreshAll();
}

// ─────────────────────────────────────────────────────────
// TASK SYSTEM
// ─────────────────────────────────────────────────────────
function setTaskFilter(val,btn){
