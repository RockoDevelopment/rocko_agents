/* ============================================================
   ROCKO CORE PLATFORM v4.0
   RockoCore — agent runtime, state, events
   ============================================================ */

/**
 * RockoAgents Core Platform v4.0
 * Added: task system, permissions, run history, approval gates, agent sync, file persistence
 */
const RockoCore = (() => {
  let _projects={}, _agents={}, _active=null, _tasks={}, _taskSeq=0;
  let _runHistory=[], _runCtx=null, _pendingApproval=null, _approvalResolve=null;
  let _logFns=[], _renderFns={}, _bridgeUrl='http://localhost:8787', _bridgeOk=false;

  function log(type,msg){const e={type,msg,ts:new Date().toLocaleTimeString('en-US',{hour12:false})};_logFns.forEach(f=>f(e));}
  function onLog(fn){_logFns.push(fn);}
  function onRender(ev,fn){_renderFns[ev]=fn;}
  function emit(ev,d){if(_renderFns[ev])_renderFns[ev](d);}

  // ── Persistence ──────────────────────────────────────────────
  const SK='rockoagents_v4';
  function saveState(){
    try{
      const s={active:_active,projects:_projects,agents:_agents,tasks:_tasks,taskSeq:_taskSeq,
               runHistory:_runHistory.slice(0,50),saved_at:new Date().toISOString()};
      localStorage.setItem(SK,JSON.stringify(s));
      _syncToFile(s);
    }catch(e){log('warn','Persist error: '+e.message);}
  }
  async function _syncToFile(state){
    if(!_bridgeOk)return;
    try{await fetch(`${_bridgeUrl}/data/save`,{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({key:'rockoagents_state',data:state}),signal:AbortSignal.timeout(3000)});}catch{}
  }
  function loadState(){
    try{
      const raw=localStorage.getItem(SK); if(!raw)return false;
      const s=JSON.parse(raw); if(!s.projects)return false;
      _projects=s.projects||{};_agents=s.agents||{};_tasks=s.tasks||{};
      _taskSeq=s.taskSeq||0;_runHistory=s.runHistory||[];_active=s.active;
      log('success',`State restored (${(s.saved_at||'').split('T')[0]})`); return true;
    }catch(e){log('warn','Load error: '+e.message);return false;}
  }
  function clearState(){
    localStorage.removeItem(SK);
    _projects={};_agents={};_tasks={};_taskSeq=0;_runHistory=[];_active=null;
    log('system','State cleared');
  }
  function getLastSaved(){try{return JSON.parse(localStorage.getItem(SK)||'{}').saved_at||null;}catch{return null;}}

  function asList(v) {
    if (!v) return [];
    if (Array.isArray(v)) return v;
    if (typeof v === 'object') return Object.keys(v);
    if (typeof v === 'string') return v.split(',').map(s => s.trim()).filter(Boolean);
    return [];
  }

  // ── Validation ───────────────────────────────────────────────
  function validateManifest(manifest){
    const errors=[],warns=[],info=[];
    if(!manifest.schema_version)errors.push('Missing: schema_version');
    if(!manifest.project?.id)errors.push('Missing: project.id');
    if(!manifest.project?.name)errors.push('Missing: project.name');
    if(!manifest.project?.root_path)errors.push('Missing: project.root_path');
    if(!manifest.model?.default_provider)errors.push('Missing: model.default_provider');
    if(!manifest.model?.default_model)errors.push('Missing: model.default_model');
    if(!Array.isArray(manifest.agents))errors.push('Missing: agents array');
    if(!manifest.pipeline?.execution_order)errors.push('Missing: pipeline.execution_order');
    const ids=new Set();
    (manifest.agents||[]).forEach(a=>{
      if(!a.id)errors.push('Agent missing id');
      else if(ids.has(a.id))errors.push('Duplicate agent id: '+a.id);
      else ids.add(a.id);
      if(!a.name)errors.push('Agent "'+a.id+'": missing name');
      if(!a.instruction_file)errors.push('Agent "'+a.id+'": missing instruction_file');
      if(!a.pipeline_step)warns.push('Agent "'+a.id+'": no pipeline_step');
      asList(a.apis).forEach(api => {
    if (!manifest.apis?.[api]) {
        warns.push('Agent "' + a.id + '": api "' + api + '" not in registry');
      }
    });

    asList(a.project_tools).forEach(t => {
      if (!manifest.tools?.[t]) {
        warns.push('Agent "' + a.id + '": tool "' + t + '" not in registry');
      }
    });
    });
    Object.entries(manifest.executors||{}).forEach(([id,ex])=>{
      if(!ex.script_path&&!ex.entry)errors.push('Executor "'+id+'": missing script_path or entry');
    });
    (manifest.pipeline?.execution_order||[]).forEach(step=>{
      if(!step.step_id)errors.push('Pipeline step missing step_id');
      if(step.type==='agent'&&step.agent_id&&!ids.has(step.agent_id))
        errors.push('Pipeline step "'+step.step_id+'": agent_id "'+step.agent_id+'" not defined');
    });
    (manifest.env?.required||[]).forEach(v=>info.push('Env var required: '+v));
    return{valid:errors.length===0,errors,warns,info};
  }

  function getProjectHealth(proj){
    const manifest=_projects[proj];
    if(!manifest)return{status:'not_loaded',agents:[],executors:[]};
    const v=validateManifest(manifest);
    return{
      status:v.valid?'ready':'errors',errors:v.errors,warns:v.warns,info:v.info,
      agents:(manifest.agents||[]).map(a=>({
        id:a.id,name:a.name,enabled:a.enabled!==false,
        hasInstructions:!!(_agents[a.id]?.instructions?.trim()),
        status:_agents[a.id]?.status||'idle',
        permittedTools:a.project_tools||[],permittedApis:a.apis||[]
      })),
      executors:Object.entries(manifest.executors||{}).map(([id,ex])=>({
        id,label:ex.label||id,scriptPath:ex.script_path,runMode:ex.run_mode,
        bridgeRequired:ex.run_mode!=='none',allowedOutsideRoot:ex.allow_outside_root===true
      })),
      bridgeNeeded:Object.values(manifest.executors||{}).some(e=>e.run_mode!=='none')
    };
  }

  // ── Permissions ──────────────────────────────────────────────
  function checkPermission(agentId,{tool=null,api=null,executor=null}={}){
    const agent=_agents[agentId];
    if(!agent)return{allowed:false,reason:'Agent "'+agentId+'" not found'};
    const manifest=_projects[agent.project];
    if(!manifest)return{allowed:false,reason:'Project manifest not loaded'};
    if(tool){
      const allowed=Object.keys(agent.tools||{});
      if(!allowed.includes(tool))return{allowed:false,reason:'Tool "'+tool+'" not permitted for "'+agent.name+'". Allowed: '+(allowed.join(', ')||'none')};
      if(agent.tools[tool]&&!agent.tools[tool].enabled)return{allowed:false,reason:'Tool "'+tool+'" is disabled'};
    }
    if(api){
      const allowed=Object.keys(agent.apis||{});
      if(!allowed.includes(api))return{allowed:false,reason:'API "'+api+'" not permitted for "'+agent.name+'". Allowed: '+(allowed.join(', ')||'none')};
    }
    if(executor){
      const ex=manifest.executors?.[executor];
      if(!ex)return{allowed:false,reason:'Executor "'+executor+'" not defined'};
      if(!['ceo','engine'].includes(agent.role))return{allowed:false,reason:'Role "'+agent.role+'" cannot trigger executors'};
    }
    return{allowed:true};
  }

  // ── Agent Sync ───────────────────────────────────────────────
  async function syncAgentsFromManifest(projectName){
    const manifest=_projects[projectName];
    if(!manifest)return{success:false,error:'Project not found'};
    const results={created:[],updated:[],missing_files:[],unchanged:[]};
    for(const def of(manifest.agents||[])){
      const existing=_agents[def.id];
      let instructions=existing?.instructions||def._instructions||'';
      if(!instructions&&_bridgeOk){
        try{
          const res=await fetch(`${_bridgeUrl}/file/read`,{method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({path:def.instruction_file,project:projectName}),signal:AbortSignal.timeout(3000)});
          if(res.ok){const d=await res.json();instructions=d.content||'';}
        }catch{}
      }
      if(!instructions)results.missing_files.push({id:def.id,name:def.name,file:def.instruction_file});
      if(existing){
        updateAgent(def.id,{name:def.name,role:def.role,type:def.type||existing.type,
          description:def.description||existing.description,pipeline_step:def.pipeline_step,
          ...(instructions&&instructions!==existing.instructions?{instructions}:{})});
        results.updated.push(def.id);
      }else{
        const agent=_buildAgent(def,manifest,instructions);
        _agents[agent.id]=agent;
        if(!manifest.agents.find(a=>a.id===def.id))manifest.agents.push(def);
        results.created.push(def.id);
      }
    }
    saveState();
    log('success','Agent sync: +'+results.created.length+' created, ~'+results.updated.length+' updated, '+results.missing_files.length+' missing files');
    emit('agentsSynced',{projectName,results});
    return{success:true,results};
  }

  // ── Task System ───────────────────────────────────────────────
  function createTask({name,description='',agentId,projectName,input={},parentId=null,priority='normal'}){
    const id='task_'+Date.now()+'_'+(_taskSeq++);
    const task={id,name,description,agentId,projectName,input,parentId,priority,
      status:'queued',output:null,error:null,steps:[],runId:null,retryCount:0,
      created:new Date().toISOString(),updated:new Date().toISOString()};
    _tasks[id]=task; saveState();
    log('info','Task queued: "'+name+'" → '+agentId);
    emit('taskCreated',{task}); return task;
  }
  function updateTask(id,updates){
    if(!_tasks[id])return false;
    Object.assign(_tasks[id],updates,{updated:new Date().toISOString()});
    saveState(); emit('taskUpdated',{task:_tasks[id]}); return true;
  }
  function deleteTask(id){
    const t=_tasks[id]; if(!t)return false;
    delete _tasks[id]; saveState();
    log('warn','Task deleted: '+t.name); emit('taskDeleted',{taskId:id}); return true;
  }
  async function runTask(taskId){
    const task=_tasks[taskId];
    if(!task){log('error','Task not found: '+taskId);return null;}
    if(!task.agentId){updateTask(taskId,{status:'failed',error:'No agent assigned'});return null;}
    const perm=checkPermission(task.agentId,{});
    if(!perm.allowed){updateTask(taskId,{status:'failed',error:perm.reason});emit('taskStatusChanged',{taskId,status:'failed'});return null;}
    const runId='run_task_'+Date.now();
    updateTask(taskId,{status:'running',runId,error:null});
    emit('taskStatusChanged',{taskId,status:'running'});
    const t0=Date.now();
    const ctx=Object.keys(task.input).length?('Context:\n'+JSON.stringify(task.input,null,2)+'\n\nTask: '+(task.description||task.name)):(task.description||task.name);
    const result=await callAgent(task.agentId,ctx);
    const dur=Date.now()-t0;
    if(result){
      const text=result.content?.find(b=>b.type==='text')?.text||'';
      let parsed=null;try{parsed=JSON.parse(text);}catch{parsed={text};}
      updateTask(taskId,{status:'complete',output:text,outputParsed:parsed,durationMs:dur});
      emit('taskStatusChanged',{taskId,status:'complete'});
      log('success','Task complete: "'+task.name+'" ('+dur+'ms)');
    }else{
      updateTask(taskId,{status:'failed',error:'Agent returned no result',durationMs:dur});
      emit('taskStatusChanged',{taskId,status:'failed'});
    }
    return _tasks[taskId];
  }
  async function retryTask(taskId){
    const task=_tasks[taskId]; if(!task)return null;
    if(!['failed','blocked'].includes(task.status)){log('warn','Cannot retry: '+task.status);return null;}
    updateTask(taskId,{status:'queued',error:null,output:null,retryCount:(task.retryCount||0)+1});
    log('info','Task retry #'+(task.retryCount+1)+': '+task.name);
    return runTask(taskId);
  }
  function blockTask(id,reason){updateTask(id,{status:'blocked',error:reason});emit('taskStatusChanged',{taskId:id,status:'blocked'});}
  function getTasks(projectName=null,status=null){
    return Object.values(_tasks).filter(t=>(!projectName||t.projectName===projectName)&&(!status||t.status===status))
      .sort((a,b)=>new Date(b.created)-new Date(a.created));
  }
  function getTask(id){return _tasks[id]||null;}
  function getSubTasks(parentId){return Object.values(_tasks).filter(t=>t.parentId===parentId);}

  // ── Run History ───────────────────────────────────────────────
  function _archiveRun(ctx){
    _runHistory.unshift({...JSON.parse(JSON.stringify(ctx)),archived_at:new Date().toISOString()});
    if(_runHistory.length>50)_runHistory=_runHistory.slice(0,50);
    saveState(); emit('runHistoryUpdated',{runs:_runHistory});
  }
  function getRunHistory(proj=null){
    const pid=proj?_projects[proj]?.project?.id:null;
    return pid?_runHistory.filter(r=>r.project_id===pid):_runHistory;
  }
  function getRunById(runId){return _runHistory.find(r=>r.run_id===runId)||(_runCtx?.run_id===runId?_runCtx:null);}
  function exportRunReport(runId){
    const run=getRunById(runId); if(!run)return null;
    const lines=['Rocko Development Run Report','Run ID: '+run.run_id,'Project: '+run.project_id,
      'Status: '+run.status,'Started: '+run.started_at,'Completed: '+(run.completed_at||'—'),'','STEPS:','------'];
    Object.entries(run.steps||{}).forEach(([sid,step])=>{
      lines.push('','['+sid+']','  Status: '+step.status,'  Duration: '+step.duration_ms+'ms');
      if(step.error)lines.push('  Error: '+step.error);
      if(step.raw_text)lines.push('  Output: '+step.raw_text.slice(0,200)+(step.raw_text.length>200?'...':''));
    });
    return{text:lines.join('\n'),json:run};
  }

  // ── Approval Gates ────────────────────────────────────────────
  function _waitForApproval(step,context){
    return new Promise(resolve=>{
      _pendingApproval={step,context,timestamp:new Date().toISOString()};
      _approvalResolve=resolve;
      emit('approvalRequired',{step,context});
      log('warn','⏸ APPROVAL REQUIRED: '+step.label);
    });
  }
  function resolveApproval(decision,modifications=null){
    if(!_pendingApproval||!_approvalResolve)return false;
    const resolve=_approvalResolve;
    _pendingApproval=null;_approvalResolve=null;
    resolve({decision,modifications});
    emit('approvalResolved',{decision,modifications});
    log(decision==='approve'?'success':'warn','Approval: '+decision.toUpperCase());
    return true;
  }
  function getPendingApproval(){return _pendingApproval;}

  // ── Project + Agent Builder ───────────────────────────────────
  function loadProject(manifest,instructionMap={}){
    const v=validateManifest(manifest);
    if(!v.valid){v.errors.forEach(e=>log('error','[VALIDATION] '+e));return{success:false,errors:v.errors};}
    v.warns.forEach(w=>log('warn','[VALIDATION] '+w));
    const name=manifest.project.name;
    _projects[name]=manifest;
    const agents=(manifest.agents||[]).map(def=>_buildAgent(def,manifest,instructionMap[def.id]||def._instructions||''));
    agents.forEach(a=>{_agents[a.id]=a;});
    if(!_active)_active=name;
    saveState();
    log('success','Project loaded: '+name+' ('+agents.length+' agents)');
    emit('projectLoaded',{projectName:name,agents,manifest});
    return{success:true,projectName:name,agents};
  }
  function _buildAgent(def,manifest,instructions){
    const root=manifest.project.root_path;
    const _activeCo = typeof getActiveCompany === 'function' ? getActiveCompany() : null;
    const _coProvider = _activeCo ? (_activeCo.default_provider || null) : null;
    const pk = (def.model_provider && def.model_provider !== '__company_default__')
      ? def.model_provider
      : (_coProvider || manifest.model?.default_provider || '__unset__');
    const pc=manifest.model?.providers?.[pk]||{};
    const apis={};asList(def.apis).forEach(k=>{const a=manifest.apis?.[k];if(a)apis[k]={...a,base_url:(a.base_url||'').replace('{{PROJECT_ROOT}}',root)};});
    const tools={};asList(def.project_tools).forEach(k=>{const t=manifest.tools?.[k];if(t)tools[k]={...t,path:(t.path||'').replace('{{PROJECT_ROOT}}',root)};});
    let lc=null;if(def.local_code?.type){lc={...def.local_code,script_path:def.local_code.script_path?`${root}\${def.local_code.script_path}`:null,working_dir:def.local_code.working_dir?`${root}\${def.local_code.working_dir}`:null};}
    return{id:def.id,name:def.name,display_name:def.display_name||def.name,role:def.role,type:def.type||'prompt',
      model:def.model_override||((_activeCo&&_activeCo.default_model)||manifest.model?.default_model)||'',provider:pk,provider_key:pk,
      api_base:pc.api_base||'',instructions,pipeline_step:def.pipeline_step,
      status:def.status||'idle',description:def.description||'',tools,apis,local_code:lc,
      tags:def.tags||[],project:manifest.project.name,instruction_file:def.instruction_file,
      model_override:def.model_override||null,created:def.created||new Date().toISOString().split('T')[0],
      edited:new Date().toISOString().split('T')[0]};
  }

  // ── Model Layer ──────────────────────────────────────────────
  async function _callProvider(agent,messages){

    // ── Guard: provider required ─────────────────────────────────────────────
    if(!agent.provider || agent.provider==='__unset__' || agent.provider==='later'){
      throw new Error('No provider configured. Select a provider in company settings.');
    }

    // ── Path 1: Local script via bridge ──────────────────────────────────────
    // Browser cannot spawn processes — local scripts must route through the bridge server.
    const scriptPath = agent.local_code?.script_path || agent.local_script_path || '';
    if(scriptPath){
      if(!_bridgeOk){
        throw new Error('Local script agent requires the bridge to be online. Start the RockoAgentHub bridge server.');
      }
      let res;
      try{
        res = await fetch(`${_bridgeUrl}/agent/exec`,{
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({
            agent_id: agent.id,
            script_path: scriptPath,
            entry_point: agent.local_code?.entry || agent.local_code?.entry_point || '',
            instructions: agent.instructions || '',
            messages
          }),
          signal: AbortSignal.timeout(120000)
        });
      }catch(fetchErr){
        if(fetchErr.name==='TimeoutError'||fetchErr.name==='AbortError'){
          throw new Error('Local script timed out after 120 seconds. Script path: ' + scriptPath);
        }
        throw new Error('Cannot reach bridge for local script execution: ' + fetchErr.message);
      }
      if(!res.ok){
        const errText = await res.text().catch(()=>'');
        throw new Error('Bridge exec error ' + res.status + (errText ? ': ' + errText.slice(0,200) : ''));
      }
      const data = await res.json();
      if(data.error) throw new Error(data.error.message || JSON.stringify(data.error));
      return {
        content:[{type:'text', text: data.text || data.output || data.result || ''}],
        usage:{output_tokens: data.usage?.output_tokens || 0}
      };
    }

    // ── Guard: model required for all LLM paths ──────────────────────────────
    if(!agent.model){
      throw new Error('No model set for agent "' + (agent.name||agent.id) + '". Set a model override or configure the company default model.');
    }

    // ── Path 2: Anthropic ────────────────────────────────────────────────────
    if(agent.provider==='anthropic'){
      const co = typeof getActiveCompany === 'function' ? getActiveCompany() : null;
      const apiKey = agent.api_key || (co && co.api_key) || '';
      if(!apiKey) throw new Error('Anthropic API key not configured. Add it in company settings.');

      const res = await fetch(`${agent.api_base}/messages`,{
        method:'POST',
        headers:{
          'Content-Type':'application/json',
          'x-api-key': apiKey,
          'anthropic-version':'2023-06-01'
        },
        body:JSON.stringify({
          model: agent.model,
          max_tokens: 1000,
          system: agent.instructions || 'You are a helpful agent.',
          messages
        }),
        signal: AbortSignal.timeout(60000)
      });
      if(!res.ok){
        const errText = await res.text().catch(()=>'');
        throw new Error('Anthropic API error ' + res.status + (errText ? ': ' + errText.slice(0,200) : ''));
      }
      const data = await res.json();
      if(data.error) throw new Error(data.error.message || JSON.stringify(data.error));
      return data;
    }

    // ── Path 3: OpenAI-compatible (Ollama, LMStudio, etc.) ──────────────────
    const openAICompatibleProviders = ['openai_compatible','local','lmstudio','custom','free_proxy','openai','nvidia','gemini'];

    if(openAICompatibleProviders.includes(agent.provider)){
      const base = agent.api_base || (
        agent.provider === 'local'      ? 'http://localhost:11434/v1' :
        agent.provider === 'lmstudio'   ? 'http://localhost:1234/v1'  :
        agent.provider === 'free_proxy' ? 'http://localhost:8082/v1'  :
        ''
      );
      if(!base) throw new Error('Provider "' + agent.provider + '" has no API base URL configured.');

      let res;
      try{
        res = await fetch(`${base}/chat/completions`,{
          method:'POST',
          headers:{'Content-Type':'application/json'},
          body:JSON.stringify({
            model: agent.model,
            messages:[{role:'system', content:agent.instructions||''},...messages]
          }),
          signal: AbortSignal.timeout(120000)
        });
      }catch(fetchErr){
        if(fetchErr.name==='TimeoutError'||fetchErr.name==='AbortError'){
          throw new Error(
            agent.provider==='local'
              ? 'Ollama timed out. Make sure the model is installed: ollama pull ' + agent.model
              : agent.provider + ' timed out after 120 seconds.'
          );
        }
        throw new Error(
          agent.provider==='local'
            ? 'Cannot reach Ollama at ' + base + '. Start it with: ollama serve'
            : 'Cannot reach ' + agent.provider + ' at ' + base + ': ' + fetchErr.message
        );
      }

      if(!res.ok){
        const errText = await res.text().catch(()=>'');
        throw new Error(agent.provider.toUpperCase() + ' error ' + res.status + (errText ? ': ' + errText.slice(0,200) : ''));
      }
      const data = await res.json();
      if(data.error) throw new Error(data.error.message || JSON.stringify(data.error));

      return {
        content:[{type:'text', text:data.choices?.[0]?.message?.content||''}],
        usage:{output_tokens:data.usage?.completion_tokens||0}
      };
    }

    throw new Error('Unknown provider: "' + agent.provider + '"');
  }
  async function callAgent(agentId,userMessage,history=[]){
    const agent=_agents[agentId];
    if(!agent){
      log('error','Agent not found: '+agentId);
      throw new Error('Agent not found: '+agentId);
    }

    agent.status='active';
    emit('agentStatusChanged',{agentId,status:'active'});
    log('system','▶ '+agent.name+' ['+agent.provider+'/'+agent.model+']');

    const t0=Date.now();

    try{
      const result=await _callProvider(agent,[...history,{role:'user',content:userMessage}]);

      agent.status='idle';
      emit('agentStatusChanged',{agentId,status:'idle'});
      saveState();

      log('success','✓ '+agent.name+' ('+(Date.now()-t0)+'ms, '+(result.usage?.output_tokens||'?')+'t)');
      return{...result,_duration_ms:Date.now()-t0};
    }catch(err){
      agent.status='idle';
      emit('agentStatusChanged',{agentId,status:'idle'});
      saveState();

      log('error','✕ '+agent.name+': '+err.message);
      throw err;
    }
  }

  // ── Bridge ───────────────────────────────────────────────────
  function setBridgeUrl(url){_bridgeUrl=url;}
  function isBridgeOnline(){return _bridgeOk;}
  async function checkBridge(){
    try{const res=await fetch(`${_bridgeUrl}/health`,{signal:AbortSignal.timeout(2500)});
      _bridgeOk=res.ok;const data=_bridgeOk?await res.json():null;emit('bridgeStatus',{ok:_bridgeOk,data});return _bridgeOk;
    }catch{_bridgeOk=false;emit('bridgeStatus',{ok:false,data:null});return false;}
  }
  async function runExecutorViaBridge(executorId,context,agentId=null){
    if(!_bridgeOk){log('warn','Bridge offline — skipping: '+executorId);return{ok:false,skipped:true,reason:'bridge_offline'};}
    if(agentId){
      const perm=checkPermission(agentId,{executor:executorId});
      if(!perm.allowed){log('error','Executor blocked: '+perm.reason);return{ok:false,skipped:false,reason:perm.reason,permission_denied:true};}
    }
    try{
      log('system','⚡ Bridge: "'+executorId+'"'); const t0=Date.now();
      const res=await fetch(`${_bridgeUrl}/run/${executorId}`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({context,input:context})});
      const data=await res.json();
      log((data.ok||data.success)?'success':'error','⚡ '+executorId+': exit='+(data.exit_code??data.return_code??'?')+' ('+(Date.now()-t0)+'ms)');
      return data;
    }catch(err){log('error','⚡ Bridge error "'+executorId+'": '+err.message);return{ok:false,error:err.message};}
  }

  // ── Pipeline ─────────────────────────────────────────────────
  function _newCtx(proj,input){return{project_id:_projects[proj]?.project?.id||proj,run_id:'run_'+Date.now(),started_at:new Date().toISOString(),status:'running',steps:{},current_context:{...input}};}
  function _stepStatus(ctx,stepId,status,output=null,rawText='',durationMs=0,error=null){
    ctx.steps[stepId]={status,output,raw_text:rawText,duration_ms:durationMs,error,ts:new Date().toISOString()};
    if(output)Object.assign(ctx.current_context,output);
    emit('pipelineStepUpdate',{run_id:ctx.run_id,step_id:stepId,status,output,duration_ms:durationMs});
  }
  async function _executeStep(step,manifest,projectName){
    _stepStatus(_runCtx,step.step_id,'running');
    log('info','  → ['+step.type.toUpperCase()+'] '+step.label);
    const t0=Date.now();

    if(step.requires_approval){
      _stepStatus(_runCtx,step.step_id,'pending_approval');
      const approval=await _waitForApproval(step,{..._runCtx.current_context});
      if(approval.decision==='reject'){
        _stepStatus(_runCtx,step.step_id,'rejected',null,'',Date.now()-t0,'Rejected by operator');
        _runCtx.status='rejected';_runCtx.completed_at=new Date().toISOString();
        emit('pipelineHalted',{step,reason:'operator_rejected',run_ctx:_runCtx});
        return;
      }
      if(approval.modifications)Object.assign(_runCtx.current_context,approval.modifications);
      log('success','  ✓ Approved: '+step.label);
    }

    if(step.type==='agent'&&step.agent_id){
      const result=await callAgent(step.agent_id,JSON.stringify(_runCtx.current_context));
      const dur=Date.now()-t0;
      if(!result){
        _stepStatus(_runCtx,step.step_id,'error',null,'',dur,'Agent call failed');
        if(manifest.pipeline?.on_error==='halt_and_log'){
          _runCtx.status='halted';_runCtx.completed_at=new Date().toISOString();
          emit('pipelineHalted',{step,run_ctx:_runCtx});
        }
      }else{
        const raw=result.content?.find(b=>b.type==='text')?.text||'';
        let parsed=null;try{parsed=JSON.parse(raw);}catch{parsed={text:raw};}
        _stepStatus(_runCtx,step.step_id,'complete',{[step.step_id]:parsed},raw,dur);
        log('success','  ✓ '+step.label);
      }
    }else if(step.type==='executor'&&step.executor_id){
      const ceo=Object.values(_agents).find(a=>a.project===projectName&&a.role==='ceo');
      const r=await runExecutorViaBridge(step.executor_id,_runCtx.current_context,ceo?.id||null);
      const dur=Date.now()-t0;
      if(r.permission_denied){
        _stepStatus(_runCtx,step.step_id,'blocked',null,'',dur,r.reason);
        if(manifest.pipeline?.on_error==='halt_and_log'){_runCtx.status='halted';_runCtx.completed_at=new Date().toISOString();emit('pipelineHalted',{step,run_ctx:_runCtx});}
      }else if(r.skipped){
        _stepStatus(_runCtx,step.step_id,'skipped',{status:'bridge_offline'},'',dur);
      }else if(!r.ok&&!r.success){
        _stepStatus(_runCtx,step.step_id,'error',null,r.stderr||'',dur,r.error||'Executor failed');
        if(manifest.pipeline?.on_error==='halt_and_log'){_runCtx.status='halted';_runCtx.completed_at=new Date().toISOString();emit('pipelineHalted',{step,run_ctx:_runCtx});}
      }else{
        _stepStatus(_runCtx,step.step_id,'complete',r.output||{status:'ok'},r.stdout||'',dur);
        log('success','  ✓ '+step.label);
      }
    }
  }

  async function runPipeline(projectName,inputData={}){
    const manifest=_projects[projectName];
    if(!manifest){log('error','Project not found: '+projectName);return;}

    _runCtx=_newCtx(projectName,inputData);
    log('system','▶▶ Pipeline: '+projectName+' | '+_runCtx.run_id);
    emit('pipelineStarted',{projectName,run_id:_runCtx.run_id});

    // ── Build execution order from live agents — company-agnostic ────────────
    const liveAgents=Object.values(_agents)
      .filter(a=>a.project===projectName&&a.pipeline_step&&a.enabled!==false);

    const agentSteps=liveAgents.map(a=>({
      step_id:a.pipeline_step,
      label:a.display_name||a.name,
      type:'agent',
      agent_id:a.id,
      requires_approval:a.role==='ceo',
      pipeline_order:a.pipeline_order||999,
      outputs_to:a.connections||a.outputs_to||[]
    }));

    // Executor steps from manifest — agents don't cover these
    const executorSteps=(manifest.pipeline?.execution_order||[])
      .filter(s=>s.type==='executor');

    const allSteps=[...agentSteps,...executorSteps];

    if(!allSteps.length){
      log('warn','No agents with pipeline_step found. Add a Pipeline Step ID to each agent.');
      _runCtx.status='complete';_runCtx.completed_at=new Date().toISOString();
      emit('pipelineComplete',{projectName,run_ctx:_runCtx});
      _archiveRun(_runCtx);return _runCtx;
    }

    // ── Sort into execution levels ────────────────────────────────────────────
    const hasEdges=agentSteps.some(s=>(s.outputs_to||[]).length>0);
    let levels=[];

    if(hasEdges){
      // DAG topological sort
      const nodeMap={};
      allSteps.forEach(s=>{nodeMap[s.agent_id||s.step_id]=s;});
      const inbound={};
      allSteps.forEach(s=>{inbound[s.agent_id||s.step_id]=0;});
      allSteps.forEach(s=>{
        (s.outputs_to||[]).forEach(tid=>{if(inbound[tid]!==undefined)inbound[tid]++;});
      });
      let frontier=allSteps
        .filter(s=>inbound[s.agent_id||s.step_id]===0)
        .map(s=>s.agent_id||s.step_id);
      const visited=new Set();
      while(frontier.length>0){
        const level=frontier.filter(id=>!visited.has(id));
        if(!level.length)break;
        levels.push(level.map(id=>nodeMap[id]));
        level.forEach(id=>visited.add(id));
        const next=[];
        level.forEach(id=>{
          const node=nodeMap[id];
          (node?.outputs_to||[]).forEach(tid=>{
            if(!visited.has(tid)&&nodeMap[tid]){
              const allDone=allSteps
                .filter(s=>(s.outputs_to||[]).includes(tid))
                .every(s=>visited.has(s.agent_id||s.step_id));
              if(allDone)next.push(tid);
            }
          });
        });
        frontier=[...new Set(next)];
      }
      // Catch any unvisited
      allSteps.forEach(s=>{
        const id=s.agent_id||s.step_id;
        if(!visited.has(id))levels.push([s]);
      });
    }else{
      // Linear — sort by pipeline_order
      levels=allSteps
        .sort((a,b)=>(a.pipeline_order||0)-(b.pipeline_order||0))
        .map(s=>[s]);
    }

    // ── Execute each level sequentially ──────────────────────────────────────
    for(const levelSteps of levels){
      for(const step of levelSteps){
        await _executeStep(step,manifest,projectName);
        if(_runCtx.status==='halted'||_runCtx.status==='rejected'){
          _archiveRun(_runCtx);return _runCtx;
        }
      }
    }

    _runCtx.status='complete';_runCtx.completed_at=new Date().toISOString();
    log('success','✓ Pipeline complete: '+projectName);
    emit('pipelineComplete',{projectName,run_ctx:_runCtx});
    _archiveRun(_runCtx);return _runCtx;
  }
  function getRunContext(){return _runCtx;}

  // ── Agent CRUD ────────────────────────────────────────────────
  function createAgent(proj,def,instructions=''){
    const manifest=_projects[proj]; if(!manifest)return null;
    const agent=_buildAgent(def,manifest,instructions);
    _agents[agent.id]=agent;
    manifest.agents=manifest.agents||[];
    if(!manifest.agents.find(a=>a.id===def.id))manifest.agents.push(def);

    if(def.pipeline_step){
      manifest.pipeline=manifest.pipeline||{mode:'sequential',on_block:'halt',on_error:'halt_and_log',execution_order:[]};
      manifest.pipeline.execution_order=manifest.pipeline.execution_order||[];
      const alreadyIn=manifest.pipeline.execution_order.find(s=>s.agent_id===def.id||s.step_id===def.pipeline_step);
      if(!alreadyIn){
        manifest.pipeline.execution_order.push({
          step_id:def.pipeline_step,
          label:def.display_name||def.name,
          type:'agent',
          agent_id:def.id,
          requires_approval:def.role==='ceo',
          pipeline_order:def.pipeline_order||manifest.pipeline.execution_order.length+1,
          outputs_to:def.connections||def.outputs_to||[]
        });
      }
    }

    saveState(); log('success','Agent created: '+agent.name);
    emit('agentCreated',{agent}); return agent;
  }
  function updateAgent(id,updates){
    if(!_agents[id])return false;
    Object.assign(_agents[id],updates,{edited:new Date().toISOString().split('T')[0]});
    saveState(); emit('agentUpdated',{agent:_agents[id]}); return true;
  }
  function deleteAgent(id){
    const a = _agents[id];
    if(!a) return false;

    const projectName = a.project;

    delete _agents[id];

    const m = _projects[projectName];
    if(m){
      m.agents = (m.agents || []).filter(x => x && x.id !== id);

      if(m.pipeline && Array.isArray(m.pipeline.execution_order)){
        m.pipeline.execution_order = m.pipeline.execution_order.filter(step => {
          return !(step && step.type === 'agent' && step.agent_id === id);
        });
      }
    }

    saveState();
    log('warn','Agent deleted: '+a.name);
    emit('agentDeleted',{agentId:id});
    return true;
  }

  function exportProject(proj){
    const manifest=_projects[proj]; if(!manifest)return null;
    const copy=JSON.parse(JSON.stringify(manifest));
    (copy.agents||[]).forEach(def=>{const a=_agents[def.id];if(a)def._instructions=a.instructions;});
    return{manifest:copy,exported_at:new Date().toISOString(),rockoagents_version:'4.0'};
  }
  function importProject(bundle){return loadProject(bundle.manifest||bundle);}

  function generateProjectManifest({name,rootPath,description='',agents=[],extras={}}){
    const id=name.toLowerCase().replace(/[^a-z0-9]/g,'_');
    const agentDefs=agents.length?agents:[{id:'ceo_agent',name:'CEO',display_name:'CEO',role:'ceo',type:'prompt',instruction_file:'agents\\ceo\\AGENT.md',model_provider:'__company_default__',model_override:null,pipeline_step:'ceo_agent_step',enabled:true,status:'active',project_tools:['filesystem','http'],apis:[],local_code:null,description:'Orchestrator.'}];
    const pipelineOrder=agentDefs.map(a=>({step_id:a.pipeline_step||a.id+'_step',label:a.name,type:'agent',agent_id:a.id,requires_approval:a.role==='ceo'}));
    return{schema_version:'2.0',project:{id,name,display_name:name,description,root_path:rootPath,environment_file:'.env',created:new Date().toISOString().split('T')[0],tags:[]},
      model:{default_provider:'anthropic',default_model:'claude-sonnet-4-20250514',fallback_model:'claude-haiku-4-5-20251001',providers:{anthropic:{type:'anthropic',api_base:'https://api.anthropic.com/v1',api_key_env:'ANTHROPIC_API_KEY',temperature:.3,max_tokens:2000}}},
      paths:{agents_root:'agents',vault_root:'vault',logs_root:'logs',outputs_root:'outputs',data_root:'data',src_root:'src'},
      databases:{},vault:{root:'vault',folders:{memory:'vault\\memory',outputs:'vault\\outputs'}},
      env:{required:['ANTHROPIC_API_KEY'],optional:['LOG_LEVEL'],env_file:'.env'},
      tools:{filesystem:{enabled:true},web_search:{enabled:false},http:{enabled:true},shell:{enabled:false}},
      apis:{},executors:{},agents:agentDefs,
      pipeline:{mode:'sequential',on_block:'halt',on_error:'halt_and_log',execution_order:pipelineOrder},
      logs:{root:'logs',pipeline_log:'logs\\pipeline.log',agent_log:'logs\\agents.log',error_log:'logs\\errors.log',format:'jsonl',rotation:'daily',retain_days:30},
      validation:{check_agent_files:true,check_executor_paths:true,check_env_vars:true,fail_mode:'warn'},...extras};
  }

  function getProjects(){return Object.keys(_projects);}
  function getProject(n){return _projects[n]||null;}
  function getActiveProject(){return _active;}
  function setActiveProject(n){if(_projects[n]){_active=n;saveState();emit('activeProjectChanged',{name:n});}}
  function getAgents(proj){return Object.values(_agents).filter(a=>!proj||a.project===proj);}
  function getAgent(id){return _agents[id]||null;}
  function getPipeline(proj){return _projects[proj]?.pipeline?.execution_order||[];}
  function getExecutors(proj){return _projects[proj]?.executors||{};}
  function getModelConfig(proj){return _projects[proj]?.model||null;}

  return{loadProject,validateManifest,getProjectHealth,importProject,exportProject,generateProjectManifest,
    callAgent,createAgent,updateAgent,deleteAgent,syncAgentsFromManifest,
    runPipeline,getRunContext,runExecutorViaBridge,checkPermission,
    createTask,updateTask,deleteTask,runTask,retryTask,blockTask,getTasks,getTask,getSubTasks,
    getRunHistory,getRunById,exportRunReport,resolveApproval,getPendingApproval,
    checkBridge,setBridgeUrl,isBridgeOnline,saveState,loadState,clearState,getLastSaved,
    getProjects,getProject,getActiveProject,setActiveProject,getAgents,getAgent,getPipeline,getExecutors,getModelConfig,
    onLog,onRender,log};
})();
if(typeof module!=='undefined')module.exports=RockoCore;


