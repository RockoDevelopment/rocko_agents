"""
RockoAgents Executor Bridge v5.0
Integrates: scheduler, task worker, CEO orchestrator, model manager.
Run: python bridge.py --port 8787
"""
import urllib.request
import argparse, json, os, subprocess, sys, threading, time, traceback, uuid, webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles
    from pydantic import BaseModel
    import uvicorn
except ImportError:
    print("\nERROR: pip install -r requirements.txt\n"); sys.exit(1)

# -- Paths ---------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    # Running as PyInstaller compiled executable
    BRIDGE_DIR = Path(sys.executable).parent.resolve()
    ROCKO_ROOT = BRIDGE_DIR
else:
    # Running as Python script
    BRIDGE_DIR = Path(__file__).parent.resolve()
    ROCKO_ROOT = BRIDGE_DIR.parent.resolve()
    sys.path.insert(0, str(BRIDGE_DIR))

BRIDGE_START    = datetime.now().isoformat()
SKILLS_SH_API     = "https://skills.sh/api/v1"
# Optional: set SKILLS_SH_API_KEY in .env for 600 req/min instead of 60 req/min
# Keys issued on request at skills.sh — unauthenticated still works at 60 req/min per IP
SKILLS_SH_API_KEY = os.environ.get("SKILLS_SH_API_KEY", "")
BRIDGE_BUILD_ID = "bridge_package_import_fix_2026_04_24"
_port = 8787  # updated in cli_main

# -- Core state ----------------------------------------------------------------
PROJECT:        Dict = {}
PROJECT_ROOT:   str  = ""
APP_DATA_DIR:       Path = ROCKO_ROOT / "data" / "rockoagents"
PROJECT_DATA_DIR:   Path = APP_DATA_DIR
DATA_DIR:           Path = PROJECT_DATA_DIR  # legacy alias for project-scoped runtime data
RUN_LOG:            Dict[str, Any] = {}
PIPELINE_RUNS:      List[Dict] = []

def _load_pipeline_runs():
    global PIPELINE_RUNS
    p = PROJECT_DATA_DIR / "pipeline_runs.json"
    if p.exists():
        try:
            with open(p) as f: PIPELINE_RUNS = json.load(f)
            _log("info", f"Pipeline run recovery: {len(PIPELINE_RUNS)} run(s) reloaded")
        except Exception: pass
PIPELINE_STATE: Dict = {}
LOG_BUFFER:     List = []
VERBOSE:        bool = False

# -- Subsystems (initialised after project loads) ------------------------------
_model_mgr    = None
_task_worker  = None
_scheduler    = None
_orchestrator = None
_runtime_mgr  = None
_exec_engine  = None

