"""
RockoAgents CEO Orchestrator
Calls the CEO agent in structured-output mode, parses its JSON decision,
validates it against manifest permissions, and returns an executable command.
The CEO is never trusted blindly. Every decision is permission-checked.
"""
import json, time, uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

# -- Valid CEO decisions -------------------------------------------------------
VALID_DECISIONS = {
    "approve",          # proceed with execution
    "reject",           # stop pipeline, do not execute
    "hold",             # pause, wait for human
    "rerun",            # rerun a step with modified input
    "skip",             # skip a pipeline step
    "request_info",     # ask a specific agent for more data
    "create_task",      # spawn follow-up tasks
    "escalate",         # route to human approval
    "log_only",         # record and stop without executing
    "change_posture",   # update system posture
    "pause_pipeline",   # pause, resume later
    "assign_skill",     # assign a skill from skills.sh to an agent
    "hire_agent",       # create a new agent
    "fire_agent",       # deactivate an underperforming agent
}

# -- CEO system prompt template ------------------------------------------------
CEO_ORCHESTRATION_PROMPT = """You are the CEO orchestrator of an autonomous agent pipeline.

You receive the full pipeline context - every agent and executor output - and you must decide what to do next.

You MUST respond with ONLY valid JSON. No markdown, no explanation outside the JSON.

Your response must match this exact schema:

{
  "decision": "approve | reject | hold | rerun | skip | request_info | create_task | escalate | log_only | change_posture | pause_pipeline | assign_skill | hire_agent | fire_agent",
  "reason": "Clear explanation of your decision",
  "target_step_id": "step id to rerun or skip (optional)",
  "target_agent_id": "agent id for request_info (optional)",
  "modified_input": {},
  "created_tasks": [
    {
      "title": "Task title",
      "assigned_to": "agent_id",
      "instructions": "What to do",
      "input": {}
    }
  ],
  "posture": "AGGRESSIVE | NEUTRAL | DEFENSIVE | null",
  "requires_human_approval": true,
  "allow_execution": false,
  "skill_repo": "owner/repo (for assign_skill decisions, e.g. anthropics/skills)",
  "skill_name": "skill-name (for assign_skill decisions, e.g. frontend-design)",
  "target_agent_id": "agent_id (for assign_skill, fire_agent decisions)",
  "agent_name": "New Agent Name (for hire_agent decisions - single agent)",
  "agent_role": "analyst|engine|ceo|custom (for hire_agent decisions)",
  "agents": [
    {
      "name": "Agent Display Name",
      "role": "analyst|engine|ceo|custom",
      "agent_id": "snake_case_id",
      "description": "What this agent does",
      "instructions": "Full AGENT.md content for this agent",
      "skills": [{"repo": "owner/repo", "skill_name": "skill-name"}]
    }
  ]
}

When building a team:
- Use "hire_agent" decision with the "agents" array to create multiple agents at once
- Write complete AGENT.md instructions for each agent - these become their system prompts
- Assign skills from skills.sh where relevant to enhance agent capabilities
- Place agents in logical pipeline order based on their roles
- Always respect company policy on whether auto-creation is allowed

Rules:
- If you are uncertain, choose "hold" and set requires_human_approval to true
- You cannot approve execution unless all risk checks passed
- You cannot bypass the risk manager
- If allow_execution is true, requires_human_approval must also be true unless explicitly safe
- created_tasks is optional - only include if you are creating follow-up work
- posture is optional - only include if you are changing system posture
"""

