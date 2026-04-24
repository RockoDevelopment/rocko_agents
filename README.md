# RockoAgents

Self-hosted local agent orchestration. No Python required for end users.

---

## For Users — Download and Run

Download the latest release from GitHub Releases:

- **Windows** → `rocko.exe`
- **Mac** → `rocko`

Place the executable in your `RockoAgentHub` folder alongside `index.html`. Then:

**Windows:**
```
rocko.exe run
```
or just double-click `rocko.exe`.

**Mac:**
```
chmod +x rocko
./rocko run
```

That is it. No Python required. No install step. No venv.

---

## For Developers — Run from source

Requires Python 3.x (any version).

```
cd RockoAgentHub
pip install -e .
rocko run
```

`pip install -e .` only needs to run once. It registers the `rocko` command globally.

---

## Building the executable yourself

```
pip install pyinstaller
python build.py
```

Outputs `rocko.exe` (Windows) or `rocko` (Mac) in the repo root. ~25MB single file.

---

## File structure

```
RockoAgentHub/
├── rocko.exe / rocko       ← Compiled executable (download from releases)
├── main.py                 ← PyInstaller entry point / developer run
├── build.py                ← Build script
├── rocko.spec              ← PyInstaller specification
├── index.html              ← Full UI
├── skills.json             ← Custom/fallback skill definitions
├── .github/
│   └── workflows/
│       └── build.yml       ← Auto-builds exe on every tagged release
├── bridge/
│   ├── bridge.py
│   ├── model_manager.py
│   ├── task_worker.py
│   ├── scheduler.py
│   ├── orchestrator.py
│   └── runtime_manager.py
└── projects/
    └── ThePaperTeam/
```

---

## How it works

`rocko run` starts the bridge (a local FastAPI server on port 8787), opens `index.html` in Edge or Chrome as a standalone app window, and keeps running in the terminal showing live request logs. The bridge handles all automation state — agents, tasks, schedules, pipeline runs — and writes everything to disk so nothing is lost on restart.

---

## First run

If no company exists, you see the company creation screen. Enter a company name, description, and local folder path. After that it goes straight to your workspace every time.

ThePaperTeam auto-loads if it was already configured.

---

## Skills — powered by skills.sh

[skills.sh](https://skills.sh) is Vercel's open agent skills directory. Skills are `SKILL.md` instruction files from GitHub used by Claude Code, Copilot, Cursor, and other agents.

RockoAgents integrates directly. The CEO agent can assign skills to other agents:

```json
{
  "decision": "assign_skill",
  "target_agent_id": "research_agent",
  "skill_repo": "anthropics/skills",
  "skill_name": "skill-creator",
  "reason": "This agent needs better methodology for complex tasks"
}
```

The bridge fetches the `SKILL.md` from GitHub and appends it to the target agent's instructions. Cached locally in `.rocko_skills/` for offline use.

Browse skills manually: Agents tab → any agent → Manage Skills.

---

## CEO decisions

| Decision | Action |
|---|---|
| `approve` | Continue with human gate if executor steps exist |
| `reject` | Halt pipeline |
| `hold` | Pause for human review |
| `rerun` | Re-run a step with modified input |
| `skip` | Skip a step |
| `request_info` | Re-run an agent with focused question |
| `create_task` | Spawn tasks into the worker queue |
| `escalate` | Human approval overlay |
| `log_only` | Record and stop |
| `pause_pipeline` | Pause for later |
| `assign_skill` | Fetch skill from skills.sh and apply to agent |
| `hire_agent` | Create a new agent |
| `fire_agent` | Deactivate underperforming agent |

---

## External runtimes

Connect OpenClaw, Claude Code, local HTTP agents, or webhooks via `project.json`:

```json
"runtimes": {
  "openclaw": {
    "type": "cli",
    "command": "openclaw",
    "args": ["run", "--agent", "researcher"],
    "input_mode": "stdin_json",
    "output_mode": "stdout_json",
    "risk_level": "read_only",
    "allowed_agents": ["ceo_agent"]
  }
}
```

---

## NVIDIA model support

RockoAgents supports NVIDIA-hosted models through your own NVIDIA API key. RockoAgents does not provide or pay for NVIDIA model access — you bring your own key.

Add to `.env`:
```
NVIDIA_API_KEY=nvapi-your-key-here
```

Add to `project.json`:
```json
"model": {
  "providers": {
    "nvidia": {
      "type": "openai_compatible",
      "api_base": "https://integrate.api.nvidia.com/v1",
      "api_key_env": "NVIDIA_API_KEY",
      "available_models": [
        "nvidia/llama-3.1-nemotron-ultra-253b-v1",
        "meta/llama-3.1-70b-instruct",
        "meta/llama-3.3-70b-instruct",
        "deepseek-ai/deepseek-r1",
        "mistralai/mixtral-8x7b-instruct-v0.1",
        "google/gemma-3-27b-it",
        "microsoft/phi-4",
        "qwen/qwen2.5-72b-instruct"
      ]
    }
  }
}
```

Per-agent override:
```json
{ "id": "research_agent", "model_override": "deepseek-ai/deepseek-r1" }
```

The CEO can run on Anthropic while analysts run on NVIDIA, or any combination. The Orchestration tab shows each provider's key status — present or missing — without exposing the actual key value. Use Test Connection to verify your key works before running agents.

Get an NVIDIA API key at: https://build.nvidia.com/


## Compared to Paperclip

| | Paperclip | RockoAgents |
|---|---|---|
| Start | `pnpm paperclipai run` | `rocko run` (or double-click exe) |
| Requirements | Node.js + pnpm | Nothing (compiled exe) |
| Company layer | Yes | Yes |
| Skills system | No | Yes — live from skills.sh |
| Agent hire/fire | No | Yes |
| External runtimes | Via integrations | CLI, HTTP, webhook |
| CEO orchestration | Approve/reject | 13 decision types |
| Desktop app | No | Yes — PWA |
| Cloud required | Yes | No |