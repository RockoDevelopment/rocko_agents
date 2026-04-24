"""
RockoAgents Runtime Manager
Orchestrates external agent systems as first-class workers.

Supported runtime types:
  cli        - subprocess CLI tool (OpenClaw, Claude Code, any local agent CLI)
  http       - HTTP API call (local agent server, hosted service)
  webhook    - event dispatch with optional async callback
  mcp        - future: MCP tool-server protocol
  python     - existing Python script executor (unified here)

Security model:
  - Commands come from project.json only - never from agent text
  - Args use {{variable}} substitution from approved context keys only
  - Secrets loaded from .env - never logged, never sent to browser
  - allowed_agents enforced per runtime
  - risk_level determines whether human approval is required
  - Timeout and working-dir boundaries enforced on every call
"""
import json, os, subprocess, sys, time, uuid, urllib.request, urllib.error
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

RISK_LEVELS             = {"read_only", "write", "deploy", "financial"}
ALWAYS_REQUIRE_APPROVAL = {"deploy", "financial"}

def _result(ok, runtime_id, rtype, t0, output=None, stdout="", stderr="", error=None, extra=None):
    return {
        "ok":           ok,
        "runtime_id":   runtime_id,
        "type":         rtype,
        "started_at":   datetime.fromtimestamp(t0).isoformat(),
        "completed_at": datetime.now().isoformat(),
        "duration_ms":  round((time.time() - t0) * 1000),
        "output":       output or {},
        "stdout":       stdout,
        "stderr":       stderr,
        "error":        error,
        **(extra or {}),
    }

