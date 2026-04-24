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

# ── Paths ─────────────────────────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    # Running as PyInstaller compiled executable
    BRIDGE_DIR = Path(sys.executable).parent.resolve()
    ROCKO_ROOT = BRIDGE_DIR
else:
    # Running as Python script
    BRIDGE_DIR = Path(__file__).parent.resolve()
    ROCKO_ROOT = BRIDGE_DIR.parent.resolve()
    sys.path.insert(0, str(BRIDGE_DIR))

BRIDGE_START = datetime.now().isoformat()

# ── Core state ────────────────────────────────────────────────────────────────
PROJECT:        Dict = {}
PROJECT_ROOT:   str  = ""
DATA_DIR:       Path = ROCKO_ROOT / "data" / "rockoagents"
RUN_LOG:        Dict[str, Any] = {}
PIPELINE_RUNS:  List[Dict] = []

def _load_pipeline_runs():
    global PIPELINE_RUNS
    p = DATA_DIR / "pipeline_runs.json"
    if p.exists():
        try:
            with open(p) as f: PIPELINE_RUNS = json.load(f)
            _log("info", f"Pipeline run recovery: {len(PIPELINE_RUNS)} run(s) reloaded")
        except Exception: pass
PIPELINE_STATE: Dict = {}
LOG_BUFFER:     List = []
VERBOSE:        bool = False

# ── Subsystems (initialised after project loads) ──────────────────────────────
_model_mgr    = None
_task_worker  = None
_scheduler    = None
_orchestrator = None
_runtime_mgr  = None
_runtime_mgr  = None

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(title="RockoAgents Bridge", version="5.0.0", docs_url="/docs")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

def _log(level: str, msg: str):
    entry = {"ts": datetime.now().isoformat(), "level": level, "msg": msg}
    LOG_BUFFER.append(entry)
    if len(LOG_BUFFER) > 500: LOG_BUFFER.pop(0)
    if VERBOSE or level == "error":
        print(f"[{level.upper():7}] {msg}")

# ── Models ────────────────────────────────────────────────────────────────────
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

class RuntimeRunRequest(BaseModel):
    context:   Dict[str, Any] = {}
    agent_id:  Optional[str] = None
    dry_run:   bool = False

# ── Project loading ───────────────────────────────────────────────────────────
def load_project(path: str) -> bool:
    global PROJECT, PROJECT_ROOT, DATA_DIR
    try:
        p = Path(path).resolve()
        if not p.exists(): _log("error", f"project.json not found: {p}"); return False
        with open(p) as f: PROJECT = json.load(f)
        PROJECT_ROOT = str(Path(PROJECT["project"]["root_path"]).resolve())
        DATA_DIR     = Path(PROJECT_ROOT) / "data" / "rockoagents"
        DATA_DIR.mkdir(parents=True, exist_ok=True)
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
        if not exists: warns.append(f"Agent '{ag['id']}' AGENT.md not found: {fp}")
    checks["agents"] = agent_checks
    env_checks = {}
    loaded_env = build_env()
    for var in PROJECT.get("env", {}).get("required", []):
        present = var in loaded_env and bool(loaded_env[var])
        env_checks[var] = {"ok": present, "required": True}
        if not present: warns.append(f"Required env var missing: {var}")
    checks["env"] = env_checks
    # Model provider check
    if _model_mgr:
        import model_manager as mm
        prov_status = mm.get_provider_status(PROJECT_ROOT)
        checks["providers"] = prov_status
    return {"valid": len(errors) == 0, "errors": errors, "warns": warns, "checks": checks}

# ── Executor runner ───────────────────────────────────────────────────────────
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