# -- App -----------------------------------------------------------------------
app = FastAPI(title="RockoAgents Bridge", version="5.0.0", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def _log(level: str, msg: str):
    entry = {"ts": datetime.now().isoformat(), "level": level, "msg": msg}
    LOG_BUFFER.append(entry)
    if len(LOG_BUFFER) > 500: LOG_BUFFER.pop(0)
    if VERBOSE or level == "error":
        print(f"[{level.upper():7}] {msg}")

# -- Models --------------------------------------------------------------------
class RunRequest(BaseModel):
    context:       Dict[str, Any] = {}
    input:         Dict[str, Any] = {}
    dry_run:       bool = False
    env_overrides: Dict[str, str] = {}

class PipelineRequest(BaseModel):
    input_data:    Dict[str, Any] = {}
    stop_on_error: bool = True
    dry_run:       bool = False

class FileReadRequest(BaseModel):
    path:    str
    project: Optional[str] = None

class DataSaveRequest(BaseModel):
    key:  str
    data: Any

class TaskCreateRequest(BaseModel):
    title:          str
    assigned_to:    str
    type:           str = "agent"
    instructions:   str = ""
    input:          Dict[str, Any] = {}
    parent_task_id: Optional[str] = None
    max_retries:    int = 1
    priority:       str = "normal"

class ScheduleCreateRequest(BaseModel):
    name:             str
    type:             str
    target_id:        str
    schedule_type:    str
    interval_seconds: Optional[int] = None
    cron:             Optional[str] = None
    enabled:          bool = True
    input:            Dict[str, Any] = {}

class ApprovalRequest(BaseModel):
    modifications: Dict[str, Any] = {}

class OrchestrateRequest(BaseModel):
    pipeline_context: Dict[str, Any] = {}
    step_id:          Optional[str] = None

class RoutingPlanRequest(BaseModel):
    reason:         str = ""
    apply:          bool = True
    allow_fallback: bool = False

class RoutingValidateRequest(BaseModel):
    routing: Dict[str, Any] = {}

class RuntimeRunRequest(BaseModel):
    context:   Dict[str, Any] = {}
    agent_id:  Optional[str] = None
    dry_run:   bool = False

# -- Project loading -----------------------------------------------------------
def load_project(path: str) -> bool:
    global PROJECT, PROJECT_ROOT, PROJECT_DATA_DIR, DATA_DIR
    try:
        p = Path(path).resolve()
        if not p.exists(): _log("error", f"project.json not found: {p}"); return False
        with open(p) as f: PROJECT = json.load(f)
        PROJECT_ROOT = str(Path(PROJECT["project"]["root_path"]).resolve())
        PROJECT_DATA_DIR = Path(PROJECT_ROOT) / "data" / "rockoagents"
        PROJECT_DATA_DIR.mkdir(parents=True, exist_ok=True)
        DATA_DIR = PROJECT_DATA_DIR  # keep legacy project-runtime callers working
        APP_DATA_DIR.mkdir(parents=True, exist_ok=True)
        _log("info", f"Project loaded: {PROJECT['project']['name']}")
        return True
    except Exception as e:
        _log("error", f"Project load error: {e}"); return False

def _resolve(rel: str) -> str:
    if not rel: return ""
    p = Path(rel)
    return str(p) if p.is_absolute() else str(Path(PROJECT_ROOT) / rel)

def _safe_path(path: str, allow_outside: bool = False) -> tuple:
    resolved = Path(_resolve(path)).resolve()
    root = Path(PROJECT_ROOT).resolve()
    try:
        resolved.relative_to(root); return str(resolved), None
    except ValueError:
        if allow_outside: return str(resolved), None
        return None, f"Path outside project root: {resolved}"

def get_executor(eid: str) -> Optional[Dict]:
    return PROJECT.get("executors", {}).get(eid)

def build_env(overrides: Dict = {}) -> Dict:
    env = os.environ.copy()
    ef  = PROJECT.get("env", {}).get("env_file", ".env")
    ep  = Path(PROJECT_ROOT) / ef
    if ep.exists():
        with open(ep) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    env[k.strip()] = v.strip().strip('"').strip("'")
    env.update(overrides)
    return env

def validate_project() -> Dict:
    if not PROJECT:
        return {"valid": False, "errors": ["No project loaded"], "warns": [], "checks": {}}
    errors, warns, checks = [], [], {}
    root_ok = Path(PROJECT_ROOT).exists()
    checks["project_root"] = {"ok": root_ok, "path": PROJECT_ROOT}
    if not root_ok: errors.append(f"Project root not found: {PROJECT_ROOT}")
    exec_checks = {}
    for eid, ex in PROJECT.get("executors", {}).items():
        sp = ex.get("script_path", "")
        safe_path, path_err = _safe_path(sp, ex.get("allow_outside_root", False)) if sp else (None, None)
        exists = Path(safe_path).exists() if safe_path else False
        exec_checks[eid] = {"ok": exists, "path": safe_path, "path_error": path_err}
        if path_err: errors.append(f"Executor '{eid}': {path_err}")
        elif not exists and ex.get("run_mode") != "none":
            warns.append(f"Executor '{eid}' script not found: {safe_path}")
    checks["executors"] = exec_checks
    agent_checks = {}
    for ag in PROJECT.get("agents", []):
        fp = _resolve(ag.get("instruction_file", ""))
        exists = Path(fp).exists() if fp else False
        agent_checks[ag["id"]] = {"ok": exists, "path": fp}
        if not exists: pass  # expected: AGENT.md files sync from UI, not required on disk
    checks["agents"] = agent_checks
    env_checks = {}
    loaded_env = build_env()
    for var in PROJECT.get("env", {}).get("required", []):
        present = var in loaded_env and bool(loaded_env[var])
        env_checks[var] = {"ok": present, "required": True}
        # Only warn for the primary API key - others are expected to be added as needed
        if not present and var == "ANTHROPIC_API_KEY":
            warns.append(f"ANTHROPIC_API_KEY missing - add to .env to run agents")
    checks["env"] = env_checks
    # Model provider check
    if _model_mgr:
        from bridge import model_manager as mm
        prov_status = mm.get_provider_status(PROJECT_ROOT)
        checks["providers"] = prov_status
        # Provider key status shown in UI - no need to warn in startup logs
    return {"valid": len(errors) == 0, "errors": errors, "warns": warns, "checks": checks}

# -- Executor runner -----------------------------------------------------------
def run_executor_sync(eid: str, context: Dict, env_overrides: Dict = {}, dry_run: bool = False) -> Dict:
    ex = get_executor(eid)
    if not ex:
        return {"ok": False, "executor_id": eid, "error": f"Executor '{eid}' not in project.json",
                "exit_code": -1, "stdout": "", "stderr": "", "duration_ms": 0}
    run_mode  = ex.get("run_mode", "subprocess")
    allow_out = ex.get("allow_outside_root", False)
    sp        = ex.get("script_path", "")
    safe_path, path_err = _safe_path(sp, allow_out) if sp else (None, None)
    working_dir = _resolve(ex.get("working_dir", ".")) or PROJECT_ROOT
    wd_safe, _  = _safe_path(working_dir, allow_out)
    working_dir = wd_safe or PROJECT_ROOT
    entry   = ex.get("entry", "main")
    timeout = ex.get("timeout_seconds", 120)
    label   = ex.get("label", eid)
    result = {"ok": False, "executor_id": eid, "label": label, "run_mode": run_mode,
              "script_path": safe_path, "started_at": datetime.now().isoformat(),
              "stdout": "", "stderr": "", "exit_code": -1, "output": {}, "duration_ms": 0}
    if path_err:
        result.update({"ok": False, "error": f"Path security violation: {path_err}"}); RUN_LOG[eid] = result; return result
    if dry_run:
        result.update({"ok": True, "exit_code": 0, "output": {"status": "dry_run"}, "stdout": "[dry_run]"}); RUN_LOG[eid] = result; return result
    t0 = time.time()
    try:
        env = build_env(env_overrides)
        if run_mode == "none":
            result.update({"ok": True, "exit_code": 0, "skipped": True, "output": {"status": "not_implemented"}, "duration_ms": round((time.time()-t0)*1000)})
        elif run_mode in ("subprocess", "python_script", "python_module"):
            if safe_path and not Path(safe_path).exists(): raise FileNotFoundError(f"Script not found: {safe_path}")
            if run_mode == "python_module" and entry and (not safe_path or not Path(safe_path).exists()):
                cmd = [sys.executable, "-m", entry, "--context", json.dumps(context)]
            else:
                cmd = [sys.executable, safe_path, "--entry", entry, "--context", json.dumps(context)]
            proc = subprocess.Popen(cmd, cwd=working_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                dur = round((time.time()-t0)*1000)
                out_parsed = {}
                if proc.returncode == 0 and stdout.strip():
                    try: out_parsed = json.loads(stdout.strip())
                    except: out_parsed = {"raw": stdout.strip()}
                result.update({"ok": proc.returncode == 0, "exit_code": proc.returncode,
                               "stdout": stdout, "stderr": stderr, "output": out_parsed, "duration_ms": dur})
                if proc.returncode != 0: result["error"] = f"Exit {proc.returncode}: {stderr[:300]}"
            except subprocess.TimeoutExpired:
                proc.kill(); proc.communicate(timeout=5)
                result.update({"ok": False, "error": f"Timed out after {timeout}s", "exit_code": -9, "duration_ms": timeout*1000})
        elif run_mode == "shell_command":
            cmd_str = ex.get("command", "")
            if not cmd_str: raise ValueError("shell_command missing 'command'")
            proc = subprocess.Popen(cmd_str, shell=True, cwd=working_dir, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
                result.update({"ok": proc.returncode == 0, "exit_code": proc.returncode,
                               "stdout": stdout, "stderr": stderr, "output": {"raw": stdout.strip()}, "duration_ms": round((time.time()-t0)*1000)})
            except subprocess.TimeoutExpired:
                proc.kill(); proc.communicate(timeout=5)
                result.update({"ok": False, "error": f"Timed out after {timeout}s", "exit_code": -9, "duration_ms": timeout*1000})
        else:
            result.update({"ok": False, "error": f"Unsupported run_mode: '{run_mode}'"})
    except FileNotFoundError as e:
        result.update({"ok": False, "error": str(e), "duration_ms": round((time.time()-t0)*1000)})
    except Exception as e:
        result.update({"ok": False, "error": str(e), "traceback": traceback.format_exc(), "duration_ms": round((time.time()-t0)*1000)})
    result["completed_at"] = datetime.now().isoformat()
    RUN_LOG[eid] = result
    _log("info" if result["ok"] else "error", f"Executor '{eid}': ok={result['ok']} exit={result['exit_code']} {result['duration_ms']}ms")
    return result

# -- Subsystem init ------------------------------------------------------------
def _init_subsystems():
    global _model_mgr, _task_worker, _scheduler, _orchestrator, _runtime_mgr, _exec_engine
    from bridge import model_manager as mm
    env = build_env()
    mm.init(PROJECT, env)
    _model_mgr = mm

    def _agent_call(agent_def, system, messages):
        return mm.run_agent_model(agent_def, system, messages, PROJECT_ROOT)

    from bridge.task_worker import TaskWorker
    def _runtime_call(runtime_id, context):
        if _runtime_mgr:
            return _runtime_mgr.run(runtime_id, context)
        return {"ok": False, "error": "Runtime manager not ready"}

    _task_worker = TaskWorker(PROJECT_DATA_DIR, run_executor_sync, _agent_call, _runtime_call)
    _task_worker.init(PROJECT, lambda msg: _log("info", msg))
    # Recovery: count what was restored from disk
    all_tasks    = _task_worker.get_tasks()
    interrupted  = [t for t in all_tasks if t.get("error") == "Recovered after bridge restart"]
    queued_tasks = [t for t in all_tasks if t["status"] == "queued"]
    _log("info", f"Task recovery: {len(interrupted)} interrupted re-queued, {len(queued_tasks)} queued, {len(all_tasks)} total")
    _task_worker.start()

    def _schedule_fire(schedule_def):
        stype = schedule_def.get("type")
        target = schedule_def.get("target_id")
        inp = schedule_def.get("input", {})
        if stype == "pipeline":
            return {"ok": True, "note": "pipeline scheduled run - use /pipeline endpoint"}
        elif stype == "executor":
            return run_executor_sync(target, inp)
        elif stype == "agent":
            agent_def = next((a for a in PROJECT.get("agents", []) if a["id"] == target), None)
            if agent_def:
                return _agent_call(agent_def, agent_def.get("_instructions", ""), [{"role": "user", "content": json.dumps(inp)}])
        elif stype == "task":
            task = _task_worker.get_task(target)
            if task: _task_worker.run_now(target)
        return {"ok": True}

    from bridge.scheduler import SchedulerManager
    _scheduler = SchedulerManager(PROJECT_DATA_DIR, _schedule_fire)
    _scheduler.init(lambda msg: _log("info", msg))
    _scheduler.start()
    _log("info", f"Schedule recovery: {len(_scheduler.list_schedules())} schedule(s) reloaded from disk")

    from bridge.orchestrator import CEOOrchestrator
    _orchestrator = CEOOrchestrator(_agent_call, _task_worker)
    _orchestrator.init(PROJECT, lambda msg: _log("info", msg))

    from bridge.runtime_manager import RuntimeManager
    _runtime_mgr = RuntimeManager()
    _runtime_mgr.init(PROJECT, lambda msg: _log("info", msg))

    from bridge.execution_engine import NativeExecutionEngine
    _exec_engine = NativeExecutionEngine()
    _exec_engine.init(PROJECT, lambda msg: _log("info", msg))

    # Re-init task worker with runtime support
    _task_worker._runtime_fn = lambda rid, ctx, aid: _runtime_mgr.execute(rid, ctx, aid)

    _load_pipeline_runs()
    _auto_migrate_paperteam()
    _log("info", "All subsystems initialised")

# -- Static + UI ---------------------------------------------------------------
@app.get("/", include_in_schema=False)
def serve_ui():
    p = ROCKO_ROOT / "index.html"
    if p.exists(): return FileResponse(str(p), media_type="text/html")
    raise HTTPException(404, "index.html not found")

@app.get("/favicon.ico", include_in_schema=False)
def serve_favicon():
    p = ROCKO_ROOT / "favicon.ico"
    if p.exists(): return FileResponse(str(p), media_type="image/x-icon")
    raise HTTPException(404, "favicon not found")

@app.get("/manifest.json", include_in_schema=False)
def serve_manifest():
    p = ROCKO_ROOT / "manifest.json"
    if p.exists():
        return FileResponse(str(p), media_type="application/manifest+json")
    # Fallback: generate manifest inline so PWA install always works
    from fastapi.responses import JSONResponse
    return JSONResponse({
        "name": "RockoAgents",
        "short_name": "RockoAgents",
        "description": "Self-hosted local agent orchestration",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#0a0a0f",
        "theme_color": "#4782ff",
        "icons": [
            {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
            {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}
        ]
    }, media_type="application/manifest+json")

@app.get("/icon-192.png", include_in_schema=False)
def serve_icon192():
    p = ROCKO_ROOT / "icon-192.png"
    if p.exists(): return FileResponse(str(p), media_type="image/png")
    raise HTTPException(404)

@app.get("/icon-512.png", include_in_schema=False)
def serve_icon512():
    p = ROCKO_ROOT / "icon-512.png"
    if p.exists(): return FileResponse(str(p), media_type="image/png")
    raise HTTPException(404)

try:
    app.mount("/assets", StaticFiles(directory=str(ROCKO_ROOT)), name="assets")
except Exception: pass

# -- Core routes ---------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "bridge_version": "5.0.0", "project_loaded": bool(PROJECT),
            "project_name": PROJECT.get("project", {}).get("name", "none"),
            "project_root": PROJECT_ROOT, "bridge_started": BRIDGE_START,
            "executors": list(PROJECT.get("executors", {}).keys()),
            "agents": [a["id"] for a in PROJECT.get("agents", [])],
            "scheduler_available": _scheduler.is_available() if _scheduler else False,
            "worker_running": _task_worker._running if _task_worker else False,
            "timestamp": datetime.now().isoformat(), "build_id": BRIDGE_BUILD_ID}

@app.get("/project")
def get_project():
    if not PROJECT: raise HTTPException(404, "No project loaded")
    return {"project": PROJECT.get("project", {}), "model": PROJECT.get("model", {}),
            "executors": list(PROJECT.get("executors", {}).keys()),
            "agents": [a["id"] for a in PROJECT.get("agents", [])],
            "pipeline": [s["step_id"] for s in PROJECT.get("pipeline", {}).get("execution_order", [])]}

@app.get("/executors")
def list_executors():
    out = {}
    for eid, ex in PROJECT.get("executors", {}).items():
        sp = ex.get("script_path", "")
        safe_path, path_err = _safe_path(sp, ex.get("allow_outside_root", False)) if sp else (None, None)
        out[eid] = {"label": ex.get("label", eid), "run_mode": ex.get("run_mode"),
                    "script_path": safe_path, "script_exists": Path(safe_path).exists() if safe_path else False,
                    "path_error": path_err, "timeout_seconds": ex.get("timeout_seconds", 120)}
    return out

@app.post("/run/{executor_id}")
def run_executor(executor_id: str, req: RunRequest):
    if not PROJECT: raise HTTPException(503, "No project loaded")
    return run_executor_sync(executor_id, {**req.context, **req.input}, req.env_overrides, req.dry_run)

@app.get("/run/{executor_id}/status")
def executor_status(executor_id: str):
    if executor_id not in RUN_LOG: return {"executor_id": executor_id, "status": "never_run"}
    r = RUN_LOG[executor_id]
    return {"executor_id": executor_id, "status": "success" if r.get("ok") else "failed",
            "completed_at": r.get("completed_at"), "duration_ms": r.get("duration_ms"), "exit_code": r.get("exit_code")}

@app.post("/pipeline")
def run_pipeline_route(req: PipelineRequest):
    global PIPELINE_STATE
    if not PROJECT: raise HTTPException(503, "No project loaded")
    steps = [s for s in PROJECT.get("pipeline", {}).get("execution_order", []) if s["type"] == "executor"]
    run_id = f"run_{uuid.uuid4().hex[:8]}"
    PIPELINE_STATE = {"run_id": run_id, "status": "running", "started_at": datetime.now().isoformat(),
                      "steps_total": len(steps), "steps_completed": 0, "steps_failed": 0, "results": {}}
    ctx = dict(req.input_data)
    for step in steps:
        eid = step.get("executor_id")
        rid = step.get("runtime_id")
        if eid:
            result = run_executor_sync(eid, ctx, {}, req.dry_run)
        elif rid and _runtime_mgr:
            result = _runtime_mgr.run(rid, ctx, dry_run=req.dry_run)
        else:
            continue
        PIPELINE_STATE["results"][step["step_id"]] = result
        if result.get("output"): ctx.update(result["output"])
        if result.get("ok") or result.get("skipped"): PIPELINE_STATE["steps_completed"] += 1
        else:
            PIPELINE_STATE["steps_failed"] += 1
            if req.stop_on_error:
                PIPELINE_STATE.update({"status": "halted", "halted_at": step["step_id"], "completed_at": datetime.now().isoformat()})
                _save_pipeline_run(PIPELINE_STATE)
                return PIPELINE_STATE
    PIPELINE_STATE.update({"status": "complete", "completed_at": datetime.now().isoformat()})
    _save_pipeline_run(PIPELINE_STATE)
    return PIPELINE_STATE

@app.get("/pipeline/status")
def pipeline_status():
    return PIPELINE_STATE if PIPELINE_STATE else {"status": "never_run"}

@app.get("/pipeline/runs")
def pipeline_runs():
    return {"runs": PIPELINE_RUNS[-50:], "total": len(PIPELINE_RUNS)}

@app.get("/pipeline/runs/{run_id}")
def pipeline_run(run_id: str):
    run = next((r for r in PIPELINE_RUNS if r.get("run_id") == run_id), None)
    if not run: raise HTTPException(404, f"Run not found: {run_id}")
    return run

def _save_pipeline_run(state: Dict):
    PIPELINE_RUNS.insert(0, {**state})
    if len(PIPELINE_RUNS) > 100: PIPELINE_RUNS.pop()
    try:
        with open(PROJECT_DATA_DIR / "pipeline_runs.json", "w") as f:
            json.dump(PIPELINE_RUNS[:100], f, indent=2)
    except Exception: pass

@app.get("/validate")
def validate_route():
    if not PROJECT: raise HTTPException(503, "No project loaded")
    return validate_project()

@app.get("/logs")
def get_logs(limit: int = 100):
    return {"logs": LOG_BUFFER[-limit:], "total": len(LOG_BUFFER)}

@app.post("/data/save")
def data_save(req: DataSaveRequest):
    try:
        fp = PROJECT_DATA_DIR / (req.key.replace("/", "_") + ".json")
        with open(fp, "w") as f: json.dump(req.data, f, indent=2)
        return {"ok": True, "path": str(fp)}
    except Exception as e: raise HTTPException(500, str(e))

@app.get("/data/load")
def data_load(key: str):
    try:
        fp = PROJECT_DATA_DIR / (key.replace("/", "_") + ".json")
        if not fp.exists(): raise HTTPException(404, f"Key not found: {key}")
        with open(fp) as f: data = json.load(f)
        return {"ok": True, "key": key, "data": data}
    except HTTPException: raise
    except Exception as e: raise HTTPException(500, str(e))

@app.post("/file/read")
def file_read(req: FileReadRequest):
    rel = req.path.replace("\\", "/")
    safe_path, path_err = _safe_path(rel, False)
    if path_err: raise HTTPException(403, f"Path security violation: {path_err}")
    p = Path(safe_path) if safe_path else None
    if not p or not p.exists(): raise HTTPException(404, f"File not found: {rel}")
    try:
        with open(p) as f: content = f.read()
        return {"ok": True, "path": str(p), "content": content, "size": len(content)}
    except Exception as e: raise HTTPException(500, str(e))

@app.post("/reset")
def reset():
    global RUN_LOG, PIPELINE_STATE
    RUN_LOG = {}; PIPELINE_STATE = {}
    _log("info", "State reset")
    return {"status": "reset", "timestamp": datetime.now().isoformat()}

# -- Task routes ---------------------------------------------------------------
@app.get("/tasks")
def list_tasks(status: Optional[str] = None):
    if not _task_worker: raise HTTPException(503, "Task worker not initialised")
    return {"tasks": _task_worker.get_tasks(status)}

@app.post("/tasks")
def create_task(req: TaskCreateRequest):
    if not _task_worker: raise HTTPException(503, "Task worker not initialised")
    task = _task_worker.create_task(req.title, req.assigned_to, req.type,
                                    req.instructions, req.input, req.parent_task_id,
                                    req.max_retries, req.priority)
    return task

@app.get("/tasks/worker/status")
def worker_status():
    if not _task_worker: raise HTTPException(503, "Task worker not initialised")
    return _task_worker.status()

@app.post("/tasks/worker/start")
def worker_start():
    if not _task_worker: raise HTTPException(503, "Task worker not initialised")
    _task_worker.start(); return {"status": "started"}

@app.post("/tasks/worker/stop")
def worker_stop():
    if not _task_worker: raise HTTPException(503, "Task worker not initialised")
    _task_worker.stop(); return {"status": "stopped"}

@app.post("/tasks/worker/pause")
def worker_pause():
    if not _task_worker: raise HTTPException(503, "Task worker not initialised")
    _task_worker.pause(); return {"status": "paused"}

@app.post("/tasks/worker/resume")
def worker_resume():
    if not _task_worker: raise HTTPException(503, "Task worker not initialised")
    _task_worker.resume(); return {"status": "resumed"}

@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    if not _task_worker: raise HTTPException(503, "Task worker not initialised")
    task = _task_worker.get_task(task_id)
    if not task: raise HTTPException(404, f"Task not found: {task_id}")
    return task

@app.post("/tasks/{task_id}/run")
def run_task_now(task_id: str):
    if not _task_worker: raise HTTPException(503, "Task worker not initialised")
    if not _task_worker.run_now(task_id): raise HTTPException(404, "Task not found")
    return {"status": "running", "task_id": task_id}

@app.post("/tasks/{task_id}/cancel")
def cancel_task(task_id: str):
    if not _task_worker: raise HTTPException(503, "Task worker not initialised")
    if not _task_worker.cancel_task(task_id): raise HTTPException(404, "Task not found or already complete")
    return {"status": "cancelled", "task_id": task_id}

@app.post("/tasks/{task_id}/retry")
def retry_task(task_id: str):
    if not _task_worker: raise HTTPException(503, "Task worker not initialised")
    if not _task_worker.retry_task(task_id): raise HTTPException(400, "Task cannot be retried")
    return {"status": "queued", "task_id": task_id}

# -- Scheduler routes ----------------------------------------------------------
@app.get("/schedules")
def list_schedules():
    if not _scheduler: raise HTTPException(503, "Scheduler not initialised")
    return {"schedules": _scheduler.list_schedules(), "available": _scheduler.is_available()}

@app.post("/schedules")
def create_schedule(req: ScheduleCreateRequest):
    if not _scheduler: raise HTTPException(503, "Scheduler not initialised")
    try:
        return _scheduler.add_schedule(req.model_dump())
    except ValueError as e: raise HTTPException(400, str(e))

@app.get("/schedules/{schedule_id}")
def get_schedule(schedule_id: str):
    if not _scheduler: raise HTTPException(503, "Scheduler not initialised")
    s = _scheduler.get_schedule(schedule_id)
    if not s: raise HTTPException(404, f"Schedule not found: {schedule_id}")
    return s

@app.patch("/schedules/{schedule_id}")
def update_schedule(schedule_id: str, updates: Dict[str, Any]):
    if not _scheduler: raise HTTPException(503, "Scheduler not initialised")
    s = _scheduler.update_schedule(schedule_id, updates)
    if not s: raise HTTPException(404, "Schedule not found")
    return s

@app.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str):
    if not _scheduler: raise HTTPException(503, "Scheduler not initialised")
    if not _scheduler.remove_schedule(schedule_id): raise HTTPException(404, "Schedule not found")
    return {"status": "deleted", "schedule_id": schedule_id}

@app.post("/schedules/{schedule_id}/pause")
def pause_schedule(schedule_id: str):
    if not _scheduler: raise HTTPException(503, "Scheduler not initialised")
    if not _scheduler.pause_schedule(schedule_id): raise HTTPException(404, "Schedule not found")
    return {"status": "paused", "schedule_id": schedule_id}

@app.post("/schedules/{schedule_id}/resume")
def resume_schedule(schedule_id: str):
    if not _scheduler: raise HTTPException(503, "Scheduler not initialised")
    if not _scheduler.resume_schedule(schedule_id): raise HTTPException(404, "Schedule not found")
    return {"status": "resumed", "schedule_id": schedule_id}

@app.post("/schedules/{schedule_id}/run-now")
def run_schedule_now(schedule_id: str):
    if not _scheduler: raise HTTPException(503, "Scheduler not initialised")
    if not _scheduler.run_now(schedule_id): raise HTTPException(404, "Schedule not found")
    return {"status": "fired", "schedule_id": schedule_id}


# -- CEO-managed internal routing ---------------------------------------------
def _project_file() -> Optional[Path]:
    if not PROJECT_ROOT:
        return None
    return Path(PROJECT_ROOT) / "project.json"

def _save_project(reason: str = "") -> bool:
    """Persist the in-memory project manifest after safe bridge-managed edits."""
    fp = _project_file()
    if not fp:
        return False
    try:
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(PROJECT, f, indent=2)
        if reason:
            _log("info", f"Project saved: {reason}")
        return True
    except Exception as e:
        _log("warn", f"Could not save project.json: {e}")
        return False

def _find_ceo_agent() -> Optional[Dict[str, Any]]:
    agents = PROJECT.get("agents", []) if PROJECT else []
    for a in agents:
        role = str(a.get("role", "")).lower()
        if role == "ceo" or a.get("id") == "ceo_agent":
            return a
    for a in agents:
        hay = f"{a.get('id','')} {a.get('name','')} {a.get('display_name','')}".lower()
        if "ceo" in hay:
            return a
    return agents[0] if agents else None

def _agent_registry() -> List[Dict[str, Any]]:
    """
    Real agent registry exposed to the CEO routing planner.
    This is the source of truth; the CEO must choose from these IDs only.
    """
    registry: List[Dict[str, Any]] = []
    for a in PROJECT.get("agents", []) if PROJECT else []:
        skills = []
        for s in a.get("skills", []) or []:
            if isinstance(s, str):
                skills.append({"id": s, "name": s})
            elif isinstance(s, dict):
                skills.append({
                    "id": s.get("id", s.get("skill_id", s.get("name", ""))),
                    "name": s.get("name", s.get("skill_name", s.get("id", ""))),
                })
        registry.append({
            "id": a.get("id", ""),
            "name": a.get("name") or a.get("display_name") or a.get("id", ""),
            "role": a.get("role", "analyst"),
            "status": a.get("status", "active"),
            "enabled": bool(a.get("enabled", True)),
            "description": a.get("description", ""),
            "skills": skills,
            "outputs_to": list(a.get("outputs_to", []) or []),
        })
    return registry

def _current_routing_graph() -> Dict[str, Any]:
    edges = []
    for a in PROJECT.get("agents", []) if PROJECT else []:
        src = a.get("id", "")
        for dst in a.get("outputs_to", []) or []:
            edges.append({"from": src, "to": dst})
    routing = PROJECT.get("routing", {}) if PROJECT else {}
    return {
        "mode": routing.get("mode", "ceo_managed"),
        "dirty": routing.get("dirty", False),
        "last_updated_at": routing.get("last_updated_at"),
        "last_updated_by": routing.get("last_updated_by"),
        "summary": routing.get("summary", ""),
        "edges": edges,
    }

def _normalise_routing_edges(payload: Dict[str, Any]) -> List[Dict[str, str]]:
    """Accept CEO routing in common shapes and normalize to [{from,to,reason}]."""
    if not isinstance(payload, dict):
        return []
    routing = payload.get("routing", payload)
    raw_edges = routing.get("edges") or routing.get("links") or []
    edges: List[Dict[str, str]] = []
    if isinstance(raw_edges, dict):
        for src, targets in raw_edges.items():
            if isinstance(targets, str):
                targets = [targets]
            for dst in targets or []:
                edges.append({"from": str(src), "to": str(dst), "reason": ""})
    elif isinstance(raw_edges, list):
        for e in raw_edges:
            if not isinstance(e, dict):
                continue
            src = e.get("from") or e.get("source") or e.get("source_agent_id") or e.get("agent_id")
            dst = e.get("to") or e.get("target") or e.get("target_agent_id") or e.get("outputs_to")
            reason = e.get("reason", "")
            if isinstance(dst, list):
                for d in dst:
                    edges.append({"from": str(src or ""), "to": str(d), "reason": str(reason)})
            else:
                edges.append({"from": str(src or ""), "to": str(dst or ""), "reason": str(reason)})
    outputs_to = routing.get("outputs_to")
    if isinstance(outputs_to, dict):
        for src, targets in outputs_to.items():
            if isinstance(targets, str):
                targets = [targets]
            for dst in targets or []:
                edges.append({"from": str(src), "to": str(dst), "reason": ""})
    clean = []
    seen = set()
    for e in edges:
        src = e.get("from", "").strip()
        dst = e.get("to", "").strip()
        if not src or not dst:
            continue
        key = (src, dst)
        if key in seen:
            continue
        seen.add(key)
        clean.append({"from": src, "to": dst, "reason": e.get("reason", "")})
    return clean

def _validate_routing_edges(edges: List[Dict[str, str]]) -> Dict[str, Any]:
    registry = _agent_registry()
    agent_ids = {a["id"] for a in registry if a.get("id")}
    active_ids = {a["id"] for a in registry if a.get("id") and a.get("enabled") and a.get("status") != "fired"}
    errors: List[str] = []
    warns: List[str] = []
    adjacency: Dict[str, List[str]] = {aid: [] for aid in agent_ids}
    incoming: Dict[str, int] = {aid: 0 for aid in agent_ids}

    for e in edges:
        src = e.get("from", "")
        dst = e.get("to", "")
        if src not in agent_ids:
            errors.append(f"Unknown source agent id: {src}")
            continue
        if dst not in agent_ids:
            errors.append(f"Unknown target agent id: {dst}")
            continue
        if src == dst:
            errors.append(f"Self-loop is not allowed: {src} -> {dst}")
            continue
        adjacency.setdefault(src, []).append(dst)
        incoming[dst] = incoming.get(dst, 0) + 1

    visiting, visited, cycle_hits = set(), set(), []
    def dfs(node: str, stack: List[str]):
        if node in visiting:
            cycle_hits.append(" -> ".join(stack + [node]))
            return
        if node in visited:
            return
        visiting.add(node)
        for nxt in adjacency.get(node, []):
            dfs(nxt, stack + [node])
        visiting.remove(node)
        visited.add(node)

    for aid in agent_ids:
        dfs(aid, [])
    for c in cycle_hits:
        errors.append(f"Cycle detected: {c}")

    if active_ids:
        routed_ids = {e.get("from") for e in edges} | {e.get("to") for e in edges}
        isolated = sorted(active_ids - routed_ids)
        if isolated:
            warns.append("Active agents not connected in routing graph: " + ", ".join(isolated))
        start_nodes = sorted([aid for aid in active_ids if incoming.get(aid, 0) == 0 and adjacency.get(aid)])
        if not start_nodes and edges:
            warns.append("No clear start node detected")
        ceo = _find_ceo_agent()
        if ceo and ceo.get("id") in active_ids:
            ceo_id = ceo.get("id")
            if edges and incoming.get(ceo_id, 0) == 0 and adjacency.get(ceo_id):
                warns.append("CEO has outbound routing but no upstream inputs; confirm this is intentional")

    return {
        "ok": len(errors) == 0,
        "errors": errors,
        "warns": warns,
        "agent_count": len(agent_ids),
        "edge_count": len(edges),
        "edges": edges,
    }

def _apply_routing_edges(edges: List[Dict[str, str]], summary: str = "", updated_by: str = "ceo") -> Dict[str, Any]:
    validation = _validate_routing_edges(edges)
    if not validation.get("ok"):
        return {"ok": False, "validation": validation}
    outputs: Dict[str, List[str]] = {}
    for e in edges:
        outputs.setdefault(e["from"], []).append(e["to"])
    for a in PROJECT.get("agents", []) if PROJECT else []:
        aid = a.get("id")
        a["outputs_to"] = outputs.get(aid, [])
    PROJECT["routing"] = {
        "mode": "ceo_managed",
        "dirty": False,
        "last_updated_at": datetime.now().isoformat(),
        "last_updated_by": updated_by,
        "summary": summary,
        "validation": validation,
    }
    _save_project("ceo routing graph updated")
    return {"ok": True, "routing": _current_routing_graph(), "validation": validation}

def _mark_routing_dirty(reason: str):
    if not PROJECT:
        return
    routing = PROJECT.setdefault("routing", {})
    routing.setdefault("mode", "ceo_managed")
    routing["dirty"] = True
    routing["dirty_reason"] = reason
    routing["dirty_at"] = datetime.now().isoformat()
    _save_project(f"routing marked dirty: {reason}")

def _extract_json_object(text: str) -> Dict[str, Any]:
    import re as _re
    if not text:
        return {}
    cleaned = text.strip()
    if "```json" in cleaned:
        cleaned = cleaned.split("```json", 1)[1].split("```", 1)[0].strip()
    elif "```" in cleaned:
        cleaned = cleaned.split("```", 1)[1].split("```", 1)[0].strip()
    try:
        return json.loads(cleaned)
    except Exception:
        pass
    m = _re.search(r"\{.*\}", text, _re.DOTALL)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            return {}
    return {}

def _ceo_generate_routing(reason: str = "") -> Dict[str, Any]:
    if not PROJECT:
        raise HTTPException(503, "No project loaded")
    if not _model_mgr:
        raise HTTPException(503, "Model manager not initialised")
    ceo = _find_ceo_agent()
    if not ceo:
        raise HTTPException(400, "No CEO agent available to generate routing")

    registry = _agent_registry()
    current = _current_routing_graph()
    system = (
        "You are the CEO routing architect for this RockoAgents company. "
        "Your job is to build the internal agent-to-agent DAG from the real agent registry. "
        "Use only agent IDs that appear in agent_registry. Do not invent IDs. "
        "Do not create UI instructions. Do not discuss Telegram, WhatsApp, dashboards, or human messaging. "
        "Return only valid JSON with this shape: "
        "{\"routing\":{\"edges\":[{\"from\":\"agent_id\",\"to\":\"agent_id\",\"reason\":\"why\"}]},\"summary\":\"short explanation\"}. "
        "The graph must be acyclic and should route specialist outputs toward review/decision agents, usually ending at the CEO when appropriate."
    )
    user_payload = {
        "company": PROJECT.get("project", {}),
        "reason": reason or "Build or refresh the company internal routing DAG.",
        "agent_registry": registry,
        "current_routing": current,
    }
    try:
        resp = _model_mgr.run_agent_model(
            ceo,
            system,
            [{"role": "user", "content": json.dumps(user_payload, indent=2)}],
            PROJECT_ROOT,
        )
    except Exception as e:
        raise HTTPException(500, f"CEO routing model call failed: {e}")

    text = ""
    if isinstance(resp, dict):
        text = (resp.get("content") or resp.get("text") or resp.get("response") or
                resp.get("stdout") or resp.get("message") or "")
        if not text and isinstance(resp.get("output"), dict):
            text = json.dumps(resp.get("output"))
    else:
        text = str(resp)
    plan = _extract_json_object(text)
    if not plan:
        raise HTTPException(500, "CEO did not return a valid routing JSON object")
    edges = _normalise_routing_edges(plan)
    validation = _validate_routing_edges(edges)
    return {
        "ok": validation.get("ok", False),
        "plan": plan,
        "edges": edges,
        "validation": validation,
        "raw_response": text,
    }

def _fallback_routing_edges() -> List[Dict[str, str]]:
    """Optional safety fallback, only used when explicitly requested."""
    registry = [a for a in _agent_registry() if a.get("enabled") and a.get("status") != "fired"]
    ceo = _find_ceo_agent()
    ceo_id = ceo.get("id") if ceo else ""
    non_ceo = [a for a in registry if a.get("id") != ceo_id]
    edges = []
    if ceo_id:
        for a in non_ceo:
            edges.append({"from": a["id"], "to": ceo_id, "reason": "fallback route to CEO"})
    return edges

@app.get("/routing/graph")
def routing_graph():
    if not PROJECT:
        raise HTTPException(503, "No project loaded")
    graph = _current_routing_graph()
    validation = _validate_routing_edges(graph.get("edges", []))
    return {"registry": _agent_registry(), "routing": graph, "validation": validation}

@app.post("/routing/validate")
def routing_validate(req: RoutingValidateRequest):
    edges = _normalise_routing_edges(req.routing or {})
    return _validate_routing_edges(edges)

@app.post("/routing/ceo/rebuild")
def routing_ceo_rebuild(req: RoutingPlanRequest):
    if not PROJECT:
        raise HTTPException(503, "No project loaded")
    try:
        generated = _ceo_generate_routing(req.reason)
    except HTTPException:
        if not req.allow_fallback:
            raise
        edges = _fallback_routing_edges()
        generated = {
            "ok": True,
            "plan": {"summary": "Fallback routing: all active specialists report to CEO."},
            "edges": edges,
            "validation": _validate_routing_edges(edges),
            "raw_response": "",
        }
    if not generated.get("validation", {}).get("ok"):
        return generated
    if req.apply:
        applied = _apply_routing_edges(
            generated.get("edges", []),
            summary=(generated.get("plan", {}) or {}).get("summary", "CEO-managed routing updated"),
            updated_by="ceo",
        )
        generated["applied"] = applied
    return generated

# -- Orchestration routes ------------------------------------------------------
def _load_skill_registry_for_ceo(limit: int = 200) -> List[Dict[str, Any]]:
    """Load project-root skills.json first so every CEO sees the local skill library."""
    try:
        resp = list_skills()
        skills = resp.get("skills", []) if isinstance(resp, dict) else []
    except Exception as e:
        _log("warn", f"CEO skill registry load failed: {e}")
        skills = []

    registry: List[Dict[str, Any]] = []
    seen = set()
    for s in skills:
        if not isinstance(s, dict):
            continue
        sid = str(s.get("id") or s.get("skill_id") or s.get("slug") or s.get("name") or "").strip()
        if not sid or sid in seen:
            continue
        seen.add(sid)
        registry.append({
            "id": sid,
            "name": s.get("name") or sid.split("/")[-1],
            "description": s.get("description", ""),
            "source": s.get("source", "skills.json"),
            "installs": s.get("installs", 0),
            "cached": s.get("cached", False),
        })
        if len(registry) >= limit:
            break
    return registry

def _agent_skill_assignments_for_ceo() -> Dict[str, List[Dict[str, Any]]]:
    assignments: Dict[str, List[Dict[str, Any]]] = {}
    for a in PROJECT.get("agents", []) if PROJECT else []:
        aid = a.get("id", "")
        assignments[aid] = []
        for s in a.get("skills", []) or []:
            if isinstance(s, str):
                assignments[aid].append({"id": s, "name": s, "assigned_by": "unknown"})
            elif isinstance(s, dict):
                assignments[aid].append({
                    "id": s.get("id") or s.get("skill_id") or s.get("name", ""),
                    "name": s.get("name") or s.get("skill_name") or s.get("id", ""),
                    "assigned_by": s.get("assigned_by", s.get("source", "unknown")),
                })
    return assignments

def _build_ceo_orchestration_context(base_context: Dict[str, Any]) -> Dict[str, Any]:
    context = dict(base_context or {})
    context["agent_registry"] = _agent_registry()
    context["available_skills"] = _load_skill_registry_for_ceo()
    context["agent_skill_assignments"] = _agent_skill_assignments_for_ceo()
    context["skill_delegation_authority"] = {
        "enabled": True,
        "instruction": (
            "You may delegate skills to agents by returning decision='assign_skill' "
            "with skill_id from available_skills and target_agent_id from agent_registry. "
            "Do not invent skill IDs or agent IDs."
        ),
    }
    return context

def _resolve_skill_for_ceo_assignment(skill_id: str, available_skills: List[Dict[str, Any]]) -> Dict[str, Any]:
    for s in available_skills:
        if str(s.get("id", "")).strip() == skill_id:
            return dict(s)
    return {"id": skill_id, "name": skill_id.split("/")[-1], "description": "", "source": "ceo"}

def _apply_ceo_skill_assignment(skill_id: str, agent_id: str, available_skills: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not PROJECT:
        raise ValueError("No project loaded")
    agent = next((a for a in PROJECT.get("agents", []) if a.get("id") == agent_id), None)
    if not agent:
        raise ValueError(f"Unknown target agent id: {agent_id}")

    parsed: Dict[str, Any]
    try:
        _, parsed = _skillssh_fetch(skill_id)
    except Exception as e:
        local = _resolve_skill_for_ceo_assignment(skill_id, available_skills)
        parsed = {
            "id": local.get("id", skill_id),
            "name": local.get("name") or skill_id.split("/")[-1],
            "slug": local.get("slug", skill_id.split("/")[-1]),
            "source": local.get("source", "skills.json"),
            "description": local.get("description", ""),
            "cached_path": local.get("cached_path", ""),
            "instructions": local.get("instructions", ""),
        }
        _log("warn", f"Skill fetch skipped/failed for {skill_id}; using local skills.json metadata: {e}")

    existing = agent.setdefault("skills", [])
    existing_ids = {s if isinstance(s, str) else s.get("id") for s in existing}
    if parsed["id"] not in existing_ids:
        existing.append({
            "id": parsed["id"],
            "name": parsed.get("name", parsed["id"].split("/")[-1]),
            "skill_name": parsed.get("slug", parsed["id"].split("/")[-1]),
            "source": parsed.get("source", "skills.json"),
            "description": parsed.get("description", ""),
            "cached_path": parsed.get("cached_path", ""),
            "instructions": parsed.get("instructions", ""),
            "assigned_at": datetime.now().isoformat(),
            "assigned_by": "ceo",
            "agent_id": agent_id,
        })
        _save_project(f"CEO assigned skill {parsed['id']} to {agent_id}")
    return parsed

def _iter_ceo_skill_decisions(decision: Dict[str, Any]) -> List[Dict[str, Any]]:
    decisions = []
    if isinstance(decision, dict):
        if decision.get("decision") == "assign_skill":
            decisions.append(decision)
        for d in decision.get("decisions", []) or []:
            if isinstance(d, dict) and d.get("decision") == "assign_skill":
                decisions.append(d)
    return decisions

@app.post("/orchestrate")
def orchestrate(req: OrchestrateRequest):
    if not _orchestrator: raise HTTPException(503, "Orchestrator not initialised")
    try:
        context = _build_ceo_orchestration_context(req.pipeline_context)
        available_skills = context.get("available_skills", [])

        decision = _orchestrator.orchestrate(context)

        if isinstance(decision, dict):
            applied_skill_assignments = []
            for d in _iter_ceo_skill_decisions(decision):
                skill_id = (
                    d.get("skill_id")
                    or d.get("skill")
                    or (f"{d.get('skill_repo','')}/{d.get('skill_name','')}".strip("/") if d.get("skill_repo") and d.get("skill_name") else "")
                ).strip()
                agent_id = (d.get("target_agent_id") or d.get("agent_id") or "").strip()
                if not skill_id or not agent_id:
                    continue
                try:
                    parsed = _apply_ceo_skill_assignment(skill_id, agent_id, available_skills)
                    applied_skill_assignments.append({"skill_id": parsed.get("id", skill_id), "agent_id": agent_id})
                    _log("info", f"CEO assigned skill: {skill_id} -> {agent_id}")
                except Exception as e:
                    _log("warn", f"CEO assign_skill failed ({skill_id} -> {agent_id}): {e}")
            if applied_skill_assignments:
                decision["_applied_skill_assignments"] = applied_skill_assignments

        return decision
    except Exception as e:
        raise HTTPException(500, f"Orchestration error: {e}")

@app.get("/orchestrate/status")
def orchestrate_status():
    if not _orchestrator: raise HTTPException(503, "Orchestrator not initialised")
    return {"latest_decision": _orchestrator.get_latest_decision()}

@app.get("/orchestrate/decisions")
def orchestrate_decisions():
    if not _orchestrator: raise HTTPException(503, "Orchestrator not initialised")
    return {"decisions": _orchestrator.get_decisions()}

# -- Model routes --------------------------------------------------------------
@app.get("/models/providers")
def model_providers():
    if not _model_mgr: raise HTTPException(503, "Model manager not initialised")
    return _model_mgr.get_provider_status(PROJECT_ROOT)

@app.post("/models/providers/{provider_id}/test")
async def test_provider(provider_id: str):
    """Test a provider connection - checks key presence and does a minimal API call."""
    if not _model_mgr:
        raise HTTPException(503, "Model manager not initialised")
    from bridge import model_manager as mm
    status = mm.get_provider_status(PROJECT_ROOT)
    prov = status.get(provider_id)
    if not prov:
        raise HTTPException(404, f"Provider '{provider_id}' not found")
    if not prov.get("key_present"):
        return {"ok": False, "provider": provider_id,
                "error": f"API key missing - set {prov.get('env_var','?')} in .env",
                "key_present": False}
    # Do a minimal test call - list models or do a tiny completion
    try:
        env   = mm.load_env(PROJECT_ROOT)
        key   = env.get(prov.get("env_var","")) if prov.get("env_var") else None
        base  = prov.get("api_base","")
        # For all OpenAI-compatible providers: try GET /models
        import urllib.request as _ur
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        req = _ur.Request(base.rstrip("/") + "/models", headers=headers)
        with _ur.urlopen(req, timeout=8) as r:
            r.read()
        return {"ok": True, "provider": provider_id, "key_present": True,
                "message": f"Connection to {provider_id} successful"}
    except Exception as e:
        return {"ok": False, "provider": provider_id, "key_present": True,
                "error": f"Connection failed: {str(e)[:200]}"}

@app.get("/models/config")
def model_config():
    if not PROJECT: raise HTTPException(503, "No project loaded")
    cfg = PROJECT.get("model", {})
    safe = {k: v for k, v in cfg.items() if k != "providers"}
    safe["providers"] = {k: {kk: vv for kk, vv in v.items() if "key" not in kk.lower()}
                         for k, v in cfg.get("providers", {}).items()}
    return safe

# -- Runtime routes ------------------------------------------------------------
def _runtime_list_payload() -> Dict[str, Any]:
    """Return runtimes using whichever RuntimeManager API this build exposes."""
    if not _runtime_mgr:
        raise HTTPException(503, "Runtime manager not initialised")
    if hasattr(_runtime_mgr, "list_runtimes"):
        runtimes = _runtime_mgr.list_runtimes()
    elif hasattr(_runtime_mgr, "get_runtimes"):
        runtimes = _runtime_mgr.get_runtimes()
    else:
        runtimes = []
    return {"runtimes": runtimes, "count": len(runtimes) if isinstance(runtimes, list) else 0}

def _runtime_execute(runtime_id: str, context: Dict[str, Any], agent_id: Optional[str] = None, dry_run: bool = False) -> Dict[str, Any]:
    """Execute a runtime while supporting both legacy and newer RuntimeManager method names."""
    if not _runtime_mgr:
        raise HTTPException(503, "Runtime manager not initialised")
    if hasattr(_runtime_mgr, "execute"):
        return _runtime_mgr.execute(runtime_id, context, agent_id, dry_run)
    if hasattr(_runtime_mgr, "run"):
        try:
            return _runtime_mgr.run(runtime_id, context, agent_id=agent_id, dry_run=dry_run)
        except TypeError:
            return _runtime_mgr.run(runtime_id, context, dry_run=dry_run)
    raise HTTPException(500, "Runtime manager has no executable runtime method")

def _runtime_requires_approval(runtime_id: str, agent_id: Optional[str] = None) -> Dict[str, Any]:
    """Normalize runtime permission checks across RuntimeManager versions."""
    if not _runtime_mgr:
        raise HTTPException(503, "Runtime manager not initialised")
    if hasattr(_runtime_mgr, "check_permission"):
        try:
            perm = _runtime_mgr.check_permission(runtime_id, agent_id)
        except TypeError:
            perm = _runtime_mgr.check_permission(runtime_id, None)
        if not perm.get("allowed", False):
            raise HTTPException(403, perm.get("reason", "Runtime permission denied"))
        return perm
    return {"allowed": True, "requires_approval": False}

@app.get("/runtimes")
def list_runtimes():
    return _runtime_list_payload()

@app.get("/runtimes/{runtime_id}")
def get_runtime(runtime_id: str):
    if not _runtime_mgr:
        raise HTTPException(503, "Runtime manager not initialised")
    rt = _runtime_mgr.get_runtime(runtime_id) if hasattr(_runtime_mgr, "get_runtime") else None
    if not rt:
        raise HTTPException(404, f"Runtime not found: {runtime_id}")
    return rt

@app.post("/runtimes/{runtime_id}/test")
def test_runtime(runtime_id: str):
    return _runtime_execute(runtime_id, {"_test": True, "_dry_run": True}, agent_id=None, dry_run=True)

@app.post("/runtimes/{runtime_id}/run")
def run_runtime_route(runtime_id: str, req: RuntimeRunRequest):
    ctx = dict(req.context or {})
    if req.dry_run:
        return _runtime_execute(runtime_id, ctx, agent_id=req.agent_id, dry_run=True)
    perm = _runtime_requires_approval(runtime_id, req.agent_id)
    requires = bool(perm.get("requires_approval"))
    if not requires and hasattr(_runtime_mgr, "requires_approval"):
        try:
            requires = bool(_runtime_mgr.requires_approval(runtime_id))
        except Exception:
            requires = False
    if requires:
        return {
            "ok": False,
            "requires_approval": True,
            "message": f"Runtime '{runtime_id}' requires human approval before execution",
            "runtime_id": runtime_id,
        }
    result = _runtime_execute(runtime_id, ctx, agent_id=req.agent_id, dry_run=False)
    if isinstance(result, dict) and result.get("delegate_to_executor"):
        return run_executor_sync(result["delegate_to_executor"], ctx)
    return result

@app.post("/runtimes/reload")
def reload_runtimes():
    if not _runtime_mgr:
        raise HTTPException(503, "Runtime manager not initialised")
    if hasattr(_runtime_mgr, "reload"):
        _runtime_mgr.reload()
    payload = _runtime_list_payload()
    return {"status": "reloaded", "count": payload.get("count", 0)}

# -- System Verification -------------------------------------------------------
@app.get("/system/test")
async def system_test():
    """
    End-to-end system verification. Tests all five subsystems.
    Safe to run against live system - uses dry_run and test fixtures.
    """
    import asyncio, time as _time
    results = {
        "task_worker":   {"status": "skip", "detail": "not initialised"},
        "scheduler":     {"status": "skip", "detail": "not initialised"},
        "pipeline":      {"status": "skip", "detail": "no project"},
        "orchestration": {"status": "skip", "detail": "not initialised"},
        "approval_gate": {"status": "skip", "detail": "not initialised"},
        "recovery":      {"status": "skip", "detail": ""},
        "timestamp":     datetime.now().isoformat(),
    }

    # -- 1. Task Worker --------------------------------------------------------
    if _task_worker:
        try:
            before = _task_worker.status()
            # Create a test task assigned to first available agent
            agents = PROJECT.get("agents", [])
            test_agent = agents[0]["id"] if agents else None
            if test_agent:
                task = _task_worker.create_task(
                    title="[SYSTEM TEST] Verification task",
                    assigned_to=test_agent,
                    task_type="agent",
                    instructions='Reply with exactly: {"status": "test_ok"}',
                    input_data={"_test": True}
                )
                task_id = task["id"]
                # Wait up to 3s for worker to pick it up
                deadline = _time.time() + 3
                picked_up = False
                while _time.time() < deadline:
                    t = _task_worker.get_task(task_id)
                    if t and t["status"] in ("running", "complete", "failed"):
                        picked_up = True; break
                    _time.sleep(0.3)
                if picked_up:
                    results["task_worker"] = {"status": "pass", "detail": f"Task {task_id} picked up by worker automatically"}
                else:
                    # Check if worker is running
                    st = _task_worker.status()
                    if not st["running"]:
                        results["task_worker"] = {"status": "warn", "detail": "Worker not running - start from Automation tab"}
                    else:
                        results["task_worker"] = {"status": "warn", "detail": "Task created but not picked up within 3s - worker may be processing another task"}
            else:
                results["task_worker"] = {"status": "warn", "detail": "No agents in project to assign test task"}
        except Exception as e:
            results["task_worker"] = {"status": "fail", "detail": str(e)}

    # -- 2. Scheduler ---------------------------------------------------------
    if _scheduler:
        try:
            if not _scheduler.is_available():
                results["scheduler"] = {"status": "warn", "detail": "APScheduler not installed - run: pip install apscheduler"}
            else:
                test_sched = _scheduler.add_schedule({
                    "id": "_system_test_sched",
                    "name": "[SYSTEM TEST] 10s interval",
                    "type": "agent",
                    "target_id": PROJECT.get("agents", [{}])[0].get("id", "none"),
                    "schedule_type": "interval",
                    "interval_seconds": 10,
                    "enabled": True,
                    "input": {"_test": True}
                })
                fired = _scheduler.run_now("_system_test_sched")
                _time.sleep(0.5)
                # Clean up test schedule
                _scheduler.remove_schedule("_system_test_sched")
                results["scheduler"] = {"status": "pass" if fired else "fail",
                                         "detail": "Schedule created, fired, and removed" if fired else "Schedule fire failed"}
        except Exception as e:
            results["scheduler"] = {"status": "fail", "detail": str(e)}
            try: _scheduler.remove_schedule("_system_test_sched")
            except: pass

    # -- 3. Pipeline (dry run) -------------------------------------------------
    if PROJECT:
        try:
            steps = [s for s in PROJECT.get("pipeline", {}).get("execution_order", []) if s["type"] == "executor"]
            if not steps:
                results["pipeline"] = {"status": "warn", "detail": "No executor steps in pipeline to test"}
            else:
                eid = steps[0].get("executor_id")
                dr = run_executor_sync(eid, {"_test": True}, {}, dry_run=True)
                if dr.get("ok"):
                    results["pipeline"] = {"status": "pass", "detail": f"Dry-run executor '{eid}': ok, run history writable"}
                else:
                    results["pipeline"] = {"status": "fail", "detail": f"Dry-run executor '{eid}' failed: " + dr.get("error", "")}
        except Exception as e:
            results["pipeline"] = {"status": "fail", "detail": str(e)}

    # -- 4. CEO Orchestration --------------------------------------------------
    if _orchestrator:
        try:
            mock_ctx = {
                "steps": {"news_context": {"status": "complete", "output": {"summary": "Test context"}}},
                "current_context": {"_system_test": True}
            }
            decision = _orchestrator.orchestrate(mock_ctx)
            if decision and decision.get("decision"):
                # Verify safety rule: if executor steps exist, must require human approval
                has_exec = any(s.get("type") == "executor" for s in PROJECT.get("pipeline", {}).get("execution_order", []))
                safety_ok = (not has_exec) or decision.get("requires_human_approval", False)
                if not safety_ok:
                    results["orchestration"] = {"status": "fail", "detail": "SAFETY VIOLATION: CEO approved without human gate on executor pipeline"}
                else:
                    results["orchestration"] = {"status": "pass",
                        "detail": f"CEO decision: {decision['decision']} | human_gate_required: {decision.get('requires_human_approval')} | safety: ok"}
            else:
                results["orchestration"] = {"status": "fail", "detail": "Orchestrator returned no decision"}
        except Exception as e:
            results["orchestration"] = {"status": "fail", "detail": str(e)}

    # -- 5. Approval Gate ------------------------------------------------------
    try:
        # Simulate: check that a pipeline with requires_approval blocks
        pipeline_steps = PROJECT.get("pipeline", {}).get("execution_order", [])
        gated_steps = [s for s in pipeline_steps if s.get("requires_approval")]
        has_executor  = any(s.get("type") == "executor" for s in pipeline_steps)
        # Verify orchestrator enforces the gate
        if _orchestrator and has_executor:
            # Check that the orchestrator hard rule is in place
            from bridge.orchestrator import CEOOrchestrator
            mock_dec = {"decision": "approve", "allow_execution": True,
                        "requires_human_approval": False, "reason": "test"}
            # _validate_decision should force requires_human_approval = True
            try:
                validated = _orchestrator._validate_decision(mock_dec, {})
                if validated.get("requires_human_approval"):
                    results["approval_gate"] = {"status": "pass",
                        "detail": f"Hard safety rule enforced: {len(gated_steps)} gate(s) in pipeline, CEO cannot bypass"}
                else:
                    results["approval_gate"] = {"status": "fail",
                        "detail": "SAFETY FAILURE: CEO was able to set requires_human_approval=False on executor pipeline"}
            except Exception as e:
                results["approval_gate"] = {"status": "fail", "detail": f"Validation error: {e}"}
        elif gated_steps:
            results["approval_gate"] = {"status": "pass",
                "detail": f"{len(gated_steps)} approval gate(s) configured in pipeline"}
        else:
            results["approval_gate"] = {"status": "warn",
                "detail": "No approval gates configured - add requires_approval: true to a pipeline step"}
    except Exception as e:
        results["approval_gate"] = {"status": "fail", "detail": str(e)}

    # -- 6. Recovery state -----------------------------------------------------
    task_count  = len(_task_worker.get_tasks()) if _task_worker else 0
    sched_count = len(_scheduler.list_schedules()) if _scheduler else 0
    run_count   = len(PIPELINE_RUNS)
    results["recovery"] = {
        "status": "pass",
        "detail": f"Tasks on disk: {task_count} | Schedules on disk: {sched_count} | Pipeline runs on disk: {run_count}"
    }

    # Summary
    statuses = [v["status"] for v in results.values() if isinstance(v, dict) and "status" in v]
    overall = "pass" if all(s == "pass" for s in statuses) else "warn" if "fail" not in statuses else "fail"
    results["overall"] = overall
    _log("info", f"System test complete: {overall.upper()} - {statuses}")
    return results


# -- Auth system ---------------------------------------------------------------
import hashlib, hmac

def _users_file() -> Path:
    return APP_DATA_DIR / "users.json"

def _sessions_file() -> Path:
    return APP_DATA_DIR / "sessions.json"

def _dedupe_paths(paths: List[Path]) -> List[Path]:
    seen = set()
    out: List[Path] = []
    for raw in paths:
        try:
            p = Path(raw).expanduser().resolve()
        except Exception:
            continue
        key = str(p).lower() if sys.platform == "win32" else str(p)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out

def _legacy_data_dirs() -> List[Path]:
    """Old builds reused DATA_DIR for both app accounts and project runtime state."""
    candidates: List[Path] = [DATA_DIR, PROJECT_DATA_DIR]
    if PROJECT_ROOT:
        candidates.append(Path(PROJECT_ROOT) / "data" / "rockoagents")
    return [p for p in _dedupe_paths(candidates) if p != APP_DATA_DIR]

def _company_recovery_dirs() -> List[Path]:
    """
    Known places older builds may have written companies.json.
    Includes deep scan of Documents folder so any project name is found.
    """
    candidates: List[Path] = [
        APP_DATA_DIR,
        DATA_DIR,
        PROJECT_DATA_DIR,
        ROCKO_ROOT / "data" / "rockoagents",
        BRIDGE_DIR / "data" / "rockoagents",
        Path.cwd() / "data" / "rockoagents",
    ]
    if PROJECT_ROOT:
        root = Path(PROJECT_ROOT)
        candidates.extend([
            root / "data" / "rockoagents",
            root.parent / "data" / "rockoagents",
        ])
    home = Path.home()
    # Explicit well-known paths
    candidates.extend([
        home / "Documents" / "RockoAgentHub" / "data" / "rockoagents",
        home / "Documents" / "RockoAgents" / "data" / "rockoagents",
        home / "Documents" / "Companies" / "RockoAgentHub" / "data" / "rockoagents",
        home / "Documents" / "Companies" / "RockoAgents" / "data" / "rockoagents",
    ])
    # Deep scan: find ANY project folder under Documents that contains
    # data/rockoagents/companies.json — catches any project name (TheIrisAgency etc.)
    for scan_root in [home / "Documents", home]:
        if not scan_root.exists():
            continue
        try:
            for match in scan_root.glob("*/data/rockoagents"):
                candidates.append(match)
            for match in scan_root.glob("*/*/data/rockoagents"):
                candidates.append(match)
            for match in scan_root.glob("*/*/*/data/rockoagents"):
                candidates.append(match)
        except Exception:
            pass
    return _dedupe_paths(candidates)

def _read_json_dict_file(fp: Path) -> dict:
    if fp.exists():
        try:
            data = json.load(open(fp, encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}
    return {}

def _load_users() -> dict:
    users = _read_json_dict_file(_users_file())
    # Backward compatibility: recover users accidentally written to project-scoped DATA_DIR.
    for d in _legacy_data_dirs():
        for uid, user in _read_json_dict_file(d / "users.json").items():
            users.setdefault(uid, user)
    if users and not _users_file().exists():
        _save_users(users)
    return users

def _save_users(data: dict):
    _users_file().parent.mkdir(parents=True, exist_ok=True)
    with open(_users_file(), 'w', encoding="utf-8") as fp: json.dump(data, fp, indent=2)

def _load_sessions() -> dict:
    sessions = _read_json_dict_file(_sessions_file())
    # Backward compatibility: recover sessions accidentally written to project-scoped DATA_DIR.
    for d in _legacy_data_dirs():
        sessions.update(_read_json_dict_file(d / "sessions.json"))
    if sessions and not _sessions_file().exists():
        _save_sessions(sessions)
    return sessions

def _save_sessions(data: dict):
    _sessions_file().parent.mkdir(parents=True, exist_ok=True)
    with open(_sessions_file(), 'w', encoding="utf-8") as fp: json.dump(data, fp, indent=2)

def _hash_password(password: str, salt: str = None) -> tuple:
    if not salt:
        salt = uuid.uuid4().hex
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 260000)
    return h.hex(), salt

def _verify_password(password: str, stored_hash: str, salt: str) -> bool:
    h, _ = _hash_password(password, salt)
    return hmac.compare_digest(h, stored_hash)

def _get_user_from_session(token: str) -> dict:
    sessions = _load_sessions()
    if token not in sessions: return None
    user_id = sessions[token]
    users = _load_users()
    return users.get(user_id)

class AuthRequest(BaseModel):
    name:     str = ""
    email:    str = ""
    password: str = ""

class LoginRequest(BaseModel):
    email:    str = ""
    password: str = ""

@app.post("/auth/signup")
async def auth_signup(req: AuthRequest):
    if not req.email or not req.password:
        raise HTTPException(400, "Email and password required")
    users = _load_users()
    # Check email not taken
    for u in users.values():
        if u.get("email", "").lower() == req.email.lower():
            raise HTTPException(409, "An account with that email already exists")
    user_id = "user_" + uuid.uuid4().hex[:12]
    pw_hash, salt = _hash_password(req.password)
    user = {
        "id":         user_id,
        "name":       req.name or req.email.split("@")[0],
        "email":      req.email.lower(),
        "pw_hash":    pw_hash,
        "salt":       salt,
        "created_at": datetime.now().isoformat(),
    }
    users[user_id] = user
    _save_users(users)
    # Create session
    token = uuid.uuid4().hex
    sessions = _load_sessions()
    sessions[token] = user_id
    _save_sessions(sessions)
    _log("info", f"New user: {user['name']} ({user['email']})")
    return {"token": token, "user": {"id": user_id, "name": user["name"], "email": user["email"]}}

@app.post("/auth/login")
async def auth_login(req: LoginRequest):
    if not req.email or not req.password:
        raise HTTPException(400, "Email and password required")
    users = _load_users()
    found = None
    for u in users.values():
        if u.get("email", "").lower() == req.email.lower():
            found = u; break
    if not found or not _verify_password(req.password, found["pw_hash"], found["salt"]):
        raise HTTPException(401, "Invalid email or password")
    token = uuid.uuid4().hex
    sessions = _load_sessions()
    sessions[token] = found["id"]
    _save_sessions(sessions)
    _log("info", f"Login: {found['name']}")
    return {"token": token, "user": {"id": found["id"], "name": found["name"], "email": found["email"]}}

@app.get("/auth/me")
async def auth_me(authorization: str = None, request: Request = None):
    token = None
    if request:
        auth_header = request.headers.get("authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
    if not token:
        raise HTTPException(401, "No session token")
    user = _get_user_from_session(token)
    if not user:
        raise HTTPException(401, "Session expired or invalid")
    return {"user": {"id": user["id"], "name": user["name"], "email": user["email"]}}

@app.post("/auth/logout")
async def auth_logout(request: Request):
    auth_header = request.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else None
    if token:
        sessions = _load_sessions()
        sessions.pop(token, None)
        _save_sessions(sessions)
    return {"status": "logged_out"}

@app.get("/auth/export")
async def auth_export(request: Request):
    """Export all account data - for backup and device transfer."""
    auth_header = request.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else None
    user = _get_user_from_session(token) if token else None
    if not user:
        raise HTTPException(401, "Authentication required")
    companies = {k: v for k, v in _load_companies().items()
                 if v.get("user_id") == user["id"]}
    return {
        "export_version": "1.0",
        "exported_at":    datetime.now().isoformat(),
        "user":           {"id": user["id"], "name": user["name"], "email": user["email"]},
        "companies":      list(companies.values()),
    }

@app.post("/auth/import")
async def auth_import(request: Request):
    """Import account data from a backup export file."""
    auth_header = request.headers.get("authorization", "")
    token = auth_header[7:] if auth_header.startswith("Bearer ") else None
    user = _get_user_from_session(token) if token else None
    if not user:
        raise HTTPException(401, "Authentication required")
    body = await request.json()
    companies_data = body.get("companies", [])
    existing = _load_companies()
    imported = 0
    for co in companies_data:
        co["user_id"] = user["id"]  # reassign to current user
        existing[co["id"]] = co
        imported += 1
    _save_companies(existing)
    return {"status": "imported", "count": imported}


# -- Company registry ----------------------------------------------------------
def _companies_file() -> Path:
    return APP_DATA_DIR / "companies.json"

def _normalise_companies_payload(data: Any) -> dict:
    if isinstance(data, list):
        return {co["id"]: co for co in data if isinstance(co, dict) and "id" in co}
    if isinstance(data, dict):
        if "companies" in data and isinstance(data.get("companies"), list):
            return {co["id"]: co for co in data["companies"] if isinstance(co, dict) and "id" in co}
        return data
    return {}

def _read_companies_file(fp: Path) -> dict:
    if not fp.exists():
        return {}
    try:
        return _normalise_companies_payload(json.load(open(fp, encoding="utf-8")))
    except Exception as e:
        _log("warn", f"Could not read companies file {fp}: {e}")
        return {}

def _load_companies() -> dict:
    companies = _read_companies_file(_companies_file())

    # Backward compatibility: recover companies that older builds wrote to
    # project-scoped folders or nearby app folders instead of APP_DATA_DIR.
    recovered = 0
    for d in _company_recovery_dirs():
        legacy = _read_companies_file(d / "companies.json")
        for cid, co in legacy.items():
            if not isinstance(co, dict):
                continue
            if cid not in companies:
                companies[cid] = co
                recovered += 1
            else:
                existing = companies[cid]
                old_updated = str(existing.get("updated_at", existing.get("created_at", "")))
                new_updated = str(co.get("updated_at", co.get("created_at", "")))
                if new_updated and new_updated > old_updated:
                    merged = {**existing, **co}
                    companies[cid] = merged
                    recovered += 1

    if recovered:
        _save_companies(companies)
        _log("info", f"Recovered/merged {recovered} company record(s) into global app registry")

    # Also scan rockoagents_state.json — the frontend syncs its full state here
    # including companies array from the last browser session
    for state_dir in [APP_DATA_DIR, DATA_DIR, BRIDGE_DIR / "data" / "rockoagents",
                      Path.home() / "Documents" / "RockoAgentHub" / "data" / "rockoagents"]:
        state_file = state_dir / "rockoagents_state.json"
        if not state_file.exists():
            continue
        try:
            state_data = json.load(open(state_file, encoding="utf-8"))
            # frontend saves companies as nested data object
            raw_cos = (state_data.get("data", {}) or {}).get("companies", None)
            if raw_cos is None:
                raw_cos = state_data.get("companies", None)
            if raw_cos:
                parsed = _normalise_companies_payload(raw_cos)
                state_added = 0
                for cid, co in parsed.items():
                    if isinstance(co, dict) and cid not in companies:
                        companies[cid] = co
                        state_added += 1
                if state_added:
                    _save_companies(companies)
                    _log("info", f"Recovered {state_added} company record(s) from frontend state file")
        except Exception as e:
            _log("warn", f"Could not parse state file {state_file}: {e}")

    return companies

def _save_companies(data: dict):
    _companies_file().parent.mkdir(parents=True, exist_ok=True)
    with open(_companies_file(), 'w', encoding="utf-8") as fp:
        json.dump(data, fp, indent=2)

def _auto_migrate_paperteam():
    """Disabled - users create their own companies via the UI after login."""
    pass

@app.get("/companies")
async def list_companies(request: Request):
    companies = _load_companies()
    auth_hdr = request.headers.get("authorization", "")
    token = auth_hdr[7:] if auth_hdr.startswith("Bearer ") else None
    if token:
        user = _get_user_from_session(token)
        if user:
            repaired = False
            visible = {}
            for k, v in companies.items():
                owner = v.get("user_id")
                # Adopt ownerless companies
                if not owner:
                    v["user_id"] = user["id"]
                    owner = user["id"]
                    repaired = True
                if owner == user["id"]:
                    visible[k] = v

            # Zero-company fallback: if strict filtering hides everything,
            # the user's previous companies were saved under a different
            # session user_id (cleared localStorage, new account etc).
            # Adopt ALL recovered companies and reassign to current user.
            if not visible and companies:
                _log("info", f"No companies matched user {user['id']} — adopting all {len(companies)} recovered record(s)")
                for k, v in companies.items():
                    v["user_id"] = user["id"]
                    visible[k] = v
                repaired = True

            if repaired:
                _save_companies(companies)
                _log("info", f"Repaired company ownership for user {user['id']}")
            companies = visible
    return {"companies": list(companies.values())}

@app.post("/companies")
async def create_company(request: Request):
    body = await request.json()
    companies = _load_companies()
    cid = body.get("id") or f"company_{uuid.uuid4().hex[:8]}"
    # Deactivate others if this is set active
    if body.get("active"):
        for c in companies.values(): c["active"] = False
    # Get user from session token if provided
    auth_hdr = request.headers.get("authorization", "")
    session_token = auth_hdr[7:] if auth_hdr.startswith("Bearer ") else None
    session_user = _get_user_from_session(session_token) if session_token else None
    existing = companies.get(cid, {})
    companies[cid] = {
        "id":           cid,
        "display_name": body.get("display_name", existing.get("display_name", "")),
        "description":  body.get("description",  existing.get("description", "")),
        "logo_path":    body.get("logo_path",     existing.get("logo_path", "")),
        "project_path": body.get("project_path",  existing.get("project_path", "")),
        "active":       body.get("active",        existing.get("active", False)),
        "user_id":      session_user["id"] if session_user else body.get("user_id", existing.get("user_id", "")),
        "created_at":   existing.get("created_at", datetime.now().isoformat()),
        "updated_at":   datetime.now().isoformat(),
    }
    _save_companies(companies)
    action = "updated" if existing else "created"
    _log("info", f"Company {action}: {companies[cid]['display_name']}")
    return companies[cid]

@app.get("/companies/{company_id}")
def get_company(company_id: str):
    companies = _load_companies()
    if company_id not in companies: raise HTTPException(404, "Company not found")
    return companies[company_id]

@app.patch("/companies/{company_id}")
async def update_company(company_id: str, request: Request):
    body = await request.json()
    companies = _load_companies()
    if company_id not in companies: raise HTTPException(404, "Company not found")
    companies[company_id].update({k: v for k, v in body.items() if k not in ("id","created_at")})
    companies[company_id]["updated_at"] = datetime.now().isoformat()
    _save_companies(companies)
    return companies[company_id]

@app.post("/companies/{company_id}/activate")
def activate_company(company_id: str):
    companies = _load_companies()
    if company_id not in companies: raise HTTPException(404, "Company not found")
    for c in companies.values(): c["active"] = False
    companies[company_id]["active"] = True
    _save_companies(companies)
    return {"status": "activated", "company_id": company_id}

@app.delete("/companies/{company_id}")
def delete_company(company_id: str):
    companies = _load_companies()
    if company_id not in companies: raise HTTPException(404, "Company not found")
    del companies[company_id]
    _save_companies(companies)
    return {"status": "deleted", "company_id": company_id}

# -- Skills endpoints ----------------------------------------------------------

# -- Skills system - backed by skills.sh / GitHub -----------------------------
import html as _html

def _fetch_github_file(owner: str, repo: str, path: str) -> Optional[str]:
    """Fetch a file from GitHub by scraping the blob HTML page."""
    import re as _re
    url = f"https://github.com/{owner}/{repo}/blob/main/{path}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 RockoAgents/5.0"})
        with urllib.request.urlopen(req, timeout=15) as r:
            page = r.read().decode("utf-8", errors="replace")
        lines_m = _re.search(r'"rawLines":\[([^\]]*(?:"[^"]*"[^\]]*)*)\]', page, _re.DOTALL)
        if lines_m:
            raw = "[" + lines_m.group(1) + "]"
            lines = json.loads(raw)
            return "\n".join(lines)
    except Exception as e:
        _log("warn", f"GitHub fetch failed ({owner}/{repo}/{path}): {e}")
    return None

def _parse_skill_md(content: str, owner: str = "", repo: str = "", skill_name: str = "",
                    skill_id: str = "") -> dict:
    """Parse YAML frontmatter from a SKILL.md file. Accepts both legacy and skills.sh id formats."""
    import re as _re
    fm_m = _re.match(r"^---\n(.*?)\n---\n(.*)", content, _re.DOTALL)
    meta = {}
    body = content
    if fm_m:
        for line in fm_m.group(1).split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip()
        body = fm_m.group(2)
    # Canonical id: prefer skills.sh id, fall back to legacy format
    canon_id = skill_id or (f"{owner}/{repo}/{skill_name}".strip("/") if (owner or repo or skill_name) else "unknown")
    return {
        "id":           canon_id,
        "name":         meta.get("name", skill_name or canon_id.split("/")[-1]),
        "description":  meta.get("description", meta.get("description", "")),
        "repo":         f"{owner}/{repo}".strip("/"),
        "skill_name":   skill_name or canon_id.split("/")[-1],
        "source":       "skills.sh",
        "instructions": body.strip(),
        "raw":          content,
    }

@app.get("/skills")
def list_skills():
    """
    Load skills from local sources — fast, no network required.
    Reads from (in priority order):
      1. skills.json in project root or bridge root
      2. .rocko_skills/ directory (skills fetched from skills.sh and saved to disk)
      3. ~/.clawd/skills/ directory (Clawd-Code installed skills)
    """
    # Try skills.json first
    search_paths = []
    if PROJECT_ROOT:
        search_paths.append(Path(PROJECT_ROOT) / "skills.json")
    search_paths.append(ROCKO_ROOT / "skills.json")
    for sp in search_paths:
        if sp.exists():
            try:
                data = json.load(open(sp))
                if data.get("skills"):
                    return data
            except Exception:
                pass

    # Build from local skill directories
    skills = []
    seen   = set()

    # .rocko_skills/ — skills previously fetched from skills.sh
    rocko_dirs = []
    if PROJECT_ROOT:
        rocko_dirs.append(Path(PROJECT_ROOT) / ".rocko_skills")
    rocko_dirs.append(ROCKO_ROOT / ".rocko_skills")
    for rdir in rocko_dirs:
        if rdir.exists():
            for f in sorted(rdir.glob("*.md")):
                name = f.stem.split("__")[-1]
                sid  = f.stem.replace("__", "/")
                if sid not in seen:
                    seen.add(sid)
                    skills.append({"id": sid, "name": name, "source": sid,
                                   "description": "", "installs": 0,
                                   "source_type": "local", "cached": True})

    # ~/.clawd/skills/ — Clawd-Code installed skills
    clawd_dir = Path.home() / ".clawd" / "skills"
    if clawd_dir.exists():
        for skill_dir in sorted(clawd_dir.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists() and skill_dir.name not in seen:
                seen.add(skill_dir.name)
                skills.append({"id": skill_dir.name, "name": skill_dir.name,
                                "source": "~/.clawd/skills", "description": "",
                                "installs": 0, "source_type": "local", "cached": True})

    if skills:
        _log("info", f"Loaded {len(skills)} local skill(s)")
        return {"version": "1.0", "skills": skills, "source": "local"}

    return {"version": "1.0", "skills": []}

def _skillssh_fetch(skill_id: str) -> tuple:
    """
    Core skills.sh fetch — GET /api/v1/skills/{id}
    Returns (skill_md: str, skill_dict: dict).
    Saves to .rocko_skills/ for offline use and Clawd-Code alignment.
    Uses SKILLS_SH_API_KEY env var if set (600 req/min vs 60 req/min).
    """
    import urllib.parse as _up
    url = f"{SKILLS_SH_API}/skills/{_up.quote(skill_id, safe='/')}"
    headers = {"User-Agent": "RockoAgents/5.0", "Accept": "application/json"}
    if SKILLS_SH_API_KEY:
        headers["Authorization"] = f"Bearer {SKILLS_SH_API_KEY}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.loads(r.read().decode("utf-8", errors="replace"))
    files    = data.get("files") or []
    skill_md = next((f["contents"] for f in files if f["path"] == "SKILL.md"), "")
    if not skill_md:
        raise ValueError(f"No SKILL.md in skills.sh response for {skill_id}")
    if PROJECT_ROOT:
        skills_dir = Path(PROJECT_ROOT) / ".rocko_skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        safe_name  = skill_id.replace("/", "__")
        cache_path = str(skills_dir / f"{safe_name}.md")
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(skill_md)
    else:
        cache_path = ""
    parts = skill_id.split("/")
    parsed = {
        "id":           data.get("id", skill_id),
        "name":         data.get("name") or parts[-1],
        "slug":         data.get("slug", parts[-1]),
        "source":       data.get("source", "/".join(parts[:2]) if len(parts) >= 2 else skill_id),
        "installs":     data.get("installs", 0),
        "instructions": skill_md,
        "raw":          skill_md,
        "cached_path":  cache_path,
        "files":        files,
    }
    return skill_md, parsed


@app.get("/skills/browse")
def browse_skills_sh(limit: int = 30, q: str = "", view: str = "all-time"):
    """
    Browse/search skills from skills.sh official REST API.
    GET /api/v1/skills        — leaderboard browse
    GET /api/v1/skills/search — semantic/fuzzy search
    Rate limit: 60 req/min unauthenticated, 600/min with SKILLS_SH_API_KEY in .env
    Falls back to local skill directories if skills.sh is unreachable.
    """
    import urllib.parse as _up
    try:
        if q:
            params = _up.urlencode({"q": q, "limit": min(limit, 200)})
            url = f"{SKILLS_SH_API}/skills/search?{params}"
        else:
            params = _up.urlencode({"view": view, "per_page": min(limit, 500), "page": 0})
            url = f"{SKILLS_SH_API}/skills?{params}"

        headers = {"User-Agent": "RockoAgents/5.0", "Accept": "application/json"}
        if SKILLS_SH_API_KEY:
            headers["Authorization"] = f"Bearer {SKILLS_SH_API_KEY}"

        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8", errors="replace"))

        raw = data.get("data", [])
        skills = [
            {
                "id":          s.get("id", ""),
                "name":        s.get("name", s.get("slug", "")),
                "slug":        s.get("slug", ""),
                "source":      s.get("source", ""),
                "installs":    s.get("installs", 0),
                "url":         s.get("url", ""),
                "install_url": s.get("installUrl", ""),
                "source_type": s.get("sourceType", "github"),
                "description": s.get("description", ""),
            }
            for s in raw
            if not s.get("isDuplicate")
        ]
        total = data.get("pagination", {}).get("total", len(skills))
        _log("info", f"Fetched {len(skills)} skills from skills.sh")
        return {"skills": skills, "source": "skills.sh", "total": total}
    except urllib.error.HTTPError as e:
        body = ""
        try: body = e.read().decode("utf-8", errors="replace")[:300]
        except: pass
        _log("warn", f"skills.sh HTTP {e.code}: {body or e.reason} — falling back to local")
        return list_skills()
    except urllib.error.URLError as e:
        _log("warn", f"skills.sh connection error: {e.reason} — falling back to local")
        return list_skills()
    except Exception as e:
        _log("warn", f"skills.sh error ({type(e).__name__}: {e}) — falling back to local")
        return list_skills()

@app.get("/skills/fetch")
def fetch_skill(id: str = "", source: str = "", slug: str = "", repo: str = "", skill: str = ""):
    """
    Fetch a skill's full SKILL.md content from skills.sh official API.
    Use the id field from browse results: e.g. id=anthropics/skills/frontend-design
    GET https://skills.sh/api/v1/skills/{id}
    Falls back to GitHub raw fetch for skills not yet snapshotted on skills.sh.
    """
    # Resolve canonical skill id from whichever params were provided
    resolved = (id or slug or
                (f"{source}/{skill}".strip("/") if source and skill else "") or
                (f"{repo}/{skill}".strip("/") if repo and skill else ""))
    if not resolved:
        raise HTTPException(400, "Provide id (e.g. anthropics/skills/frontend-design)")
    # Try skills.sh API first
    try:
        _, parsed = _skillssh_fetch(resolved)
        return {"ok": True, "skill": parsed}
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise HTTPException(500, f"skills.sh error {e.code} for {resolved}")
        _log("warn", f"skills.sh: {resolved} not snapshotted (404) — trying GitHub fallback")
    except Exception as e:
        _log("warn", f"skills.sh fetch failed for {resolved}: {e} — trying GitHub fallback")

    # GitHub fallback
    if repo and skill:
        owner, reponame = repo.split("/", 1) if "/" in repo else (repo, repo)
        for path in [f"{skill}/SKILL.md", f"skills/{skill}/SKILL.md", f".claude/skills/{skill}/SKILL.md"]:
            gh_content = _fetch_github_file(owner, reponame, path)
            if gh_content:
                parsed = _parse_skill_md(gh_content, owner, reponame, skill)
                return {"ok": True, "skill": parsed}

    raise HTTPException(404, f"Skill not found: {resolved_slug}")

@app.post("/skills/assign")
async def assign_skill(request: Request):
    """
    Assign a skill to an agent.
    Accepts new format: {id, agent_id}          e.g. id=anthropics/skills/frontend-design
    Accepts legacy format: {repo, skill_name, agent_id}
    Fetches SKILL.md from skills.sh, saves to .rocko_skills/, updates agent record.
    """
    body       = await request.json()
    agent_id   = body.get("agent_id", "")
    # Resolve skill id — new format takes priority
    skill_id   = (body.get("id") or body.get("skill_id") or "").strip()
    if not skill_id:
        repo       = body.get("repo", "")
        skill_name = body.get("skill_name", "")
        if repo and skill_name:
            skill_id = f"{repo}/{skill_name}".strip("/")
    if not skill_id:
        raise HTTPException(400, "Provide id (e.g. anthropics/skills/frontend-design) or repo+skill_name")
    try:
        _, parsed = _skillssh_fetch(skill_id)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            # GitHub fallback for skills not yet on skills.sh
            parts = skill_id.split("/")
            if len(parts) >= 3:
                owner_gh = parts[0]; repo_gh = parts[1]; sname_gh = "/".join(parts[2:])
                for path in [f"{sname_gh}/SKILL.md", f"skills/{sname_gh}/SKILL.md"]:
                    gh_raw = _fetch_github_file(owner_gh, repo_gh, path)
                    if gh_raw:
                        parsed = _parse_skill_md(gh_raw, owner_gh, repo_gh, sname_gh, skill_id=skill_id)
                        break
                else:
                    raise HTTPException(404, f"Skill not found: {skill_id}")
            else:
                raise HTTPException(404, f"Skill not found: {skill_id}")
        else:
            raise HTTPException(500, f"skills.sh error {e.code}")
    except Exception as e:
        raise HTTPException(500, f"Failed to fetch skill {skill_id}: {e}")
    # Attach skill to agent record in PROJECT
    if PROJECT and agent_id:
        for a in PROJECT.get("agents", []):
            if a.get("id") == agent_id:
                existing     = a.setdefault("skills", [])
                existing_ids = {s.get("id") for s in existing}
                if parsed["id"] not in existing_ids:
                    existing.append({
                        "id":           parsed["id"],
                        "name":         parsed["name"],
                        "skill_name":   parsed.get("slug", parsed["id"].split("/")[-1]),
                        "source":       parsed.get("source", "skills.sh"),
                        "cached_path":  parsed.get("cached_path", ""),
                        "instructions": parsed.get("instructions", ""),
                        "assigned_at":  datetime.now().isoformat(),
                        "agent_id":     agent_id,
                    })
                break
    _log("info", f"Skill assigned: {skill_id} -> agent {agent_id}")
    return {"ok": True, "skill": parsed, "agent_id": agent_id}

@app.post("/skills/{skill_id}/apply/{agent_id}")
async def apply_skill_legacy(skill_id: str, agent_id: str, request: Request):
    """
    Legacy apply endpoint — delegates to assign_skill using skills.sh id.
    skill_id path param is treated as skills.sh id (owner/repo/slug, URL-encoded).
    """
    import urllib.parse as _up
    decoded_id = _up.unquote(skill_id)
    try:
        _, parsed = _skillssh_fetch(decoded_id)
        if PROJECT and agent_id:
            for a in PROJECT.get("agents", []):
                if a.get("id") == agent_id:
                    existing     = a.setdefault("skills", [])
                    existing_ids = {s.get("id") for s in existing}
                    if parsed["id"] not in existing_ids:
                        existing.append({
                            "id":           parsed["id"],
                            "name":         parsed["name"],
                            "cached_path":  parsed.get("cached_path", ""),
                            "instructions": parsed.get("instructions", ""),
                            "assigned_at":  datetime.now().isoformat(),
                        })
                    break
        _log("info", f"Skill applied (legacy): {decoded_id} -> {agent_id}")
        return {"ok": True, "skill": parsed, "agent_id": agent_id,
                "applied_at": datetime.now().isoformat()}
    except Exception as e:
        raise HTTPException(500, f"Failed to apply skill {decoded_id}: {e}")



# -- Entry ---------------------------------------------------------------------
# ── CEO Agent creation — write files, register, assign skills ─────────────────
def _write_agent_files(agent_def: dict, project_root: str) -> str:
    if not project_root: return ""
    agent_id = (agent_def.get("agent_id") or agent_def.get("id") or
                agent_def.get("name","agent").lower().replace(" ","_").replace("-","_"))
    agent_dir  = Path(project_root) / "agents" / agent_id
    agent_dir.mkdir(parents=True, exist_ok=True)
    agent_file = agent_dir / "AGENT.md"
    instructions = agent_def.get("instructions", "")
    if not instructions:
        instructions = f"# {agent_def.get('name', agent_id)}\n\nRole: {agent_def.get('role','analyst')}\nDescription: {agent_def.get('description','')}\n\nYou are a specialist agent. Follow company policy and CEO direction.\n"
    with open(agent_file, "w", encoding="utf-8") as f:
        f.write(instructions)
    return str(agent_file)

def _register_agent_in_project(agent_def: dict, file_path: str) -> dict:
    global PROJECT
    if not PROJECT: return agent_def
    agent_id = (agent_def.get("agent_id") or agent_def.get("id") or
                agent_def.get("name","agent").lower().replace(" ","_"))
    agents = [a for a in PROJECT.get("agents", []) if a.get("id") != agent_id]
    new_agent = {
        "id":             agent_id, "name": agent_def.get("name", agent_id),
        "display_name":   agent_def.get("name", agent_id),
        "role":           agent_def.get("role", "analyst"), "type": "prompt",
        "description":    agent_def.get("description", ""),
        "instruction_file": f"agents\\{agent_id}\\AGENT.md",
        "model_provider": agent_def.get("model_provider","__company_default__"),
        "model_override": agent_def.get("model_override"), "pipeline_step": agent_id+"_step",
        "enabled": True, "status": "active", "skills": agent_def.get("skills",[]),
        "using_company_default": True, "created_by": "ceo",
        "created_at": datetime.now().isoformat(),
    }
    agents.append(new_agent)
    PROJECT["agents"] = agents
    _mark_routing_dirty(f"agent registered: {agent_id}")
    return new_agent

def _assign_skills_to_agent(agent_id: str, skills: list) -> list:
    """
    Assign a list of skills to an agent using skills.sh API.
    Each skill_ref can be:
      {"id": "anthropics/skills/frontend-design"}        — preferred
      {"repo": "anthropics/skills", "skill_name": "frontend-design"}  — legacy
    """
    if not skills: return []
    assigned = []
    for skill_ref in skills:
        # Resolve skill id
        skill_id = (skill_ref.get("id") or skill_ref.get("skill_id") or "").strip()
        if not skill_id:
            repo       = skill_ref.get("repo", "")
            skill_name = skill_ref.get("skill_name", "")
            if repo and skill_name:
                skill_id = f"{repo}/{skill_name}".strip("/")
        if not skill_id:
            continue
        try:
            _, parsed = _skillssh_fetch(skill_id)
            assigned.append({
                "id":           parsed["id"],
                "name":         parsed["name"],
                "skill_name":   parsed.get("slug", skill_id.split("/")[-1]),
                "source":       "skills.sh",
                "cached_path":  parsed.get("cached_path", ""),
                "agent_id":     agent_id,
                "assigned_at":  datetime.now().isoformat(),
                "instructions": parsed.get("instructions", ""),
            })
            _log("info", f"Skill assigned via skills.sh: {skill_id} -> {agent_id}")
        except Exception as e:
            _log("warn", f"Skill assign failed for {skill_id}: {e}")
    return assigned

def _build_effective_instructions(agent_id: str) -> str:
    if not PROJECT: return ""
    agent = next((a for a in PROJECT.get("agents",[]) if a.get("id")==agent_id), None)
    if not agent: return ""
    base = ""
    instr_file = agent.get("instruction_file","")
    if instr_file and PROJECT_ROOT:
        fp = Path(PROJECT_ROOT) / instr_file.replace("\\\\","/").replace("\\","/").replace("\\","/")
        if fp.exists():
            with open(fp, encoding="utf-8") as f: base = f.read()
    for skill in agent.get("skills",[]):
        cached = skill.get("cached_path","")
        if cached and Path(cached).exists():
            with open(cached, encoding="utf-8") as f: base += f"\n\n{f.read()}"
        elif skill.get("instructions"):
            base += f"\n\n{skill.get('instructions')}"
    return base

@app.post("/agents/create")
async def create_agent_api(request: Request):
    body = await request.json()
    file_path  = _write_agent_files(body, PROJECT_ROOT)
    registered = _register_agent_in_project(body, file_path)
    skills     = _assign_skills_to_agent(registered["id"], body.get("skills",[]))
    if skills:
        for a in (PROJECT.get("agents",[]) if PROJECT else []):
            if a.get("id") == registered["id"]: a["skills"] = skills; break
    return {"ok": True, "agent": registered, "file_path": file_path, "skills": skills}

@app.post("/agents/create_team")
async def create_team_api(request: Request):
    body    = await request.json()
    agents  = body.get("agents", [])
    reason  = body.get("reason", "")
    results = []
    for agent_def in agents:
        file_path  = _write_agent_files(agent_def, PROJECT_ROOT)
        registered = _register_agent_in_project(agent_def, file_path)
        skills     = _assign_skills_to_agent(registered["id"], agent_def.get("skills",[]))
        if skills:
            for a in (PROJECT.get("agents",[]) if PROJECT else []):
                if a.get("id") == registered["id"]: a["skills"] = skills; break
        results.append({"agent": registered, "file_path": file_path, "skills": skills})
        _log("info", f"Team agent created: {registered['name']}")
    return {"ok": True, "reason": reason, "count": len(results), "agents": results}

@app.get("/agents/{agent_id}/instructions")
def get_agent_effective_instructions(agent_id: str):
    effective = _build_effective_instructions(agent_id)
    return {"agent_id": agent_id, "instructions": effective, "length": len(effective)}

@app.post("/agents/{agent_id}/skills/assign")
async def assign_agent_skills(agent_id: str, request: Request):
    body   = await request.json()
    skills = body.get("skills", [])
    result = _assign_skills_to_agent(agent_id, skills)
    for a in (PROJECT.get("agents",[]) if PROJECT else []):
        if a.get("id") == agent_id:
            existing     = a.get("skills", [])
            existing_ids = {s.get("id") for s in existing}
            for s in result:
                if s.get("id") not in existing_ids: existing.append(s)
            a["skills"] = existing; break
    _mark_routing_dirty(f"skills assigned to agent: {agent_id}")
    return {"ok": True, "agent_id": agent_id, "skills_assigned": result}

# ── Native Execution Engine endpoints ─────────────────────────────────────────
@app.get("/engine/executors")
def list_engine_executors():
    if not _exec_engine: raise HTTPException(503, "Execution engine not ready")
    return {"executors": _exec_engine.get_executors(), "engine": "native"}

@app.post("/engine/run/{executor_id}")
async def engine_run(executor_id: str, request: Request):
    if not _exec_engine: raise HTTPException(503, "Execution engine not ready")
    body = {}
    try: body = await request.json()
    except: pass
    ctx    = body.get("context", body.get("input", {}))
    dry    = body.get("dry_run", False)
    bypass = body.get("bypass_approval", False)
    if _exec_engine.requires_approval(executor_id) and not bypass:
        return {"ok": False, "requires_approval": True,
                "risk_level": _exec_engine.get_risk_level(executor_id)}
    return _exec_engine.run(executor_id, ctx, dry_run=dry, bypass_approval=bypass)

@app.post("/engine/run/{executor_id}/approve")
async def engine_run_approved(executor_id: str, request: Request):
    if not _exec_engine: raise HTTPException(503, "Execution engine not ready")
    body = {}
    try: body = await request.json()
    except: pass
    return _exec_engine.run(executor_id, body.get("context",{}), bypass_approval=True)

@app.get("/models/providers/list")
def list_all_providers():
    import bridge.model_manager as mm
    return {"providers": mm.BUILTIN_PROVIDERS}

@app.post("/models/providers/{provider_id}/validate")
async def validate_provider_endpoint(provider_id: str, request: Request):
    body = {}
    try: body = await request.json()
    except: pass
    import bridge.model_manager as mm
    return mm.validate_provider(provider_id, PROJECT_ROOT, body.get("base_url",""))


def cli_main(argv=None):
    """Callable entry point - used by PyInstaller exe and rockoagents.cli"""
    global VERBOSE
    import sys as _s
    if argv is not None:
        _s.argv = argv
    # ---------------------------------------------------------------------
    parser = argparse.ArgumentParser(description="RockoAgents Bridge v5")
    parser.add_argument("--project",    default=None)
    parser.add_argument("--port",       type=int, default=8787)
    parser.add_argument("--host",       default="127.0.0.1")
    parser.add_argument("--verbose",    action="store_true")
    parser.add_argument("--no-browser", action="store_true", dest="no_browser")
    args = parser.parse_args()
    VERBOSE = args.verbose

    ui_url = f"http://{args.host}:{args.port}"

    # -- Load silently first ------------------------------------------------
    # Load project only if explicitly passed - never hardcode a default
    ok = False
    if args.project:
        print(f"  Loading project...")
        ok = load_project(args.project)
        if ok:
            proj_name = PROJECT.get("project", {}).get("name", "?")
            print(f"  Project: {proj_name}")
    print(f"  Initialising subsystems...")
    try:
        _init_subsystems()
        print(f"  Scheduler: ready")
        print(f"  Task worker: running")
        print(f"  Orchestrator: ready")
        print(f"  Model manager: ready")
    except Exception as e:
        print(f"  Subsystem warning: {e}")

    # Run validation silently - results available at /validate endpoint
    validation_result = {"errors": [], "warns": []}
    if ok:
        try:
            validation_result = validate_project()
        except Exception:
            validation_result = {"errors": [], "warns": []}
    print()

    # -- Open app window ----------------------------------------------------
    if not args.no_browser:
        def _open_app(url):
            time.sleep(2.0)
            launched = False
            if sys.platform == "win32":
                for browser in [
                    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                ]:
                    if Path(browser).exists():
                        subprocess.Popen([browser, f"--app={url}"])
                        launched = True; break
            elif sys.platform == "darwin":
                for browser in [
                    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
                    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
                ]:
                    if Path(browser).exists():
                        subprocess.Popen([browser, f"--app={url}"])
                        launched = True; break
            if not launched: webbrowser.open(url)
        threading.Thread(target=_open_app, args=(f"{ui_url}/",), daemon=True).start()

    # ── Banner (after all loading, just before uvicorn) ────────────────────
    BANNER = r"""
██████╗  ██████╗  ██████╗██╗  ██╗ ██████╗      █████╗  ██████╗ ███████╗███╗   ██╗████████╗███████╗
██╔══██╗██╔═══██╗██╔════╝██║ ██╔╝██╔═══██╗    ██╔══██╗██╔════╝ ██╔════╝████╗  ██║╚══██╔══╝██╔════╝
██████╔╝██║   ██║██║     █████╔╝ ██║   ██║    ███████║██║  ███╗█████╗  ██╔██╗ ██║   ██║   ███████╗
██╔══██╗██║   ██║██║     ██╔═██╗ ██║   ██║    ██╔══██║██║   ██║██╔══╝  ██║╚██╗██║   ██║   ╚════██║
██║  ██║╚██████╔╝╚██████╗██║  ██╗╚██████╔╝    ██║  ██║╚██████╔╝███████╗██║ ╚████║   ██║   ███████║
╚═╝  ╚═╝ ╚═════╝  ╚═════╝╚═╝  ╚═╝ ╚═════╝    ╚═╝  ╚═╝ ╚═════╝ ╚══════╝╚═╝  ╚═══╝   ╚═╝   ╚══════╝
"""
    print(BANNER)
    print("  " + "-" * 94)
    print("  Self-hosted local agent orchestration  |  v5.0")
    print("  " + "-" * 94)
    print()
    print("+   RockoAgents starting")
    print("|")
    print(f"|  Bridge:  {ui_url}")
    print(f"|  UI:      {ui_url}/")
    print("|")
    print("|  Status:")
    print(f"|    {'OK' if ok else 'o'}  {'Project loaded' if ok else 'No project - use UI to add one'}")
    print("|    OK  Scheduler: ready")
    print("|    OK  Task worker: running")
    print("|    OK  CEO orchestrator: ready")
    print("|")
    print("|  Details:  /validate  .  /project  .  /logs  .  /docs")
    print("|")
    if args.verbose:
        print(f"|  [verbose] Project root: {PROJECT_ROOT or 'none'}")
        print(f"|  [verbose] App data dir: {APP_DATA_DIR}")
        print(f"|  [verbose] Project data: {PROJECT_DATA_DIR}")
        for e in validation_result.get("errors", []): print(f"|  FAIL {e}")
        for w in validation_result.get("warns",  []): print(f"|  WARN {w}")
        print("|")
    if not args.no_browser:
        print(f"+  RockoAgents is ready.")
        print(f"   Opening {ui_url}/ in app window")
    else:
        print(f"+  RockoAgents is ready.")
        print(f"   Open {ui_url}/")
    print()

    # Start background heartbeat so terminal shows system is alive
    import threading as _th
    def _heartbeat():
        import time as _t
        _t.sleep(30)
        while True:
            try:
                tasks = _task_worker.status() if _task_worker else {}
                q = tasks.get("queued_count", 0)
                r = tasks.get("running_count", 0)
                scheds = len(_scheduler.list_schedules()) if _scheduler else 0
                if r > 0:
                    print(f"  [active] {r} task(s) running, {q} queued, {scheds} schedule(s)")
                _t.sleep(60)
            except Exception:
                _t.sleep(60)
    _th.Thread(target=_heartbeat, daemon=True).start()

    global _port
    _port = args.port
    uvicorn.run(app, host=args.host, port=args.port,
        log_level="info", access_log=True,
        log_config={
            "version": 1, "disable_existing_loggers": False,
            "formatters": {"rocko": {"()": "uvicorn.logging.DefaultFormatter",
                "fmt": "[%(asctime)s] %(levelprefix)s %(message)s", "datefmt": "%H:%M:%S", "use_colors": True}},
            "handlers": {"default": {"formatter": "rocko", "class": "logging.StreamHandler", "stream": "ext://sys.stdout"}},
            "loggers": {
                "uvicorn":        {"handlers": ["default"], "level": "INFO", "propagate": False},
                "uvicorn.error":  {"level": "INFO"},
                "uvicorn.access": {"handlers": ["default"], "level": "INFO", "propagate": False},
            },
        }
    )

if __name__ == "__main__":
    cli_main()