class CEOOrchestrator:
    def __init__(self, agent_fn: Callable, task_worker=None):
        """
        agent_fn(agent_def, system_prompt, messages) -> dict
        task_worker: optional TaskWorker to create follow-up tasks
        """
        self._agent_fn   = agent_fn
        self._task_worker = task_worker
        self._project:   Dict = {}
        self._log_fn:    Callable = print
        self._decisions: List[Dict] = []

    def init(self, project: Dict, log_fn: Callable = print):
        self._project = project
        self._log_fn  = log_fn

    def _log(self, msg: str):
        self._log_fn(f"[ORCHESTR] {msg}")

    # -- Find CEO agent --------------------------------------------------------
    def _find_ceo(self) -> Optional[Dict]:
        for a in self._project.get("agents", []):
            if a.get("role") == "ceo":
                return a
        return None

    # -- Permission validation -------------------------------------------------
    def _validate_decision(self, decision: Dict, pipeline_ctx: Dict) -> Dict:
        """
        Validates the CEO decision against project permissions.
        Returns validated decision or raises ValueError.
        """
        cmd = decision.get("decision")
        if cmd not in VALID_DECISIONS:
            raise ValueError(f"Invalid decision: '{cmd}'. Must be one of {VALID_DECISIONS}")

        manifest = self._project
        pipeline = manifest.get("pipeline", {})
        steps    = {s["step_id"]: s for s in pipeline.get("execution_order", [])}

        # HARD SAFETY RULE: If ANY executor step exists in pipeline, human approval
        # is ALWAYS required. This cannot be overridden by CEO - not even by allow_execution=True.
        has_executor = any(s.get("type") == "executor" for s in pipeline.get("execution_order", []))
        if has_executor:
            if not decision.get("requires_human_approval"):
                self._log("SAFETY: CEO approval forced to require human gate (executor steps present)")
            decision["requires_human_approval"] = True
            # allow_execution may be true in CEO response but it means nothing without human approval
            # We preserve the flag for logging but the pipeline runner checks requires_human_approval first
            if decision.get("allow_execution") and cmd == "approve":
                self._log("SAFETY: CEO allow_execution=True acknowledged but human gate still required")

        # Validate rerun target exists
        if cmd == "rerun":
            target = decision.get("target_step_id")
            if target and target not in steps:
                raise ValueError(f"rerun target step '{target}' not in pipeline")

        # Validate skip target exists
        if cmd == "skip":
            target = decision.get("target_step_id")
            if target and target not in steps:
                raise ValueError(f"skip target step '{target}' not in pipeline")

        # Validate request_info target
        if cmd == "request_info":
            target_agent = decision.get("target_agent_id")
            agent_ids = {a["id"] for a in manifest.get("agents", [])}
            if target_agent and target_agent not in agent_ids:
                raise ValueError(f"request_info target agent '{target_agent}' not in project")

        # Validate created_tasks assignments
        agent_ids = {a["id"] for a in manifest.get("agents", [])}
        for task in decision.get("created_tasks", []):
            if task.get("assigned_to") and task["assigned_to"] not in agent_ids:
                raise ValueError(f"Created task assigned to unknown agent: '{task['assigned_to']}'")

        return decision

    # -- Parse CEO JSON --------------------------------------------------------
    def _parse_ceo_json(self, raw_text: str) -> Dict:
        text = raw_text.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"CEO returned invalid JSON: {e}\n\nRaw response:\n{raw_text[:500]}")

    # -- Main orchestration call -----------------------------------------------
    def orchestrate(self, pipeline_ctx: Dict, step: Dict = None) -> Dict:
        """
        Call the CEO agent in orchestration mode with full pipeline context.
        Returns a validated, permission-checked decision dict.
        """
        ceo_def = self._find_ceo()
        if not ceo_def:
            self._log("No CEO agent defined in project - defaulting to hold")
            return self._default_hold("No CEO agent defined")

        ceo_id  = ceo_def["id"]
        t0      = time.time()
        run_id  = f"orch_{uuid.uuid4().hex[:8]}"

        # Build context message for CEO
        ctx_summary = json.dumps(pipeline_ctx, indent=2)[:4000]
        user_msg = f"""Pipeline context for your review:

{ctx_summary}

Based on all upstream outputs, provide your orchestration decision as JSON."""

        self._log(f"CEO orchestration call [{run_id}]")

        # Load CEO instructions
        instr_file = ceo_def.get("instruction_file", "")
        system = CEO_ORCHESTRATION_PROMPT
        if ceo_def.get("_instructions"):
            system = ceo_def["_instructions"] + "\n\n" + CEO_ORCHESTRATION_PROMPT

        result = self._agent_fn(
            ceo_def, system,
            [{"role": "user", "content": user_msg}]
        )

        dur = round((time.time() - t0) * 1000)

        if not result.get("ok"):
            self._log(f"CEO call failed: {result.get('error', 'unknown')}")
            return self._default_hold(f"CEO call failed: {result.get('error', 'unknown')}")

        raw_text = result.get("text", "")

        # Parse and validate
        try:
            decision = self._parse_ceo_json(raw_text)
            decision = self._validate_decision(decision, pipeline_ctx)
        except (ValueError, Exception) as e:
            self._log(f"CEO decision invalid: {e}")
            record = {
                "id":           run_id,
                "status":       "parse_error",
                "error":        str(e),
                "raw_response": raw_text,
                "timestamp":    datetime.now().isoformat(),
                "duration_ms":  dur,
            }
            self._decisions.insert(0, record)
            return self._default_hold(f"CEO returned invalid decision: {e}")

        # Handle follow-up task creation
        created = []
        if decision.get("created_tasks") and self._task_worker:
            for task_def in decision["created_tasks"]:
                try:
                    task = self._task_worker.create_task(
                        title        = task_def.get("title", "CEO Follow-up Task"),
                        assigned_to  = task_def.get("assigned_to", ceo_id),
                        task_type    = "agent",
                        instructions = task_def.get("instructions", ""),
                        input_data   = task_def.get("input", {}),
                    )
                    created.append(task["id"])
                    self._log(f"CEO created task: {task['title']}")
                except Exception as e:
                    self._log(f"Failed to create CEO task: {e}")

        # Record decision
        record = {
            "id":            run_id,
            "status":        "complete",
            "decision":      decision.get("decision"),
            "reason":        decision.get("reason"),
            "raw_response":  raw_text,
            "created_tasks": created,
            "requires_human_approval": decision.get("requires_human_approval", True),
            "allow_execution": decision.get("allow_execution", False),
            "timestamp":     datetime.now().isoformat(),
            "duration_ms":   dur,
            "model":         result.get("model"),
            "fallback_used": result.get("fallback_used", False),
        }
        self._decisions.insert(0, record)
        if len(self._decisions) > 50: self._decisions = self._decisions[:50]

        self._log(f"CEO decision: {decision['decision']} - {decision.get('reason', '')[:80]}")
        return {**decision, "_run_id": run_id, "_duration_ms": dur, "_created_tasks": created}

    # -- Apply decision to pipeline --------------------------------------------
    def apply_to_pipeline(self, decision: Dict, pipeline_steps: List[Dict],
                          current_ctx: Dict) -> Dict:
        """
        Translates a CEO decision into pipeline actions.
        Returns action dict for the pipeline runner to execute.
        """
        cmd = decision.get("decision", "hold")

        if cmd == "approve":
            if decision.get("requires_human_approval"):
                return {"action": "pause_for_human", "context": current_ctx}
            return {"action": "continue", "context": current_ctx}

        elif cmd == "reject":
            return {"action": "halt", "reason": decision.get("reason", "CEO rejected")}

        elif cmd == "hold":
            return {"action": "pause_for_human", "context": current_ctx,
                    "reason": decision.get("reason", "CEO requested hold")}

        elif cmd == "rerun":
            return {"action": "rerun_step",
                    "step_id": decision.get("target_step_id"),
                    "modified_input": decision.get("modified_input", {})}

        elif cmd == "skip":
            return {"action": "skip_step",
                    "step_id": decision.get("target_step_id")}

        elif cmd == "request_info":
            return {"action": "rerun_agent",
                    "agent_id": decision.get("target_agent_id"),
                    "modified_input": decision.get("modified_input", {})}

        elif cmd == "create_task":
            return {"action": "tasks_created",
                    "task_ids": decision.get("_created_tasks", [])}

        elif cmd == "escalate":
            return {"action": "pause_for_human", "escalated": True, "context": current_ctx}

        elif cmd == "log_only":
            return {"action": "halt", "log_only": True,
                    "reason": decision.get("reason", "CEO: log only")}

        elif cmd == "pause_pipeline":
            return {"action": "pause_for_human", "context": current_ctx}

        elif cmd == "change_posture":
            return {"action": "continue", "posture": decision.get("posture"),
                    "context": current_ctx}

        elif cmd == "assign_skill":
            return {
                "action":       "assign_skill",
                "target_agent": decision.get("target_agent_id"),
                "skill_repo":   decision.get("skill_repo"),
                "skill_name":   decision.get("skill_name"),
                "reason":       decision.get("reason"),
            }

        elif cmd == "hire_agent":
            # Support both single agent and agents[] array
            agents = decision.get("agents", [])
            if not agents and decision.get("agent_name"):
                # Legacy single-agent format
                agents = [{
                    "name":         decision.get("agent_name"),
                    "role":         decision.get("agent_role", "analyst"),
                    "agent_id":     decision.get("agent_id", ""),
                    "description":  decision.get("reason", ""),
                    "instructions": decision.get("instructions", ""),
                    "skills":       decision.get("skills", []),
                }]
            return {
                "action":     "hire_agent",
                "agents":     agents,
                "reason":     decision.get("reason", ""),
                "requires_approval": decision.get("requires_human_approval", True),
                "auto_create": not decision.get("requires_human_approval", True),
            }

        elif cmd == "fire_agent":
            return {
                "action":      "fire_agent",
                "target_agent": decision.get("target_agent_id"),
                "reason":      decision.get("reason"),
            }

        return {"action": "pause_for_human", "context": current_ctx}

    def _default_hold(self, reason: str) -> Dict:
        return {
            "decision":               "hold",
            "reason":                 reason,
            "requires_human_approval": True,
            "allow_execution":         False,
            "created_tasks":           [],
        }

    # -- History ---------------------------------------------------------------
    def get_decisions(self) -> List[Dict]:
        return self._decisions

    def get_latest_decision(self) -> Optional[Dict]:
        return self._decisions[0] if self._decisions else None