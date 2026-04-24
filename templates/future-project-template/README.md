# Future Project Template

Starter template for onboarding any new local agent project into RockoAgents.
Copy this folder, rename it, fill in `project.json`, and import it into the hub.

---

## Minimum Required Files

```
YourProject/
├── project.json          ← Required. Defines everything.
├── .env                  ← Your API keys and env vars
└── agents/
    └── your_agent/
        └── AGENT.md      ← System prompt for the agent
```

---

## Suggested Full Structure

```
YourProject/
├── project.json
├── .env
├── agents/
│   ├── ceo/
│   │   └── AGENT.md
│   └── specialist_one/
│       └── AGENT.md
├── src/                  ← Local code for executor steps
├── api/                  ← API wrappers if needed
├── data/                 ← Datastores, CSVs, etc.
├── vault/                ← Persistent agent memory
├── logs/                 ← Pipeline and agent logs
└── outputs/              ← Agent outputs
```

---

## How to Onboard a New Project

1. Copy this template folder to a new location
2. Rename the folder to your project name
3. Edit `project.json`:
   - `project.id` — unique lowercase_snake_case identifier
   - `project.name` — display name
   - `project.root_path` — absolute path to your project folder
   - `model` — set your provider and default model
   - `env.required` — list all required env vars
   - `apis` — declare any external APIs your agents call
   - `tools` — enable only what your project needs
   - `executors` — declare any local Python scripts in the pipeline
   - `agents` — add your agents with instruction file paths
   - `pipeline.execution_order` — define the step sequence
4. Add `AGENT.md` files for each agent in the agents array
5. Copy `.env.example` to `.env` and fill in your keys
6. In RockoAgents: **Project Switcher → Import project.json**
7. Fix any validation errors shown in Settings → Validation
8. Your project is live

---

## project.json Key Rules

| Field | Required | Notes |
|-------|----------|-------|
| `schema_version` | Yes | Must be `"2.0"` |
| `project.id` | Yes | Unique, no spaces |
| `project.root_path` | Yes | Absolute path |
| `model.default_provider` | Yes | `anthropic`, `openai`, or custom |
| `model.default_model` | Yes | Model name string |
| `agents[].instruction_file` | Yes | Relative to root_path |
| `pipeline.execution_order` | Yes | At least one step |
| `env.required` | Yes | List required env vars |

---

## Supported Agent Types

| Type | Description |
|------|-------------|
| `prompt` | Pure LLM agent — runs on instructions only |
| `hybrid` | LLM agent + local code integration |
| `executor` | Local code only — no LLM prompt |

---

## Supported Model Providers

| Provider | type field |
|----------|-----------|
| Anthropic Claude | `anthropic` |
| OpenAI | `openai_compatible` |
| Ollama (local) | `openai_compatible` with local base_url |
| Any OpenAI-compatible | `openai_compatible` |

---

## Changing the Default Project

To change which project auto-loads when RockoAgents opens, edit:

```
RockoAgents/config/app.json
```

Change `default_project` to your new project name.
No core code changes required.

---

## Platform Guarantee

Adding this project to RockoAgents requires:
- ✅ A `project.json` manifest
- ✅ `AGENT.md` files for each agent
- ✅ A `.env` file with your keys

**It never requires editing `platform.js`, `index.html` core logic, or any RockoAgents system file.**