class RuntimeManager:
    def __init__(self):
        self._project_root = ""
        self._runtimes:    Dict[str, Dict] = {}
        self._run_history: Dict[str, List] = {}
        self._log_fn       = print
        self._env_cache:   Optional[Dict] = None
        self._project:     Dict = {}

    def init(self, project: Dict, log_fn: Callable = print):
        self._project      = project
        self._project_root = project.get("project", {}).get("root_path", "")
        self._log_fn       = log_fn
        self._env_cache    = None
        self._load_runtimes()

    def _log(self, msg: str):
        self._log_fn(f"[RUNTIME ] {msg}")

    def _load_runtimes(self):
        raw = self._project.get("runtimes", {})
        self._runtimes = {}
        for rid, cfg in raw.items():
            err = self._validate(rid, cfg)
            if err:
                self._log(f"Runtime '{rid}' config error: {err}")
            else:
                self._runtimes[rid] = {**cfg, "id": rid}
        for eid, ex in self._project.get("executors", {}).items():
            if "runtime_id" in ex:
                self._runtimes[f"__exec_{eid}"] = {
                    "id": f"__exec_{eid}", "type": "executor_ref",
                    "executor_id": eid, "runtime_id": ex["runtime_id"],
                    "description": ex.get("description", ""),
                }
        self._log(f"Loaded {len([k for k in self._runtimes if not k.startswith('__')])} runtime(s)")

    def _validate(self, rid: str, cfg: Dict) -> Optional[str]:
        rtype = cfg.get("type")
        if not rtype:              return "missing type"
        if rtype == "cli" and not cfg.get("command"): return "cli missing command"
        if rtype == "http" and not cfg.get("base_url"): return "http missing base_url"
        if rtype == "webhook" and not cfg.get("url"):  return "webhook missing url"
        risk = cfg.get("risk_level", "read_only")
        if risk not in RISK_LEVELS: return f"invalid risk_level '{risk}'"
        return None

    def _load_env(self, env_file: Optional[str] = None) -> Dict:
        if self._env_cache is not None:
            return self._env_cache
        env = os.environ.copy()
        ef  = env_file or self._project.get("env", {}).get("env_file", ".env")
        ep  = Path(self._project_root) / ef if self._project_root else Path(ef)
        if ep.exists():
            with open(ep) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.split("=", 1)
                        env[k.strip()] = v.strip().strip('"').strip("'")
        self._env_cache = env
        return env

    def check_permission(self, runtime_id: str, agent_id: Optional[str]) -> Dict:
        cfg = self._runtimes.get(runtime_id)
        if not cfg:
            return {"allowed": False, "reason": f"Runtime '{runtime_id}' not defined"}
        allowed = cfg.get("allowed_agents", [])
        if allowed and agent_id and agent_id not in allowed:
            return {"allowed": False,
                    "reason": f"Agent '{agent_id}' not in allowed_agents for '{runtime_id}'"}
        return {"allowed": True}

    def requires_approval(self, runtime_id: str) -> bool:
        return self._runtimes.get(runtime_id, {}).get("risk_level", "read_only") in ALWAYS_REQUIRE_APPROVAL

    def get_risk_level(self, runtime_id: str) -> str:
        return self._runtimes.get(runtime_id, {}).get("risk_level", "read_only")

    def _sub(self, template: str, context: Dict) -> str:
        result = template
        safe = {"PROJECT_ROOT": self._project_root, "project_root": self._project_root}
        for k, v in context.items():
            if isinstance(v, (str, int, float)):
                safe[k] = str(v)
        for k, v in safe.items():
            result = result.replace("{{" + k + "}}", v)
        return result

    def _sub_list(self, args: List[str], context: Dict) -> List[str]:
        return [self._sub(a, context) for a in args]

    def _safe_dir(self, d: str, allow_outside: bool = False) -> str:
        try:
            resolved = Path(d).resolve()
            if not allow_outside and self._project_root:
                resolved.relative_to(Path(self._project_root).resolve())
            return str(resolved)
        except Exception:
            return self._project_root or "."

    def _run_cli(self, runtime_id: str, cfg: Dict, context: Dict, env: Dict) -> Dict:
        t0      = time.time()
        command = cfg["command"]
        args    = self._sub_list(cfg.get("args", []), context)
        cmd     = [command] + args
        wdir    = self._safe_dir(self._sub(cfg.get("working_dir", self._project_root or "."), context),
                                  cfg.get("allow_outside_root", False))
        timeout  = cfg.get("timeout_seconds", 120)
        in_mode  = cfg.get("input_mode", "none")
        out_mode = cfg.get("output_mode", "stdout_json")
        stdin_d  = json.dumps(context).encode() if in_mode == "stdin_json" else None
        self._log(f"CLI '{runtime_id}': {command} {args[:2]}")
        try:
            proc = subprocess.Popen(cmd, cwd=wdir, env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    stdin=subprocess.PIPE if stdin_d else None, text=False)
            try:
                out_b, err_b = proc.communicate(input=stdin_d, timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try: proc.communicate(timeout=5)
                except: pass
                return _result(False, runtime_id, "cli", t0,
                               error=f"CLI timed out after {timeout}s")
            out = out_b.decode("utf-8", errors="replace")
            err = err_b.decode("utf-8", errors="replace")
            ok  = proc.returncode == 0
            output = {}
            if ok and out.strip():
                if out_mode == "stdout_json":
                    try:    output = json.loads(out.strip())
                    except: output = {"raw": out.strip()}
                else:
                    output = {"text": out.strip()}
            return _result(ok, runtime_id, "cli", t0, output=output, stdout=out, stderr=err,
                           error=f"Exit {proc.returncode}: {err[:200]}" if not ok else None)
        except FileNotFoundError:
            return _result(False, runtime_id, "cli", t0,
                           error=f"Command not found: '{command}' - is it installed and on PATH?")
        except Exception as e:
            return _result(False, runtime_id, "cli", t0, error=str(e))

    def _run_http(self, runtime_id: str, cfg: Dict, context: Dict, env: Dict) -> Dict:
        t0       = time.time()
        base     = cfg["base_url"].rstrip("/")
        endpoint = cfg.get("endpoint", "/")
        url      = base + ("/" + endpoint.lstrip("/") if endpoint and endpoint != "/" else "")
        method   = cfg.get("method", "POST").upper()
        timeout  = cfg.get("timeout_seconds", 60)
        headers  = {"Content-Type": "application/json"}
        auth     = cfg.get("auth", "none")
        if auth in ("bearer_env", "bearer"):
            tok = env.get(cfg.get("env_var", "API_TOKEN"), "")
            if tok: headers["Authorization"] = f"Bearer {tok}"
        elif auth == "header_env":
            val = env.get(cfg.get("env_var", "API_KEY"), "")
            if val: headers[cfg.get("header_name", "X-API-Key")] = val
        body_map = cfg.get("body_mapping")
        body     = {k: context.get(v, v) for k, v in body_map.items()} if body_map else context
        self._log(f"HTTP '{runtime_id}': {method} {url}")
        try:
            payload = json.dumps(body).encode()
            req = urllib.request.Request(url, data=payload if method != "GET" else None,
                                         headers=headers, method=method)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw    = resp.read().decode("utf-8", errors="replace")
                status = resp.status
            output = {}
            try:    output = json.loads(raw)
            except: output = {"raw": raw}
            ok = 200 <= status < 300
            return _result(ok, runtime_id, "http", t0, output=output, stdout=raw,
                           error=f"HTTP {status}" if not ok else None,
                           extra={"status_code": status})
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace")
            return _result(False, runtime_id, "http", t0, stdout=raw,
                           error=f"HTTP {e.code}: {raw[:200]}", extra={"status_code": e.code})
        except Exception as e:
            return _result(False, runtime_id, "http", t0, error=str(e))

    def _run_webhook(self, runtime_id: str, cfg: Dict, context: Dict, env: Dict) -> Dict:
        t0      = time.time()
        url     = cfg["url"]
        timeout = cfg.get("timeout_seconds", 30)
        corr_id = str(uuid.uuid4())
        headers = {"Content-Type": "application/json", "X-Correlation-ID": corr_id}
        auth    = cfg.get("auth", "none")
        if auth in ("bearer_env", "bearer"):
            tok = env.get(cfg.get("env_var", "WEBHOOK_TOKEN"), "")
            if tok: headers["Authorization"] = f"Bearer {tok}"
        tpl     = cfg.get("payload_template", {})
        payload = ({k: self._sub(str(v), context) for k, v in tpl.items()}
                   if tpl else {"context": context, "correlation_id": corr_id})
        self._log(f"Webhook '{runtime_id}': POST {url}")
        try:
            req = urllib.request.Request(url, data=json.dumps(payload).encode(),
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw    = resp.read().decode("utf-8", errors="replace")
                status = resp.status
            output = {}
            try:    output = json.loads(raw)
            except: output = {"raw": raw, "delivered": True}
            ok = 200 <= status < 300
            return _result(ok, runtime_id, "webhook", t0, output=output, stdout=raw,
                           error=f"HTTP {status}" if not ok else None,
                           extra={"correlation_id": corr_id, "status_code": status})
        except Exception as e:
            return _result(False, runtime_id, "webhook", t0,
                           error=str(e), extra={"correlation_id": corr_id})

    def run(self, runtime_id: str, context: Dict,
            agent_id: Optional[str] = None, dry_run: bool = False) -> Dict:
        cfg = self._runtimes.get(runtime_id)
        if not cfg:
            return _result(False, runtime_id, "unknown", time.time(),
                           error=f"Runtime '{runtime_id}' not defined in project.json")
        perm = self.check_permission(runtime_id, agent_id)
        if not perm["allowed"]:
            return _result(False, runtime_id, cfg.get("type","?"), time.time(),
                           error=f"Permission denied: {perm['reason']}")
        if self.requires_approval(runtime_id):
            return _result(False, runtime_id, cfg.get("type","?"), time.time(),
                           error=f"Risk level '{cfg.get('risk_level')}' requires human approval",
                           extra={"requires_approval": True})
        if dry_run:
            return _result(True, runtime_id, cfg.get("type","?"), time.time(),
                           output={"status": "dry_run"})
        rtype  = cfg.get("type", "")
        env    = self._load_env(cfg.get("env_file"))
        if rtype == "cli":
            result = self._run_cli(runtime_id, cfg, context, env)
        elif rtype == "http":
            result = self._run_http(runtime_id, cfg, context, env)
        elif rtype == "webhook":
            result = self._run_webhook(runtime_id, cfg, context, env)
        elif rtype in ("python", "python_script", "executor_ref"):
            result = _result(True, runtime_id, rtype, time.time(),
                             output={"delegated": True},
                             extra={"delegate_to_executor": cfg.get("executor_id", runtime_id)})
        elif rtype == "mcp":
            result = _result(False, runtime_id, "mcp", time.time(),
                             error="MCP runtime: reserved for future implementation")
        else:
            result = _result(False, runtime_id, rtype, time.time(),
                             error=f"Unknown runtime type: '{rtype}'")
        self._record(runtime_id, result)
        status_word = "OK" if result["ok"] else "FAIL"
        self._log(f"{status_word} Runtime '{runtime_id}': {result['duration_ms']}ms")
        return result

    def _record(self, runtime_id: str, result: Dict):
        h = self._run_history.setdefault(runtime_id, [])
        h.insert(0, result)
        if len(h) > 20: self._run_history[runtime_id] = h[:20]

    def get_runtimes(self) -> List[Dict]:
        out = []
        for rid, cfg in self._runtimes.items():
            if rid.startswith("__"): continue
            last = (self._run_history.get(rid) or [{}])[0]
            out.append({
                "id": rid, "type": cfg.get("type","?"),
                "description": cfg.get("description",""),
                "risk_level": cfg.get("risk_level","read_only"),
                "allowed_agents": cfg.get("allowed_agents",[]),
                "requires_approval": self.requires_approval(rid),
                "last_run_at": last.get("completed_at"),
                "last_ok": last.get("ok"), "last_error": last.get("error"),
                "run_count": len(self._run_history.get(rid,[])),
            })
        return sorted(out, key=lambda x: x["id"])

    def get_runtime(self, runtime_id: str) -> Optional[Dict]:
        cfg = self._runtimes.get(runtime_id)
        if not cfg: return None
        safe = {k: v for k, v in cfg.items()
                if k.lower() not in ("env_var","token","secret","password","api_key")}
        return {**safe, "run_history": self._run_history.get(runtime_id,[])[:5]}

    def reload(self):
        self._env_cache = None
        self._load_runtimes()