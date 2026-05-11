/* ============================================================
   PIPELINE — render, run, approval gate
   ============================================================ */

  const proj=RockoCore.getActiveProject(),manifest=RockoCore.getProject(proj);
  const runCtx=RockoCore.getRunContext();
  document.getElementById('pipelineProjectLabel').textContent=manifest?.project?.display_name||proj||'—';

  // Build unified node list from live agents + any manifest executor steps
  const allAgents=RockoCore.getAgents(proj);
  const manifestSteps=RockoCore.getPipeline(proj);
  const agentIds=new Set(allAgents.map(a=>a.id));

  // Agent nodes — source of truth is live _agents, not manifest
  const agentNodes=allAgents
    .filter(a=>a.pipeline_step)
    .map(a=>({
      step_id:a.pipeline_step,
      label:a.display_name||a.name,
      type:'agent',
      agent_id:a.id,
      requires_approval:a.role==='ceo',
      pipeline_order:a.pipeline_order||999,
      outputs_to:a.connections||a.outputs_to||[]
    }));

  // Executor nodes from manifest only (agents don't cover these)
  const executorNodes=manifestSteps.filter(s=>s.type==='executor');

  const allNodes=[...agentNodes,...executorNodes];

  if(!allNodes.length){
    document.getElementById('pipelineFlow').innerHTML='<div style="color:var(--text-dim);font-family:var(--mono);font-size:12px;padding:20px">No pipeline defined. Create agents with a Pipeline Step ID to populate this view.</div>';
    return;
  }

  // ── DAG traversal ────────────────────────────────────────────────────────
  const nodeMap={};
  allNodes.forEach(n=>{ nodeMap[n.agent_id||n.step_id]=n; });

  // Count inbound edges per node
  const inbound={};
  allNodes.forEach(n=>{ inbound[n.agent_id||n.step_id]=0; });
  allNodes.forEach(n=>{
    (n.outputs_to||[]).forEach(targetId=>{
      if(inbound[targetId]!==undefined) inbound[targetId]++;
    });
  });

  const hasEdges=allNodes.some(n=>(n.outputs_to||[]).length>0);

  let levels=[];

  if(hasEdges){
    // DAG mode — traverse from roots following outputs_to
    let frontier=allNodes
      .filter(n=>inbound[n.agent_id||n.step_id]===0)
      .map(n=>n.agent_id||n.step_id);

    const visited=new Set();
    while(frontier.length>0){
      const level=frontier.filter(id=>!visited.has(id));
      if(!level.length)break;
      levels.push(level);
      level.forEach(id=>visited.add(id));
      const next=[];
      level.forEach(id=>{
        const node=nodeMap[id];
        (node?.outputs_to||[]).forEach(targetId=>{
          if(!visited.has(targetId)&&nodeMap[targetId]){
            // Only advance when ALL inputs to this target have been visited
            const allInputsDone=allNodes
              .filter(n=>(n.outputs_to||[]).includes(targetId))
              .every(n=>visited.has(n.agent_id||n.step_id));
            if(allInputsDone) next.push(targetId);
          }
        });
      });
      frontier=[...new Set(next)];
    }
    // Catch any unconnected nodes
    allNodes.forEach(n=>{
      const id=n.agent_id||n.step_id;
      if(!visited.has(id)) levels.push([id]);
    });
  } else {
    // Linear fallback — sort by pipeline_order
    levels=allNodes
      .sort((a,b)=>(a.pipeline_order||0)-(b.pipeline_order||0))
      .map(n=>[n.agent_id||n.step_id]);
  }

  // ── Render ───────────────────────────────────────────────────────────────
  const rC=agentId=>{
    const a=agentId?RockoCore.getAgent(agentId):null;
    return({ceo:'var(--yellow)',analyst:'var(--accent)',engine:'var(--green)',custom:'var(--purple)'})[a?.role]||'var(--text-dim)';
  };
  const sC=s=>({active:'var(--green)',idle:'var(--text-dim)',paused:'var(--yellow)',error:'var(--red)',
    complete:'var(--green)',skipped:'var(--yellow)',running:'var(--yellow)',
    pending_approval:'var(--yellow)',rejected:'var(--red)',blocked:'var(--red)'})[s]||'var(--text-dim)';

  const renderNode=(nodeId,globalIdx)=>{
    const step=nodeMap[nodeId]; if(!step)return '';
    const agent=step.agent_id?RockoCore.getAgent(step.agent_id):null;
    const color=rC(step.agent_id);
    const agentSt=agent?.status||'idle';
    const runStep=runCtx?.steps?.[step.step_id];
    const displaySt=runStep?.status||agentSt;
    const isCeo=agent?.role==='ceo';
    const isExt=step.type==='executor';
    const needsApproval=step.requires_approval;
    const ex=isExt?(RockoCore.getExecutors(proj)[step.executor_id||step.step_id]||{}):null;
    const durBadge=runStep?.duration_ms?'<span class="node-badge badge-in">'+runStep.duration_ms+'ms</span>':'';
    const stBadge=runStep?.status?'<span class="node-badge badge-'+(runStep.status==='complete'?'ok':runStep.status==='error'||runStep.status==='rejected'||runStep.status==='blocked'?'err':'skip')+'">'+runStep.status.toUpperCase()+'</span>':'';
    const typeBadge='<span class="node-badge '+(isExt?'badge-ext':'badge-in')+'">'+(isExt?'EXECUTOR':'AGENT')+'</span>';
    const approvalBadge=needsApproval?'<span class="node-badge" style="color:var(--yellow);border-color:rgba(255,209,102,.3)">⏸ APPROVAL</span>':'';
    const subtext=isExt?(ex?.script_path||step.step_id):(agent?.description||'No agent registered');
    const nId=(step.agent_id||'').replace(/'/g,'');
    const nLabel=(step.label||'').replace(/'/g,'');
    const nType=(step.type||'');
    return '<div class="pipeline-node '+(isCeo?'ceo-node':'')+' fade-in" style="flex:1;min-width:220px;max-width:420px;" onclick="pipelineClick(\''+nId+'\',\''+nLabel+'\',\''+nType+'\')">'
      +'<div class="node-step">'+globalIdx+'</div>'
      +'<div class="node-icon" style="border-color:'+color+';color:'+color+'">'+(isCeo?'★':isExt?'⚙':'◎')+'</div>'
      +'<div class="node-info"><div class="node-name" style="color:'+color+'">'+step.label+'</div>'
      +'<div class="node-desc">'+subtext+'</div>'
      +'<div class="node-badges">'+typeBadge+approvalBadge+stBadge+durBadge+'</div></div>'
      +'<div class="node-status" style="color:'+sC(displaySt)+'"><div class="agent-status-dot status-'+displaySt+'"></div>'+displaySt.toUpperCase()+'</div>'
      +'</div>';
  };

  let globalIdx=1;
  let html='';
  levels.forEach((level,li)=>{
    const isMulti=level.length>1;
    html+='<div style="display:flex;flex-direction:'+(isMulti?'row':'column')+';align-items:center;justify-content:center;gap:12px;width:100%;">';
    level.forEach(nodeId=>{
      html+=renderNode(nodeId,globalIdx++);
    });
    html+='</div>';
    if(li<levels.length-1){
      // Arrow row — one arrow per node in current level pointing down
      html+='<div style="display:flex;justify-content:center;gap:12px;width:100%;">';
      level.forEach(()=>{
        html+='<div class="pipeline-arrow" style="flex:1;min-width:220px;max-width:420px;"><div class="arrow-head"></div></div>';
      });
      html+='</div>';
    }
  });

  document.getElementById('pipelineFlow').innerHTML=html;
}

function clearRunPanel(){document.getElementById('runPanelBody').innerHTML='<div class="run-empty">Pipeline running...</div>';document.getElementById('runId').textContent='';}
function renderRunPanel(ctx){
  if(!ctx)return;
  document.getElementById('runId').textContent=(ctx.run_id||'').slice(-8);
  const body=document.getElementById('runPanelBody');
  if (!body) return;
  const steps=ctx.steps||{};
  const entries=Object.entries(steps);
  if(!entries.length){body.innerHTML='<div class="run-empty">No steps completed.</div>';return;}
  const sC=s=>({complete:'var(--green)',error:'var(--red)',skipped:'var(--yellow)',running:'var(--yellow)',pending_approval:'var(--yellow)',rejected:'var(--red)',blocked:'var(--red)'})[s]||'var(--text-dim)';
  body.innerHTML=entries.map(([id,step])=>'<div class="run-step-item">'+
    '<div class="run-step-id">'+id+'</div>'+
    '<div class="run-step-status"><div class="agent-status-dot" style="background:'+sC(step.status)+';width:6px;height:6px;border-radius:50%;flex-shrink:0;"></div>'+
    '<span style="color:'+sC(step.status)+';font-size:10px;">'+(step.status||'?').toUpperCase()+'</span>'+
    (step.error?'<span style="color:var(--red);font-size:9px;margin-left:4px">ERR</span>':'')+
    '<span class="run-step-dur">'+(step.duration_ms?step.duration_ms+'ms':'')+'</span></div>'+
    (step.error?'<div style="color:var(--red);font-size:10px;margin-top:4px;word-break:break-all;">'+step.error.slice(0,100)+'</div>':'')+
    '</div>').join('');
}
function updateRunPanelStep(d){const ctx=RockoCore.getRunContext();if(ctx)renderRunPanel(ctx);renderPipeline();}
function pipelineClick(agentId,label,type){
  if(agentId&&RockoCore.getAgent(agentId)){openAgent(agentId);setTab('agents',null);}
  else RockoCore.log('info',(type==='executor'?'Executor: ':'External: ')+label);
}
async function runFullPipeline(){await RockoCore.runPipeline(RockoCore.getActiveProject(),{timestamp:new Date().toISOString()});renderPipeline();}

// ─────────────────────────────────────────────────────────
// APPROVAL GATE
// ─────────────────────────────────────────────────────────
function showApprovalGate(d){
  const ov=document.getElementById('approvalOverlay');
  document.getElementById('approvalStepLabel').textContent='Step: '+d.step.label+' | '+new Date().toLocaleTimeString();
  const ctx=d.context||{};
  document.getElementById('approvalContext').textContent=JSON.stringify(ctx,null,2).slice(0,2000);
  document.getElementById('approvalModInput').value='';
  document.getElementById('approvalModArea').classList.remove('show');
  ov.classList.add('open');
  // Countdown timer
  let secs=0;
  const timer=document.getElementById('approvalTimer');
  if (!timer) return;
  const interval=setInterval(()=>{secs++;timer.textContent='Waiting '+secs+'s...';if(!ov.classList.contains('open'))clearInterval(interval);},1000);
}
function hideApprovalGate(){document.getElementById('approvalOverlay').classList.remove('open');}
function toggleApprovalMod(){document.getElementById('approvalModArea').classList.toggle('show');}
function doApprove(){
  let mods=null;
  const modText=document.getElementById('approvalModInput').value.trim();
  if(modText){try{mods=JSON.parse(modText);}catch(e){alert('Invalid JSON in modifications: '+e.message);return;}}
  RockoCore.resolveApproval('approve',mods);
}
function doReject(){if(!confirm('Reject this pipeline step? The pipeline will halt.'))return;RockoCore.resolveApproval('reject');}

// ─────────────────────────────────────────────────────────
// AGENT EDITOR
// ─────────────────────────────────────────────────────────
function _setVal(id,v){var el=document.getElementById(id);if(el&&'value' in el)el.value=v||'';else if(el)el.textContent=v||'';}