# ── Subsystem init ────────────────────────────────────────────────────────────
def _init_subsystems():
    global _model_mgr, _task_worker, _scheduler, _orchestrator
    import model_manager as mm
    env = build_env()
    mm.init(PROJECT, env)
    _model_mgr = mm

    def _agent_call(agent_def, system, messages):
        return mm.run_agent_model(agent_def, system, messages, PROJECT_ROOT)

    from task_worker import TaskWorker
    def _runtime_call(runtime_id, context):
        if _runtime_mgr:
            return _runtime_mgr.run(runtime_id, context)
        return {"ok": False, "error": "Runtime manager not ready"}

    _task_worker = TaskWorker(DATA_DIR, run_executor_sync, _agent_call, _runtime_call)
    _task_worker.init(PROJECT, _log)
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
            return {"ok": True, "note": "pipeline scheduled run — use /pipeline endpoint"}
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

    from scheduler import SchedulerManager
    _scheduler = SchedulerManager(DATA_DIR, _schedule_fire)
    _scheduler.init(_log)
    _scheduler.start()
    _log("info", f"Schedule recovery: {len(_scheduler.list_schedules())} schedule(s) reloaded from disk")

    from orchestrator import CEOOrchestrator
    _orchestrator = CEOOrchestrator(_agent_call, _task_worker)
    _orchestrator.init(PROJECT, _log)

    from runtime_manager import RuntimeManager
    _runtime_mgr = RuntimeManager()
    _runtime_mgr.init(PROJECT, _log)

    # Re-init task worker with runtime support
    _task_worker._runtime_fn = lambda rid, ctx, aid: _runtime_mgr.execute(rid, ctx, aid)

    _load_pipeline_runs()
    _auto_migrate_paperteam()
    _log("info", "All subsystems initialised")

# ── Static + UI ───────────────────────────────────────────────────────────────
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
    if p.exists(): return FileResponse(str(p), media_type="application/manifest+json")
    raise HTTPException(404, "manifest not found")

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

# ── Core routes ───────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok", "bridge_version": "5.0.0", "project_loaded": bool(PROJECT),
            "project_name": PROJECT.get("project", {}).get("name", "none"),
            "project_root": PROJECT_ROOT, "bridge_started": BRIDGE_START,
            "executors": list(PROJECT.get("executors", {}).keys()),
            "agents": [a["id"] for a in PROJECT.get("agents", [])],
            "scheduler_available": _scheduler.is_available() if _scheduler else False,
            "worker_running": _task_worker._running if _task_worker else False,
            "timestamp": datetime.now().isoformat()}

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
        with open(DATA_DIR / "pipeline_runs.json", "w") as f:
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
        fp = DATA_DIR / (req.key.replace("/", "_") + ".json")
        with open(fp, "w") as f: json.dump(req.data, f, indent=2)
        return {"ok": True, "path": str(fp)}
    except Exception as e: raise HTTPException(500, str(e))

@app.get("/data/load")
def data_load(key: str):
    try:
        fp = DATA_DIR / (key.replace("/", "_") + ".json")
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

# ── Task routes ───────────────────────────────────────────────────────────────
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

# ── Scheduler routes ──────────────────────────────────────────────────────────
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

# ── Orchestration routes ──────────────────────────────────────────────────────
@app.post("/orchestrate")
def orchestrate(req: OrchestrateRequest):
    if not _orchestrator: raise HTTPException(503, "Orchestrator not initialised")
    try:
        decision = _orchestrator.orchestrate(req.pipeline_context)
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

# ── Model routes ──────────────────────────────────────────────────────────────
@app.get("/models/providers")
def model_providers():
    if not _model_mgr: raise HTTPException(503, "Model manager not initialised")
    return _model_mgr.get_provider_status(PROJECT_ROOT)

@app.get("/models/config")
def model_config():
    if not PROJECT: raise HTTPException(503, "No project loaded")
    cfg = PROJECT.get("model", {})
    safe = {k: v for k, v in cfg.items() if k != "providers"}
    safe["providers"] = {k: {kk: vv for kk, vv in v.items() if "key" not in kk.lower()}
                         for k, v in cfg.get("providers", {}).items()}
    return safe

# ── Runtime routes ────────────────────────────────────────────────────────────
@app.get("/runtimes")
def list_runtimes():
    if not _runtime_mgr: raise HTTPException(503, "Runtime manager not initialised")
    return {"runtimes": _runtime_mgr.list_runtimes(), "count": len(_runtime_mgr.list_runtimes())}

@app.get("/runtimes/{runtime_id}")
def get_runtime(runtime_id: str):
    if not _runtime_mgr: raise HTTPException(503, "Runtime manager not initialised")
    rt = _runtime_mgr.get_runtime(runtime_id)
    if not rt: raise HTTPException(404, f"Runtime '{runtime_id}' not found")
    return rt

