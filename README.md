# RockoAgents

Self-hosted local agent orchestration platform. One command. No cloud. No account required.

---

## What it is

RockoAgents is a complete alternative to Paperclip that runs entirely on your machine. You create a company, build a team of agents, connect your scripts or external tools, and the platform handles everything — pipeline execution, task queuing, scheduling, CEO orchestration, and human approval gates.

**Company** is the user-facing workspace. **Project** is the technical manifest underneath. You never need to edit project.json unless you want to.

---

## Quickstart

```
cd RockoAgentHub
pip install -e .
rocko run
```

First run shows the company creation screen. After that it goes straight to your workspace.

---

## Prerequisites

- Python 3.x — any version
- Chrome or Edge — for the app window
- An Anthropic API key, or any OpenAI-compatible endpoint

No Node.js. No npm. No Docker.

---

## File structure

```
RockoAgentHub/
├── index.html
├── skills.json             Custom/fallback skill definitions
├── pyproject.toml
├── rockoagents/
│   └── cli.py
├── bridge/
│   ├── bridge.py
│   ├── model_manager.py
│   ├── task_worker.py
│   ├── scheduler.py
│   ├── orchestrator.py
│   └── runtime_manager.py
├── projects/
│   └── ThePaperTeam/
│       ├── project.json
│       └── agents/
└── data/rockoagents/
    ├── companies.json
    ├── tasks.json
    ├── schedules.json
    └── pipeline_runs.json
```

---

## Company layer

When RockoAgents opens with no company, you see the creation screen. Enter a company name, description, folder path, and optional logo. That becomes your workspace. Everything — agents, pipeline, tasks, history — is scoped to the active company.

The company rail on the left shows all your companies as logo icons. Click to switch instantly.

ThePaperTeam is auto-migrated as your first company if it was already loaded.

---

## Skills system — powered by skills.sh

