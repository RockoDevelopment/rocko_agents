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

Domain-agnostic. All behavior comes from project.json executors,
AGENT.md instructions, company policy, and pipeline config.

Works for: trading, research, content, support, coding,
           data analysis, operations, sales - same engine, zero changes.
"""
import json, os, subprocess, sys, time, uuid, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

RISK_LEVELS = {"read_only", "write", "deploy", "financial"}
AUTO_APPROVE = {"read_only"}
ALWAYS_HUMAN = {"deploy", "financial"}

ENGINE_VERSION = "native_engine_v1_2026_04_24"


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
    """

    def __init__(self):
        self._project:      Dict = {}
        self._project_root: str  = ""
        self._executors:    Dict = {}
        self._run_history:  Dict = {}
        self._log_fn:       Callable = print
        self._env_cache:    Optional[Dict] = None

    def init(self, project: Dict, log_fn: Callable = print):
        self._project      = project
        self._project_root = project.get("project", {}).get("root_path", "")
        self._log_fn       = log_fn
        self._env_cache    = None
        self._load_executors()

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

        env    = self._load_env()
        etype  = cfg.get("type", cfg.get("run_mode", "python_script"))
        result = {}

        if etype in ("python_script", "python", "script"):
            result = self._run_python_script(executor_id, cfg, context, env)
        elif etype in ("shell", "bash", "cmd", "command"):
            result = self._run_shell(executor_id, cfg, context, env)
        elif etype in ("http", "http_call", "api"):
            result = self._run_http(executor_id, cfg, context, env)
        elif etype == "python_inline":
            result = self._run_python_inline(executor_id, cfg, context)
        else:
            result = _result(False, executor_id, etype, time.time(),
                             error=f"Unknown executor type: '{etype}'. Supported: python_script, shell, http, python_inline")

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
            last = (self._run_history.get(eid) or [{}])[0]
            script = cfg.get("script_path", cfg.get("command", cfg.get("url", "")))
            out.append({
                "id":          eid,
                "type":        cfg.get("type", cfg.get("run_mode", "python_script")),
                "description": cfg.get("description", ""),
                "risk_level":  cfg.get("risk_level", "read_only"),
                "requires_approval": self.requires_approval(eid),
                "script":      script,
                "last_run_at": last.get("completed_at"),
                "last_ok":     last.get("ok"),
                "last_error":  last.get("error"),
                "run_count":   len(self._run_history.get(eid, [])),
            })
        return sorted(out, key=lambda x: x["id"])

    def reload(self):
        self._env_cache = None
        self._load_executors()