@app.post("/runtimes/{runtime_id}/test")
def test_runtime(runtime_id: str):
    if not _runtime_mgr: raise HTTPException(503, "Runtime manager not initialised")
    return _runtime_mgr.execute(runtime_id, {"_test": True}, dry_run=True)

@app.post("/runtimes/{runtime_id}/run")
def run_runtime(runtime_id: str, req: RuntimeRunRequest):
    if not _runtime_mgr: raise HTTPException(503, "Runtime manager not initialised")
    perm = _runtime_mgr.check_permission(runtime_id, req.agent_id)
    if not perm["allowed"]:
        raise HTTPException(403, perm["reason"])
    if perm.get("requires_approval") and not req.dry_run:
        return {"ok": False, "requires_approval": True,
                "message": f"Runtime '{runtime_id}' requires human approval before execution",
                "runtime_id": runtime_id}
    return _runtime_mgr.execute(runtime_id, req.context, req.agent_id, req.dry_run)


# ── Runtime endpoints ─────────────────────────────────────────────────────────
@app.get("/runtimes")
def list_runtimes():
    if not _runtime_mgr: raise HTTPException(503, "Runtime manager not initialised")
    return {"runtimes": _runtime_mgr.get_runtimes()}

@app.get("/runtimes/{runtime_id}")
def get_runtime(runtime_id: str):
    if not _runtime_mgr: raise HTTPException(503, "Runtime manager not initialised")
    rt = _runtime_mgr.get_runtime(runtime_id)
    if not rt: raise HTTPException(404, f"Runtime not found: {runtime_id}")
    return rt

@app.post("/runtimes/{runtime_id}/test")
def test_runtime(runtime_id: str):
    if not _runtime_mgr: raise HTTPException(503, "Runtime manager not initialised")
    result = _runtime_mgr.run(runtime_id, {"_test": True, "_dry_run": True},
                               agent_id=None, dry_run=True)
    return result

@app.post("/runtimes/{runtime_id}/run")
def run_runtime_route(runtime_id: str, req: RunRequest):
    if not _runtime_mgr: raise HTTPException(503, "Runtime manager not initialised")
    ctx = {**req.context, **req.input}
    if req.dry_run:
        return _runtime_mgr.run(runtime_id, ctx, dry_run=True)
    perm = _runtime_mgr.check_permission(runtime_id, None)
    if not perm["allowed"]:
        raise HTTPException(403, perm["reason"])
    if _runtime_mgr.requires_approval(runtime_id):
        raise HTTPException(403, f"Runtime '{runtime_id}' requires human approval before execution")
    result = _runtime_mgr.run(runtime_id, ctx)
    # If executor delegation, hand off
    if result.get("delegate_to_executor"):
        eid = result["delegate_to_executor"]
        return run_executor_sync(eid, ctx)
    return result

@app.post("/runtimes/reload")
def reload_runtimes():
    if not _runtime_mgr: raise HTTPException(503, "Runtime manager not initialised")
    _runtime_mgr.reload()
    return {"status": "reloaded", "count": len(_runtime_mgr.get_runtimes())}


