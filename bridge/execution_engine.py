"""
RockoAgents Native Execution Engine
====================================
Built-in execution layer. No external runtime required.

Supports:
  - Python scripts (.py files)
  - Python modules (importable)
  - Shell commands
  - Local executables
  - HTTP calls (GET/POST/PUT/DELETE)
  - JSON input/output
  - stdout/stderr capture
  - exit code capture
  - execution duration
  - timeout enforcement
  - working directory enforcement
  - run logs
  - approval gates by risk level

  [Clawd-Code Integration]
  - clawd_agent executor type: multi-turn tool-calling agent loop
  - Skills loaded from SKILL.md directories via Clawd-Code skill loader
  - Provider-agnostic: Anthropic, OpenAI, GLM, Minimax
  - Full tool registry: Read, Write, WebFetch, WebSearch, Bash, Glob, Grep, Edit
  - Session persistence across turns
  - Tool events logged per step

Domain-agnostic. All behavior comes from project.json executors,
AGENT.md instructions, company policy, and pipeline config.

Works for: trading, research, content, support, coding,
           data analysis, operations, sales - same engine, zero changes.
"""
import json, os, subprocess, sys, time, uuid, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

# ── Clawd-Code path registration ─────────────────────────────────────────────
# Resolve the Clawd-Code repo relative to this file's location.
# Supports both sibling-directory layout and explicit CLAWD_CODE_PATH env var.
# Clawd-Code is a git submodule at the project root:
#   git submodule add https://github.com/GPT-AGI/Clawd-Code.git Clawd-Code
# Path from bridge/execution_engine.py -> ../Clawd-Code (root level)
_CLAWD_PATH = os.environ.get("CLAWD_CODE_PATH") or str(
    Path(__file__).parent.parent / "Clawd-Code"
)
if Path(_CLAWD_PATH).exists() and _CLAWD_PATH not in sys.path:
    sys.path.insert(0, _CLAWD_PATH)

# Attempt to import Clawd-Code modules — graceful fallback if not available
_CLAWD_AVAILABLE = False
_CLAWD_IMPORT_ERROR = ""
try:
    from src.skills.loader import get_all_skills, load_skills_from_dir
    from src.skills.model import PromptSkill
    from src.tool_system.agent_loop import run_agent_loop, AgentLoopResult, ToolEvent
    from src.tool_system.registry import ToolRegistry, ToolSpec
    from src.tool_system.context import ToolContext
    from src.tool_system.permissions import ToolPermissionContext
    from src.tool_system.loader import load_tools_from_dir
    from src.agent.conversation import Conversation
    from src.providers import get_provider_class
    _CLAWD_AVAILABLE = True
except ImportError as _clawd_err:
    _CLAWD_IMPORT_ERROR = str(_clawd_err)

# ─────────────────────────────────────────────────────────────────────────────

RISK_LEVELS  = {"read_only", "write", "deploy", "financial"}
AUTO_APPROVE = {"read_only"}
ALWAYS_HUMAN = {"deploy", "financial"}

ENGINE_VERSION = "native_engine_v2_clawd_2026_05"

# Clawd tools available per risk level
_CLAWD_TOOLS_READ_ONLY = ["Read", "Glob", "Grep", "WebFetch", "WebSearch"]
_CLAWD_TOOLS_WRITE     = _CLAWD_TOOLS_READ_ONLY + ["Write", "Edit"]
_CLAWD_TOOLS_FULL      = _CLAWD_TOOLS_WRITE + ["Bash"]


def _result(ok: bool, executor_id: str, exec_type: str, t0: float,
            output: Dict = None, stdout: str = "", stderr: str = "",
            error: str = None, extra: Dict = None) -> Dict:
    return {
        "ok":           ok,
        "executor_id":  executor_id,
        "type":         exec_type,
        "engine":       ENGINE_VERSION,
        "started_at":   datetime.fromtimestamp(t0).isoformat(),
        "completed_at": datetime.now().isoformat(),
        "duration_ms":  round((time.time() - t0) * 1000),
        "output":       output or {},
        "stdout":       stdout,
        "stderr":       stderr,
        "error":        error,
        **(extra or {}),
    }