Skills are reusable `SKILL.md` instruction files hosted on GitHub. The [skills.sh](https://skills.sh) directory lists thousands of community-published skills compatible with Claude Code, Copilot, Cursor, and other agents.

**RockoAgents integrates directly with this ecosystem.**

### How it works

The CEO agent can browse skills.sh and assign skills to other agents as part of its orchestration decisions. When a skill is assigned, RockoAgents fetches the `SKILL.md` from GitHub and appends its instructions to the target agent's system prompt.

### CEO assigning a skill

The CEO returns a structured JSON decision:

```json
{
  "decision": "assign_skill",
  "reason": "The research agent needs better synthesis methodology for this task",
  "target_agent_id": "research_agent",
  "skill_repo": "anthropics/skills",
  "skill_name": "skill-creator",
  "requires_human_approval": false,
  "allow_execution": false
}
```

The bridge fetches `https://github.com/anthropics/skills/blob/main/skills/skill-creator/SKILL.md`, parses the frontmatter and instruction content, and appends it to the target agent's prompt. The skill is cached locally in `.rocko_skills/` for offline use.

### Manual skill assignment

Open any agent → Skills section → **Manage Skills**. The modal browses the live skills.sh leaderboard. Find a skill, click Apply. The SKILL.md is fetched from GitHub and applied.

### Bridge endpoints

```
GET  /skills              Local skills.json (custom/fallback)
GET  /skills/browse       Live skills.sh leaderboard (trending skills)
GET  /skills/fetch?repo=owner/repo&skill=name   Fetch a specific SKILL.md
POST /skills/assign       Assign a skill to an agent (CEO uses this)
```

### Skills.sh

Skills.sh is Vercel's open agent skills directory — a leaderboard of SKILL.md files from GitHub used by Claude Code, GitHub Copilot, Cursor, Gemini CLI, and many others. Any skill in this ecosystem works with RockoAgents.

Browse at: https://skills.sh

Popular skills include:
- `anthropics/skills` — frontend-design, docx, pptx, pdf, skill-creator
- `vercel-labs/agent-skills` — React best practices, web design guidelines
- `microsoft/azure-skills` — Azure integration skills
- And thousands more from the community

---

## Hire and Fire

**Hiring an agent:**
Click **+ Hire Agent** in the Agents tab. Choose a role, name, description, and initial instructions.

**Firing an agent:**
Open any agent → click **⊘ Fire**. The agent is deactivated, removed from the active pipeline, and preserved in history. Click **↩ Reinstate** to restore.

**CEO-triggered hire/fire:**

CEO can return:

```json
{
  "decision": "hire_agent",
  "reason": "Need a dedicated compliance reviewer for this pipeline",
  "agent_name": "Compliance Reviewer",
  "agent_role": "analyst"
}
```

```json
{
  "decision": "fire_agent",
  "reason": "Research agent consistently produces low-confidence outputs",
  "target_agent_id": "research_agent"
}
```

---

## CEO Orchestration

Full decision types the CEO can return:

| Decision | Action |
|---|---|
| `approve` | Continue — human gate shown if executor steps exist |
| `reject` | Halt — archived as rejected |
| `hold` | Pause for human review |
| `rerun` | Re-run a step with modified input |
| `skip` | Skip a step |
| `request_info` | Re-run an agent with a focused question |
| `create_task` | Spawn follow-up tasks into the worker queue |
| `escalate` | Route to human approval |
| `log_only` | Record and stop |
| `pause_pipeline` | Pause for later |
| `assign_skill` | Fetch a skill from skills.sh and apply to an agent |
| `hire_agent` | Create a new agent |
| `fire_agent` | Deactivate an underperforming agent |

**Safety rule:** If the pipeline contains any executor or runtime step, human approval is always required — regardless of what the CEO returns.

---

## External runtimes

Connect any external agent system as a worker. Add to `project.json`:

```json
"runtimes": {
  "openclaw_research": {
    "type": "cli",
    "command": "openclaw",
    "args": ["run", "--agent", "researcher"],
    "working_dir": "{{PROJECT_ROOT}}",
    "input_mode": "stdin_json",
    "output_mode": "stdout_json",
    "timeout_seconds": 300,
    "risk_level": "read_only",
    "allowed_agents": ["ceo_agent"]
  }
}
```

Supported types: `cli`, `http`, `webhook`, `mcp` (reserved).

Risk levels: `read_only` runs normally. `write` runs with optional approval. `deploy` and `financial` always require human approval.

---

## Automation

**Task Worker** — background worker processes the queue continuously. No manual intervention needed.

**Scheduler** — cron or interval schedules for any agent, pipeline, executor, or runtime. Persists across bridge restarts. Scheduled runs still stop at approval gates.

---

## Model configuration

All config in `project.json`. Nothing hardcoded:

```json
"model": {
  "default_provider": "anthropic",
  "default_model": "claude-sonnet-4-20250514",
  "fallback_model": "claude-haiku-4-5-20251001",
  "providers": {
    "anthropic": { "type": "anthropic", "api_key_env": "ANTHROPIC_API_KEY" },
    "openai":    { "type": "openai_compatible", "api_base": "https://api.openai.com/v1", "api_key_env": "OPENAI_API_KEY" },
    "local":     { "type": "openai_compatible", "api_base": "http://localhost:11434/v1" }
  }
}
```

Override per agent: `"model_override": "claude-opus-4-20250514"`

---

## System verification

Settings → **⚙ System Test** — verifies all subsystems and returns pass/warn/fail per component.

---

## Compared to Paperclip

| | Paperclip | RockoAgents |
|---|---|---|
| Start | `pnpm paperclipai run` | `rocko run` |
| Setup | Node + PostgreSQL | `pip install -e .` |
| Company layer | Yes | Yes |
| Agent hire/fire | No | Yes — human and CEO-triggered |
| Skills system | No | Yes — live from skills.sh |
| External runtimes | Via integrations | CLI, HTTP, webhook |
| Task queue | Yes | Yes — auto-worker |
| Scheduling | Yes | Yes — APScheduler |
| CEO orchestration | Approve/reject | 13 decision types including skill assignment |
| Approval gates | Yes | Yes — cannot be bypassed |
| Desktop app | No | Yes — PWA |
| Cloud required | Yes | No |