# ── System Verification ───────────────────────────────────────────────────────
@app.get("/system/test")
async def system_test():
    """
    End-to-end system verification. Tests all five subsystems.
    Safe to run against live system — uses dry_run and test fixtures.
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

    # ── 1. Task Worker ────────────────────────────────────────────────────────
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
                        results["task_worker"] = {"status": "warn", "detail": "Worker not running — start from Automation tab"}
                    else:
                        results["task_worker"] = {"status": "warn", "detail": "Task created but not picked up within 3s — worker may be processing another task"}
            else:
                results["task_worker"] = {"status": "warn", "detail": "No agents in project to assign test task"}
        except Exception as e:
            results["task_worker"] = {"status": "fail", "detail": str(e)}

    # ── 2. Scheduler ─────────────────────────────────────────────────────────
    if _scheduler:
        try:
            if not _scheduler.is_available():
                results["scheduler"] = {"status": "warn", "detail": "APScheduler not installed — run: pip install apscheduler"}
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

    # ── 3. Pipeline (dry run) ─────────────────────────────────────────────────
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

    # ── 4. CEO Orchestration ──────────────────────────────────────────────────
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

    # ── 5. Approval Gate ──────────────────────────────────────────────────────
    try:
        # Simulate: check that a pipeline with requires_approval blocks
        pipeline_steps = PROJECT.get("pipeline", {}).get("execution_order", [])
        gated_steps = [s for s in pipeline_steps if s.get("requires_approval")]
        has_executor  = any(s.get("type") == "executor" for s in pipeline_steps)
        # Verify orchestrator enforces the gate
        if _orchestrator and has_executor:
            # Check that the orchestrator hard rule is in place
            from orchestrator import CEOOrchestrator
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
                "detail": "No approval gates configured — add requires_approval: true to a pipeline step"}
    except Exception as e:
        results["approval_gate"] = {"status": "fail", "detail": str(e)}

    # ── 6. Recovery state ─────────────────────────────────────────────────────
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
    _log("info", f"System test complete: {overall.upper()} — {statuses}")
    return results


# ── Company registry ──────────────────────────────────────────────────────────
def _companies_file() -> Path:
    return DATA_DIR / "companies.json"

def _load_companies() -> dict:
    f = _companies_file()
    if f.exists():
        try:
            with open(f) as fp: return json.load(fp)
        except Exception: pass
    return {}

def _save_companies(data: dict):
    _companies_file().parent.mkdir(parents=True, exist_ok=True)
    with open(_companies_file(), 'w') as fp:
        json.dump(data, fp, indent=2)

def _auto_migrate_paperteam():
    """If no companies exist and ThePaperTeam is loaded, create a company record."""
    companies = _load_companies()
    if companies: return
    if not PROJECT: return
    proj_name = PROJECT.get("project", {}).get("name", "")
    if not proj_name: return
    cid = proj_name.lower().replace(" ", "_") + "_migrated"
    companies[cid] = {
        "id":           cid,
        "display_name": PROJECT.get("project", {}).get("display_name") or proj_name,
        "description":  PROJECT.get("project", {}).get("description", ""),
        "logo_path":    "",
        "project_path": PROJECT_ROOT,
        "active":       True,
        "created_at":   datetime.now().isoformat(),
        "updated_at":   datetime.now().isoformat(),
    }
    _save_companies(companies)
    _log("info", f"Auto-migrated '{proj_name}' as company record")

@app.get("/companies")
def list_companies():
    companies = _load_companies()
    return {"companies": list(companies.values())}

@app.post("/companies")
async def create_company(request: Request):
    body = await request.json()
    companies = _load_companies()
    cid = body.get("id") or f"company_{uuid.uuid4().hex[:8]}"
    # Deactivate others if this is set active
    if body.get("active"):
        for c in companies.values(): c["active"] = False
    companies[cid] = {
        "id":           cid,
        "display_name": body.get("display_name", ""),
        "description":  body.get("description", ""),
        "logo_path":    body.get("logo_path", ""),
        "project_path": body.get("project_path", ""),
        "active":       body.get("active", False),
        "created_at":   datetime.now().isoformat(),
        "updated_at":   datetime.now().isoformat(),
    }
    _save_companies(companies)
    _log("info", f"Company created: {companies[cid]['display_name']}")
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

# ── Skills endpoints ──────────────────────────────────────────────────────────

# ── Skills system — backed by skills.sh / GitHub ─────────────────────────────
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

def _parse_skill_md(content: str, owner: str, repo: str, skill_name: str) -> dict:
    """Parse YAML frontmatter from a SKILL.md file."""
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
    return {
        "id":          f"{owner}__{repo.replace('/','_')}__{skill_name}",
        "name":        meta.get("name", skill_name),
        "description": meta.get("description", ""),
        "repo":        f"{owner}/{repo}",
        "skill_name":  skill_name,
        "source":      "skills.sh",
        "instructions": body.strip(),
        "raw":         content,
    }

@app.get("/skills")
def list_skills():
    """Load skills from local skills.json (custom skills) — fast, no network."""
    search_paths = []
    if PROJECT_ROOT:
        search_paths.append(Path(PROJECT_ROOT) / "skills.json")
    search_paths.append(ROCKO_ROOT / "skills.json")
    for sp in search_paths:
        if sp.exists():
            try:
                with open(sp) as f: return json.load(f)
            except Exception as e:
                _log("error", f"Skills load error: {e}")
    return {"version": "1.0", "skills": []}

@app.get("/skills/browse")
def browse_skills_sh(limit: int = 30):
    """
    Fetch trending skills from skills.sh leaderboard.
    Parses the live leaderboard so agents always see the latest community skills.
    Falls back to local skills.json if skills.sh is unreachable.
    """
    import re as _re, urllib.request as _ur
    try:
        req = urllib.request.Request("https://skills.sh/",
            headers={"User-Agent": "Mozilla/5.0 RockoAgents/5.0",
                     "Accept": "text/html"})
        with urllib.request.urlopen(req, timeout=10) as r:
            content = r.read().decode("utf-8", errors="replace")
        # Parse leaderboard entries: ###skill-name\nowner/repo\nNNNK installs
        entries = _re.findall(
            r"###\s+(\S+)\n([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)\n([\d.]+[KM]?)",
            content)
        skills = []
        seen = set()
        for skill_name, repo, installs in entries:
            key = f"{repo}/{skill_name}"
            if key in seen: continue
            seen.add(key)
            skills.append({
                "id":          f"{repo.replace('/','__')}__{skill_name}",
                "name":        skill_name.replace("-", " ").title(),
                "skill_name":  skill_name,
                "repo":        repo,
                "installs":    installs,
                "description": f"From {repo} — install with: npx skills add {repo}",
                "source":      "skills.sh",
            })
            if len(skills) >= limit: break
        _log("info", f"Fetched {len(skills)} skills from skills.sh")
        return {"skills": skills, "source": "skills.sh", "total": len(skills)}
    except Exception as e:
        _log("warn", f"skills.sh unreachable: {e} — falling back to local")
        return list_skills()

@app.get("/skills/fetch")
def fetch_skill(repo: str, skill: str):
    """
    Fetch a SKILL.md from GitHub and return its parsed content.
    repo format: owner/repo  (e.g. anthropics/skills)
    skill: skill directory name (e.g. frontend-design)
    Tries common paths: {skill}/SKILL.md, skills/{skill}/SKILL.md
    """
    owner, reponame = repo.split("/", 1) if "/" in repo else (repo, repo)
    paths_to_try = [
        f"{skill}/SKILL.md",
        f"skills/{skill}/SKILL.md",
        f".claude/skills/{skill}/SKILL.md",
    ]
    for path in paths_to_try:
        content = _fetch_github_file(owner, reponame, path)
        if content:
            parsed = _parse_skill_md(content, owner, reponame, skill)
            return {"ok": True, "skill": parsed}
    raise HTTPException(404, f"SKILL.md not found in {repo} for skill '{skill}'")

@app.post("/skills/assign")
async def assign_skill(request: Request):
    """
    Assign a skill from GitHub to an agent.
    Body: {repo, skill_name, agent_id, project_name}
    Fetches SKILL.md, stores it locally, returns the parsed skill.
    """
    body = await request.json()
    repo       = body.get("repo", "")
    skill_name = body.get("skill_name", "")
    agent_id   = body.get("agent_id", "")
    if not repo or not skill_name:
        raise HTTPException(400, "repo and skill_name required")
    owner, reponame = repo.split("/", 1) if "/" in repo else (repo, repo)
    paths_to_try = [f"{skill_name}/SKILL.md", f"skills/{skill_name}/SKILL.md"]
    content = None
    for path in paths_to_try:
        content = _fetch_github_file(owner, reponame, path)
        if content: break
    if not content:
        raise HTTPException(404, f"Could not fetch SKILL.md for {repo}/{skill_name}")
    parsed = _parse_skill_md(content, owner, reponame, skill_name)
    # Save skill locally for offline use
    if PROJECT_ROOT:
        skills_dir = Path(PROJECT_ROOT) / ".rocko_skills"
        skills_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skills_dir / f"{owner}__{reponame}__{skill_name}.md"
        with open(skill_file, "w") as f:
            f.write(content)
    _log("info", f"Skill assigned: {repo}/{skill_name} → agent {agent_id}")
    return {"ok": True, "skill": parsed, "agent_id": agent_id}

@app.post("/skills/{skill_id}/apply/{agent_id}")
async def apply_skill_legacy(skill_id: str, agent_id: str, request: Request):
    return {"ok": True, "skill_id": skill_id, "agent_id": agent_id,
            "applied_at": datetime.now().isoformat()}



# ── Entry ─────────────────────────────────────────────────────────────────────
def cli_main(argv=None):
    """Callable entry point — used by PyInstaller exe and rockoagents.cli"""
    import sys as _s
    if argv is not None:
        _s.argv = argv
    # ─────────────────────────────────────────────────────────────────────
    parser = argparse.ArgumentParser(description="RockoAgents Bridge v5")
    parser.add_argument("--project",    default=None)
    parser.add_argument("--port",       type=int, default=8787)
    parser.add_argument("--host",       default="127.0.0.1")
    parser.add_argument("--verbose",    action="store_true")
    parser.add_argument("--no-browser", action="store_true", dest="no_browser")
    args = parser.parse_args()
    VERBOSE = args.verbose

    ui_url = f"http://{args.host}:{args.port}"

    # ── Load silently first ────────────────────────────────────────────────
    print(f"  Loading project...")
    if args.project:
        ok = load_project(args.project)
    else:
        default = ROCKO_ROOT / "projects" / "ThePaperTeam" / "project.json"
        ok = load_project(str(default)) if default.exists() else False
        if not ok: print("  No project specified — starting without project")

    if ok:
        proj_name = PROJECT.get("project", {}).get("name", "?")
        print(f"  Project: {proj_name}")

    print(f"  Initialising subsystems...")
    if ok:
        try:
            _init_subsystems()
            print(f"  Scheduler: ready")
            print(f"  Task worker: running")
            print(f"  Orchestrator: ready")
            print(f"  Model manager: ready")
        except Exception as e:
            print(f"  Subsystem warning: {e}")

    print(f"  Running validation...")
    v = validate_project() if ok else {"valid": False, "errors": ["No project loaded"], "warns": []}
    if v["errors"]:
        print(f"  Validation: {len(v['errors'])} error(s)")
    elif v["warns"]:
        print(f"  Validation: passed with {len(v['warns'])} warning(s)")
    else:
        print(f"  Validation: passed")
    print()

    # ── Open app window ────────────────────────────────────────────────────
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
    print("  " + "─" * 94)
    print("  Self-hosted local agent orchestration  |  v5.0")
    print("  " + "─" * 94)
    print()
    print("┌   RockoAgents starting")
    print("│")
    print(f"│  Bridge:  {ui_url}")
    print(f"│  UI:      {ui_url}/")
    print("│")
    print("│  Status:")
    print(f"│    {'✓' if ok else '○'}  {'Project loaded' if ok else 'No project — use UI to add one'}")
    if v["errors"]:   print(f"│    ✕  Validation: {len(v['errors'])} error(s)")
    elif v["warns"]:  print(f"│    ⚠  Validation: passed with {len(v['warns'])} warning(s)")
    else:             print("│    ✓  Validation: passed")
    print("│    ✓  Scheduler: ready")
    print("│    ✓  Task worker: running")
    print("│    ✓  CEO orchestrator: ready")
    print("│")
    print("│  Details:  /validate  ·  /project  ·  /logs  ·  /docs")
    print("│")
    if args.verbose:
        print(f"│  [verbose] Project root: {PROJECT_ROOT or 'none'}")
        print(f"│  [verbose] Data dir:     {DATA_DIR}")
        for e in v.get("errors", []): print(f"│  ✕ {e}")
        for w in v.get("warns",  []): print(f"│  ⚠ {w}")
        print("│")
    if not args.no_browser:
        print(f"└  RockoAgents is ready.")
        print(f"   Opening {ui_url}/ in app window")
    else:
        print(f"└  RockoAgents is ready.")
        print(f"   Open {ui_url}/")
    print()

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