class NativeExecutionEngine:
    """
    Domain-agnostic local execution engine.
    Reads all behavior from executor definitions - never from hardcoded domain logic.
    Integrates Clawd-Code for multi-turn agent loop execution.
    """

    def __init__(self):
        self._project:      Dict = {}
        self._project_root: str  = ""
        self._executors:    Dict = {}
        self._run_history:  Dict = {}
        self._log_fn:       Callable = print
        self._env_cache:    Optional[Dict] = None
        self._skill_cache:  Optional[List] = None

    def init(self, project: Dict, log_fn: Callable = print):
        self._project      = project
        self._project_root = project.get("project", {}).get("root_path", "")
        self._log_fn       = log_fn
        self._env_cache    = None
        self._skill_cache  = None
        self._load_executors()
        if _CLAWD_AVAILABLE:
            self._log("Clawd-Code integration: ACTIVE (skills + agent loop enabled)")
        else:
            self._log(f"Clawd-Code integration: UNAVAILABLE — {_CLAWD_IMPORT_ERROR}")

    def _log(self, msg: str):
        self._log_fn(f"[ENGINE  ] {msg}")

    def _load_executors(self):
        raw = self._project.get("executors", {})
        self._executors = {}
        for eid, cfg in raw.items():
            self._executors[eid] = {**cfg, "id": eid}
        self._log(f"Loaded {len(self._executors)} executor(s)")

    def _load_env(self) -> Dict:
        if self._env_cache is not None:
            return self._env_cache
        env = os.environ.copy()
        env_file = self._project.get("env", {}).get("env_file", ".env")
        ep = Path(self._project_root) / env_file if self._project_root else Path(env_file)
        if ep.exists():
            with open(ep) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip().strip('"').strip("'")
        self._env_cache = env
        return env

    def _safe_dir(self, working_dir: str) -> str:
        try:
            resolved = Path(working_dir).resolve()
            return str(resolved)
        except Exception:
            return self._project_root or "."

    def _substitute(self, template: str, context: Dict) -> str:
        if not isinstance(template, str):
            return str(template)
        result = template
        safe = {
            "PROJECT_ROOT": self._project_root,
            "project_root": self._project_root,
        }
        for k, v in context.items():
            if isinstance(v, (str, int, float)):
                safe[k] = str(v)
        for k, v in safe.items():
            result = result.replace("{{" + k + "}}", v)
        return result

    # ── Execution types ───────────────────────────────────────────────────────

    def _run_python_script(self, eid: str, cfg: Dict, context: Dict, env: Dict) -> Dict:
        t0 = time.time()
        script = cfg.get("script_path", "")
        if not script:
            return _result(False, eid, "python_script", t0, error="No script_path defined")

        script_path = Path(self._project_root) / script if self._project_root else Path(script)
        if not script_path.exists():
            return _result(False, eid, "python_script", t0,
                           error=f"Script not found: {script_path}")

        timeout = cfg.get("timeout_seconds", 120)
        wdir    = self._safe_dir(cfg.get("working_dir", self._project_root or "."))
        args    = cfg.get("args", [])
        cmd     = [sys.executable, str(script_path)] + [str(a) for a in args]

        self._log(f"Python '{eid}': {script_path.name}")
        try:
            stdin_data = json.dumps(context).encode() if cfg.get("input_mode") == "stdin_json" else None
            proc = subprocess.Popen(
                cmd, cwd=wdir, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                stdin=subprocess.PIPE if stdin_data else None, text=False
            )
            try:
                out_b, err_b = proc.communicate(input=stdin_data, timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                return _result(False, eid, "python_script", t0,
                               error=f"Timed out after {timeout}s")

            stdout = out_b.decode("utf-8", errors="replace")
            stderr = err_b.decode("utf-8", errors="replace")
            ok     = proc.returncode == 0
            output = {}
            if ok and stdout.strip():
                try:    output = json.loads(stdout.strip())
                except: output = {"text": stdout.strip()}
            return _result(ok, eid, "python_script", t0,
                           output=output, stdout=stdout, stderr=stderr,
                           error=f"Exit {proc.returncode}" if not ok else None)
        except Exception as e:
            return _result(False, eid, "python_script", t0, error=str(e))

    def _run_shell(self, eid: str, cfg: Dict, context: Dict, env: Dict) -> Dict:
        t0      = time.time()
        command = cfg.get("command", "")
        if not command:
            return _result(False, eid, "shell", t0, error="No command defined")

        command  = self._substitute(command, context)
        timeout  = cfg.get("timeout_seconds", 60)
        wdir     = self._safe_dir(cfg.get("working_dir", self._project_root or "."))
        shell    = cfg.get("use_shell", True)

        self._log(f"Shell '{eid}': {command[:60]}")
        try:
            proc = subprocess.Popen(
                command, shell=shell, cwd=wdir, env=env,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=False
            )
            try:
                out_b, err_b = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.communicate()
                return _result(False, eid, "shell", t0,
                               error=f"Timed out after {timeout}s")

            stdout = out_b.decode("utf-8", errors="replace")
            stderr = err_b.decode("utf-8", errors="replace")
            ok     = proc.returncode == 0
            output = {}
            if ok and stdout.strip():
                try:    output = json.loads(stdout.strip())
                except: output = {"text": stdout.strip()}
            return _result(ok, eid, "shell", t0,
                           output=output, stdout=stdout, stderr=stderr,
                           error=f"Exit {proc.returncode}: {stderr[:200]}" if not ok else None)
        except Exception as e:
            return _result(False, eid, "shell", t0, error=str(e))

    def _run_http(self, eid: str, cfg: Dict, context: Dict, env: Dict) -> Dict:
        t0      = time.time()
        url     = self._substitute(cfg.get("url", cfg.get("base_url", "")), context)
        method  = cfg.get("method", "POST").upper()
        timeout = cfg.get("timeout_seconds", 30)
        headers = {"Content-Type": "application/json"}

        auth = cfg.get("auth", "none")
        if auth in ("bearer_env", "bearer"):
            tok = env.get(cfg.get("env_var", "API_TOKEN"), "")
            if tok: headers["Authorization"] = f"Bearer {tok}"

        body_map = cfg.get("body_mapping")
        body     = {k: context.get(v, v) for k, v in body_map.items()} if body_map else context

        self._log(f"HTTP '{eid}': {method} {url[:60]}")
        try:
            data = json.dumps(body).encode() if method != "GET" else None
            req  = urllib.request.Request(url, data=data, headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw    = r.read().decode("utf-8", errors="replace")
                status = r.status
            output = {}
            try:    output = json.loads(raw)
            except: output = {"raw": raw}
            ok = 200 <= status < 300
            return _result(ok, eid, "http", t0, output=output, stdout=raw,
                           error=f"HTTP {status}" if not ok else None,
                           extra={"status_code": status})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            return _result(False, eid, "http", t0, stdout=raw,
                           error=f"HTTP {e.code}: {raw[:200]}",
                           extra={"status_code": e.code})
        except Exception as e:
            return _result(False, eid, "http", t0, error=str(e))

    def _run_python_inline(self, eid: str, cfg: Dict, context: Dict) -> Dict:
        """Run a small inline Python snippet from project.json."""
        t0   = time.time()
        code = cfg.get("code", "")
        if not code:
            return _result(False, eid, "python_inline", t0, error="No code defined")

        local_vars = {"context": context, "output": {}, "result": None}
        try:
            exec(compile(code, f"<executor:{eid}>", "exec"), {}, local_vars)
            output = local_vars.get("output", {})
            if not isinstance(output, dict):
                output = {"value": output}
            return _result(True, eid, "python_inline", t0, output=output)
        except Exception as e:
            return _result(False, eid, "python_inline", t0, error=str(e))

    # ── Clawd-Code agent loop ─────────────────────────────────────────────────

    def _load_clawd_skills_markdown(self, cfg: Dict, env: Dict) -> str:
        """
        Load skills and return combined markdown to append to system prompt.

        Sources checked in priority order:
          1. .rocko_skills/    — skills downloaded from skills.sh by the bridge (CEO delegation + manual)
          2. Clawd-Code dirs   — ~/.clawd/skills, project .clawd/skills (if Clawd available)

        Skills listed in cfg['skills'] are loaded by name/id.
        If cfg['skills'] is empty, all available skills from all sources are loaded.
        This is the alignment point between skills.sh and Clawd-Code.
        """
        skill_names: List[str] = cfg.get("skills", [])
        parts: List[str] = []
        loaded_names: set = set()

        # ── Source 1: .rocko_skills/ — bridge-downloaded skills.sh content ─────
        if self._project_root:
            rocko_skills_dir = Path(self._project_root) / ".rocko_skills"
            if rocko_skills_dir.exists():
                for skill_file in sorted(rocko_skills_dir.glob("*.md")):
                    # File name format: owner__repo__skillname.md  (from _skillssh_fetch)
                    skill_key = skill_file.stem.replace("__", "/")
                    skill_short = skill_file.stem.split("__")[-1] if "__" in skill_file.stem else skill_file.stem
                    # Load if: no filter, or matches by full key or short name
                    if not skill_names or skill_key in skill_names or skill_short in skill_names:
                        try:
                            with open(skill_file, "r", encoding="utf-8") as f:
                                md = f.read()
                            parts.append(f"\n\n---\n## Skill: {skill_short}\n{md}")
                            loaded_names.add(skill_short)
                            self._log(f"Skill loaded from .rocko_skills: {skill_short}")
                        except Exception as e:
                            self._log(f"Skill read error {skill_file.name}: {e}")

        # ── Source 2: Clawd-Code skill dirs ──────────────────────────────────────
        if _CLAWD_AVAILABLE:
            project_root = self._project_root or "."
            try:
                all_clawd_skills = get_all_skills(
                    project_root=project_root,
                    user_skills_dir=cfg.get("skills_dir") or None,
                )
                for skill in (all_clawd_skills or []):
                    if skill.name in loaded_names:
                        continue  # already loaded from .rocko_skills
                    if not skill_names or skill.name in skill_names:
                        parts.append(f"\n\n---\n## Skill: {skill.name}\n{skill.markdown_content}")
                        loaded_names.add(skill.name)
                        self._log(f"Skill loaded from Clawd: {skill.name} ({skill.loaded_from})")
            except Exception as e:
                self._log(f"Clawd skills load warning: {e}")

        if not parts:
            self._log("No skills loaded (none in .rocko_skills/ or Clawd dirs)")

        return "".join(parts)

    def _build_clawd_provider(self, cfg: Dict, env: Dict):
        """
        Build a Clawd-Code provider from executor + project model config.
        Resolution order for each field:
          executor cfg → project.json model block → environment defaults
        """
        project_model  = self._project.get("model", {})
        provider_name  = cfg.get("provider") or project_model.get("default_provider", "anthropic")
        model          = cfg.get("model") or project_model.get("default_model", "claude-sonnet-4-5-20250929")
        providers_cfg  = project_model.get("providers", {})
        provider_block = providers_cfg.get(provider_name, {})
        base_url       = cfg.get("base_url") or provider_block.get("api_base") or None

        api_key_env = cfg.get("api_key_env") or provider_block.get("api_key_env", "ANTHROPIC_API_KEY")
        api_key     = cfg.get("api_key") or env.get(api_key_env, "")

        if not api_key:
            raise ValueError(
                f"No API key for provider '{provider_name}'. "
                f"Set {api_key_env} in your .env file."
            )

        provider_cls = get_provider_class(provider_name)
        return provider_cls(api_key=api_key, base_url=base_url, model=model)

    def _build_clawd_tool_registry(self, cfg: Dict) -> "ToolRegistry":
        """
        Build a Clawd-Code ToolRegistry scoped by risk level or explicit allow list.
        Risk level defaults:
          read_only → Read, Glob, Grep, WebFetch, WebSearch
          write     → + Write, Edit
          deploy/financial → + Bash (still requires approval gate above)
        cfg['allowed_tools'] overrides the risk-level defaults completely.
        """
        risk = cfg.get("risk_level", "read_only")
        if cfg.get("allowed_tools"):
            allowed_names = set(cfg["allowed_tools"])
        elif risk == "read_only":
            allowed_names = set(_CLAWD_TOOLS_READ_ONLY)
        elif risk == "write":
            allowed_names = set(_CLAWD_TOOLS_WRITE)
        else:
            allowed_names = set(_CLAWD_TOOLS_FULL)

        registry = ToolRegistry()

        # Clawd-Code bundled tools directory
        clawd_tool_dirs = [Path(_CLAWD_PATH) / "src" / "tool_system" / "tools"]

        # Project-local tools directory (drop custom tool files here)
        if self._project_root:
            local_tools = Path(self._project_root) / "tools"
            if local_tools.exists():
                clawd_tool_dirs.append(local_tools)

        for tool_dir in clawd_tool_dirs:
            if not tool_dir.exists():
                continue
            try:
                for tool in load_tools_from_dir(tool_dir):
                    spec = tool.spec()
                    if spec.name in allowed_names:
                        try:
                            registry.register(tool)
                            allowed_names.discard(spec.name)
                        except ValueError:
                            pass  # duplicate — already registered
            except Exception as e:
                self._log(f"Tool load warning from {tool_dir}: {e}")

        loaded = [s.name for s in registry.list_specs()]
        self._log(f"Clawd tools loaded: {loaded or 'none'}")
        return registry

    def _build_clawd_tool_context(self, cfg: Dict) -> "ToolContext":
        """
        Build a ToolContext scoped to the project root.
        Bash is blocked unless risk level explicitly permits it.
        """
        workspace = Path(self._project_root).resolve() if self._project_root else Path.cwd()
        risk      = cfg.get("risk_level", "read_only")

        deny_tools = set()
        if risk not in ("deploy", "financial"):
            deny_tools.add("Bash")

        perm_ctx = ToolPermissionContext(
            workspace_root=workspace,
            deny_names=deny_tools,
        )
        return ToolContext(
            workspace_root=workspace,
            permission_context=perm_ctx,
        )

    def _run_clawd_agent(self, eid: str, cfg: Dict, context: Dict, env: Dict) -> Dict:
        """
        Execute a multi-turn Clawd-Code agent loop.

        Executor config fields (all optional except type):
          system_prompt   — base system prompt injected before skills
          instructions    — alias for system_prompt
          max_turns       — maximum tool-calling turns (default: 8)
          skills          — list of skill names to inject (default: all available)
          skills_dir      — override Clawd skills directory
          allowed_tools   — explicit tool allow list (overrides risk-level defaults)
          provider        — llm provider: anthropic | openai | glm | minimax
          model           — model override
          api_key_env     — env var for api key (default: ANTHROPIC_API_KEY)
          stream          — stream text chunks to log (default: False)
          timeout_seconds — wall-clock timeout (default: 120)

        Input:  pipeline context dict → serialised as JSON user message
        Output: agent final response parsed as JSON, or {"text": raw_response}
        """
        t0 = time.time()

        if not _CLAWD_AVAILABLE:
            return _result(False, eid, "clawd_agent", t0,
                           error=(
                               f"Clawd-Code not available. "
                               f"Ensure Clawd-Code is cloned at: {_CLAWD_PATH}. "
                               f"Import error: {_CLAWD_IMPORT_ERROR}"
                           ))

        max_turns = cfg.get("max_turns", 8)
        timeout   = cfg.get("timeout_seconds", 120)
        stream    = cfg.get("stream", False)

        # Build system prompt: base instructions + skill markdown appended
        base_instructions = (
            cfg.get("system_prompt")
            or cfg.get("instructions")
            or "You are an autonomous agent. Complete the task described in the input."
        )
        skill_markdown    = self._load_clawd_skills_markdown(cfg, env)
        full_system_prompt = base_instructions + skill_markdown

        # Pipeline context becomes the initial user message
        user_message = json.dumps(context, indent=2) if context else "{}"

        tool_events: List[Dict] = []
        text_chunks: List[str]  = []

        def on_event(event: "ToolEvent"):
            entry: Dict = {"kind": event.kind, "tool": event.tool_name}
            if event.tool_input:
                entry["input"] = event.tool_input
            if event.is_error:
                entry["error"] = event.error
            tool_events.append(entry)
            self._log(f"  [TOOL] {event.kind} → {event.tool_name}")

        def on_text_chunk(chunk: str):
            text_chunks.append(chunk)

        self._log(f"Clawd agent '{eid}' — max_turns={max_turns} provider={cfg.get('provider', 'project_default')}")

        try:
            provider = self._build_clawd_provider(cfg, env)
            registry = self._build_clawd_tool_registry(cfg)
            tool_ctx = self._build_clawd_tool_context(cfg)

            conversation = Conversation(system=full_system_prompt)
            conversation.add_user_message(user_message)

            # Run agent loop in a daemon thread to enforce wall-clock timeout
            import threading
            loop_result: List[Optional["AgentLoopResult"]] = [None]
            loop_error:  List[Optional[str]]               = [None]

            def _run_loop():
                try:
                    loop_result[0] = run_agent_loop(
                        conversation=conversation,
                        provider=provider,
                        tool_registry=registry,
                        tool_context=tool_ctx,
                        max_turns=max_turns,
                        stream=stream,
                        verbose=False,
                        on_event=on_event,
                        on_text_chunk=on_text_chunk,
                    )
                except Exception as exc:
                    loop_error[0] = str(exc)

            thread = threading.Thread(target=_run_loop, daemon=True)
            thread.start()
            thread.join(timeout=timeout)

            if thread.is_alive():
                return _result(False, eid, "clawd_agent", t0,
                               error=f"Clawd agent timed out after {timeout}s",
                               stdout="".join(text_chunks),
                               extra={"tool_events": tool_events})

            if loop_error[0]:
                return _result(False, eid, "clawd_agent", t0,
                               error=loop_error[0],
                               stdout="".join(text_chunks),
                               extra={"tool_events": tool_events})

            result = loop_result[0]
            if not result:
                return _result(False, eid, "clawd_agent", t0,
                               error="Agent loop returned no result",
                               extra={"tool_events": tool_events})

            # Parse final response — try JSON block, then raw JSON, then plain text
            response_text = result.response_text or ""
            output: Dict = {}
            stripped = response_text.strip()

            if "```json" in stripped:
                try:
                    json_block = stripped.split("```json")[1].split("```")[0].strip()
                    output = json.loads(json_block)
                except Exception:
                    pass

            if not output and stripped.startswith("{"):
                try:
                    output = json.loads(stripped)
                except Exception:
                    pass

            if not output:
                output = {"text": response_text}

            usage = result.usage or {}
            self._log(
                f"Clawd agent '{eid}' complete — "
                f"turns={result.num_turns} "
                f"in={usage.get('input_tokens', 0)} "
                f"out={usage.get('output_tokens', 0)}"
            )

            return _result(True, eid, "clawd_agent", t0,
                           output=output,
                           stdout=response_text,
                           extra={
                               "tool_events": tool_events,
                               "num_turns":   result.num_turns,
                               "usage":       usage,
                           })

        except Exception as e:
            return _result(False, eid, "clawd_agent", t0,
                           error=str(e),
                           stdout="".join(text_chunks),
                           extra={"tool_events": tool_events})

    # ── Skills public API (called by bridge /skills/browse) ───────────────────

    def get_available_skills(self, skills_dir: Optional[str] = None) -> List[Dict]:
        """
        Return all available skills as serialisable dicts.
        Merges two sources:
          1. .rocko_skills/  — skills downloaded from skills.sh (primary, always checked)
          2. Clawd-Code dirs — ~/.clawd/skills, project .clawd/skills (if available)
        The bridge /skills/browse endpoint can call this for a local inventory.
        """
        result: List[Dict] = []
        seen: set = set()

        # Source 1: .rocko_skills/ — skills.sh downloaded content
        if self._project_root:
            rocko_dir = Path(self._project_root) / ".rocko_skills"
            if rocko_dir.exists():
                for skill_file in sorted(rocko_dir.glob("*.md")):
                    skill_id = skill_file.stem.replace("__", "/")
                    name     = skill_file.stem.split("__")[-1] if "__" in skill_file.stem else skill_file.stem
                    if name in seen:
                        continue
                    seen.add(name)
                    try:
                        with open(skill_file, "r", encoding="utf-8") as f:
                            md = f.read()
                        result.append({
                            "id":          skill_id,
                            "name":        name,
                            "description": "",
                            "loaded_from": str(skill_file),
                            "source":      "skills.sh",
                            "cached":      True,
                        })
                    except Exception:
                        pass

        # Source 2: Clawd-Code dirs
        if _CLAWD_AVAILABLE:
            try:
                clawd_skills = get_all_skills(
                    project_root=self._project_root or None,
                    user_skills_dir=skills_dir or None,
                )
                for s in (clawd_skills or []):
                    if s.name in seen:
                        continue
                    seen.add(s.name)
                    result.append({
                        "id":             s.name,
                        "name":           s.name,
                        "description":    s.description,
                        "loaded_from":    s.loaded_from,
                        "version":        s.version,
                        "when_to_use":    s.when_to_use,
                        "allowed_tools":  list(s.allowed_tools or []),
                        "user_invocable": s.user_invocable,
                        "skill_root":     s.skill_root,
                        "source":         "clawd",
                        "cached":         False,
                    })
            except Exception as e:
                self._log(f"get_available_skills Clawd error: {e}")

        return result

    def get_skill_markdown(self, skill_name: str,
                           skills_dir: Optional[str] = None) -> Optional[str]:
        """Return raw SKILL.md markdown for a named skill."""
        if not _CLAWD_AVAILABLE:
            return None
        try:
            skills = get_all_skills(
                project_root=self._project_root or None,
                user_skills_dir=skills_dir or None,
            )
            for s in skills:
                if s.name == skill_name:
                    return s.markdown_content
        except Exception:
            pass
        return None

    # ── Risk + approval ───────────────────────────────────────────────────────

    def requires_approval(self, executor_id: str) -> bool:
        cfg  = self._executors.get(executor_id, {})
        risk = cfg.get("risk_level", "read_only")
        return risk in ALWAYS_HUMAN

    def get_risk_level(self, executor_id: str) -> str:
        return self._executors.get(executor_id, {}).get("risk_level", "read_only")

    # ── Main run ──────────────────────────────────────────────────────────────

    def run(self, executor_id: str, context: Dict,
            dry_run: bool = False, bypass_approval: bool = False) -> Dict:
        cfg = self._executors.get(executor_id)
        if not cfg:
            return _result(False, executor_id, "unknown", time.time(),
                           error=f"Executor '{executor_id}' not defined in project.json")

        risk = cfg.get("risk_level", "read_only")
        if risk in ALWAYS_HUMAN and not bypass_approval:
            return _result(False, executor_id, cfg.get("type", "?"), time.time(),
                           error=f"Risk level '{risk}' requires human approval",
                           extra={"requires_approval": True, "risk_level": risk})

        if dry_run:
            return _result(True, executor_id, cfg.get("type", "?"), time.time(),
                           output={"status": "dry_run", "risk_level": risk})

        env   = self._load_env()
        etype = cfg.get("type", cfg.get("run_mode", "python_script"))

        if etype in ("clawd_agent", "clawd", "agent_loop"):
            result = self._run_clawd_agent(executor_id, cfg, context, env)
        elif etype in ("python_script", "python", "script"):
            result = self._run_python_script(executor_id, cfg, context, env)
        elif etype in ("shell", "bash", "cmd", "command"):
            result = self._run_shell(executor_id, cfg, context, env)
        elif etype in ("http", "http_call", "api"):
            result = self._run_http(executor_id, cfg, context, env)
        elif etype == "python_inline":
            result = self._run_python_inline(executor_id, cfg, context)
        else:
            result = _result(False, executor_id, etype, time.time(),
                             error=(
                                 f"Unknown executor type: '{etype}'. "
                                 f"Supported: clawd_agent, python_script, shell, http, python_inline"
                             ))

        self._record(executor_id, result)
        sym = "OK" if result["ok"] else "FAIL"
        self._log(f"{sym} Executor '{executor_id}' ({etype}) {result['duration_ms']}ms")
        return result

    def _record(self, executor_id: str, result: Dict):
        h = self._run_history.setdefault(executor_id, [])
        h.insert(0, result)
        if len(h) > 20:
            self._run_history[executor_id] = h[:20]

    def get_executors(self) -> List[Dict]:
        out = []
        for eid, cfg in self._executors.items():
            last   = (self._run_history.get(eid) or [{}])[0]
            script = cfg.get("script_path", cfg.get("command", cfg.get("url", "")))
            etype  = cfg.get("type", cfg.get("run_mode", "python_script"))
            out.append({
                "id":                eid,
                "type":              etype,
                "description":       cfg.get("description", ""),
                "risk_level":        cfg.get("risk_level", "read_only"),
                "requires_approval": self.requires_approval(eid),
                "script":            script,
                "clawd_enabled":     etype in ("clawd_agent", "clawd", "agent_loop") and _CLAWD_AVAILABLE,
                "last_run_at":       last.get("completed_at"),
                "last_ok":           last.get("ok"),
                "last_error":        last.get("error"),
                "run_count":         len(self._run_history.get(eid, [])),
            })
        return sorted(out, key=lambda x: x["id"])

    def reload(self):
        self._env_cache   = None
        self._skill_cache = None
        self._load_executors()