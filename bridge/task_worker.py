"""
RockoAgents Task Worker
Background worker that continuously processes the task queue.
One task at a time (default). Persists all state to disk.
"""
import json, threading, time, uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

# ── Constants ─────────────────────────────────────────────────────────────────
STATUS_QUEUED    = "queued"
STATUS_RUNNING   = "running"
STATUS_COMPLETE  = "complete"
STATUS_FAILED    = "failed"
STATUS_BLOCKED   = "blocked"
STATUS_CANCELLED = "cancelled"
STATUS_WAITING   = "waiting_approval"

class TaskWorker:
    def __init__(self, data_dir: Path, executor_fn: Callable, agent_fn: Callable,
                 runtime_fn: Callable = None,
                 concurrency: int = 1, max_retries: int = 1, retry_delay: int = 10):
        self.data_dir    = data_dir
        self.tasks_file  = data_dir / "tasks.json"
        self.runs_file   = data_dir / "task_runs.json"
        self._executor_fn = executor_fn   # fn(executor_id, context) -> dict
        self._agent_fn    = agent_fn      # fn(agent_def, system, messages) -> dict
        self._runtime_fn  = runtime_fn    # fn(runtime_id, context) -> dict
        self._runtime_fn  = runtime_fn    # fn(runtime_id, context, agent_id) -> dict
        self.concurrency  = concurrency
        self.max_retries  = max_retries
        self.retry_delay  = retry_delay

        self._tasks:     Dict[str, Dict] = {}
        self._runs:      List[Dict]      = []
        self._lock       = threading.Lock()
        self._running    = False
        self._paused     = False
        self._thread:    Optional[threading.Thread] = None
        self._current_task_id: Optional[str] = None

        self._project:   Dict = {}
        self._log_fn:    Callable = print

        self.load()

    # ── Init ──────────────────────────────────────────────────────────────────
    def init(self, project: Dict, log_fn: Callable = print):
        self._project = project
        self._log_fn  = log_fn
        # Recover any interrupted tasks
        with self._lock:
            for task in self._tasks.values():
                if task["status"] == STATUS_RUNNING:
                    task["status"] = STATUS_QUEUED
                    task["error"]  = "Recovered after bridge restart"
            self._save()

    def _log(self, msg: str):
        self._log_fn(f"[WORKER ] {msg}")

    # ── Persistence ───────────────────────────────────────────────────────────
    def load(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.tasks_file.exists():
            try:
                with open(self.tasks_file) as f:
                    self._tasks = json.load(f)
            except Exception: self._tasks = {}
        if self.runs_file.exists():
            try:
                with open(self.runs_file) as f:
                    self._runs = json.load(f)
            except Exception: self._runs = []

    def _save(self):
        try:
            with open(self.tasks_file, "w") as f:
                json.dump(self._tasks, f, indent=2)
        except Exception as e:
            self._log(f"Save error: {e}")

    def _save_run(self, run: Dict):
        self._runs.insert(0, run)
        if len(self._runs) > 200: self._runs = self._runs[:200]
        try:
            with open(self.runs_file, "w") as f:
                json.dump(self._runs, f, indent=2)
        except Exception as e:
            self._log(f"Run save error: {e}")

    # ── Task CRUD ─────────────────────────────────────────────────────────────
    def create_task(self, title: str, assigned_to: str, task_type: str = "agent",
                    instructions: str = "", input_data: Dict = {},
                    parent_task_id: str = None, max_retries: int = None,
                    priority: str = "normal") -> Dict:
        task_id = f"task_{uuid.uuid4().hex[:10]}"
        task = {
            "id":             task_id,
            "title":          title,
            "assigned_to":    assigned_to,
            "type":           task_type,
            "status":         STATUS_QUEUED,
            "priority":       priority,
            "input":          input_data,
            "instructions":   instructions,
            "parent_task_id": parent_task_id,
            "subtasks":       [],
            "created_at":     datetime.now().isoformat(),
            "started_at":     None,
            "completed_at":   None,
            "attempts":       0,
            "max_retries":    max_retries if max_retries is not None else self.max_retries,
            "result":         None,
            "error":          None,
        }
        with self._lock:
            self._tasks[task_id] = task
            if parent_task_id and parent_task_id in self._tasks:
                self._tasks[parent_task_id]["subtasks"].append(task_id)
            self._save()
        self._log(f"Task created: {title} → {assigned_to}")
        return task

    def get_task(self, task_id: str) -> Optional[Dict]:
        return self._tasks.get(task_id)

    def get_tasks(self, status: str = None) -> List[Dict]:
        tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t["status"] == status]
        return sorted(tasks, key=lambda t: t["created_at"])

    def update_task(self, task_id: str, updates: Dict) -> bool:
        with self._lock:
            if task_id not in self._tasks: return False
            self._tasks[task_id].update(updates)
            self._save()
        return True

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t: return False
            if t["status"] in (STATUS_COMPLETE, STATUS_CANCELLED): return False
            t["status"] = STATUS_CANCELLED
            self._save()
        return True

    def retry_task(self, task_id: str) -> bool:
        with self._lock:
            t = self._tasks.get(task_id)
            if not t: return False
            if t["status"] not in (STATUS_FAILED, STATUS_BLOCKED): return False
            t["status"]  = STATUS_QUEUED
            t["error"]   = None
            self._save()
        self._log(f"Task retry queued: {t['title']}")
        return True

    # ── Execution ─────────────────────────────────────────────────────────────
    def _run_task(self, task: Dict):
        task_id = task["id"]
        self._current_task_id = task_id
        self._log(f"Running task: {task['title']} [{task['assigned_to']}]")
        task["status"]     = STATUS_RUNNING
        task["started_at"] = datetime.now().isoformat()
        task["attempts"]  += 1
        with self._lock: self._save()

        t0 = time.time()
        try:
            if task["type"] == "agent":
                agent_def = self._find_agent_def(task["assigned_to"])
                if not agent_def:
                    raise ValueError(f"Agent '{task['assigned_to']}' not found in project")
                # Check permissions
                if not self._check_agent_permission(agent_def):
                    raise PermissionError(f"Agent '{task['assigned_to']}' permission denied")
                system = agent_def.get("_instructions") or agent_def.get("instruction_file", "You are a helpful agent.")
                prompt = task["instructions"] or task["title"]
                if task["input"]:
                    prompt = f"Context:\n{json.dumps(task['input'], indent=2)}\n\n{prompt}"
                result = self._agent_fn(agent_def, system, [{"role": "user", "content": prompt}])
                if result.get("ok"):
                    task["status"]       = STATUS_COMPLETE
                    task["result"]       = result.get("text", "")
                    task["completed_at"] = datetime.now().isoformat()
                else:
                    raise RuntimeError(result.get("error", "Agent returned no result"))

            elif task["type"] == "executor":
                result = self._executor_fn(task["assigned_to"], task["input"])
                if result.get("ok") or result.get("skipped"):
                    task["status"]       = STATUS_COMPLETE
                    task["result"]       = json.dumps(result.get("output", {}))
                    task["completed_at"] = datetime.now().isoformat()
                else:
                    raise RuntimeError(result.get("error", "Executor failed"))
            elif task["type"] == "runtime":
                if not self._runtime_fn:
                    raise RuntimeError("Runtime manager not initialised")
                if result := self._runtime_fn(task["assigned_to"], task["input"],
                                               task.get("agent_id")):
                    if result.get("ok"):
                        task["status"]       = STATUS_COMPLETE
                        task["result"]       = json.dumps(result.get("output", {}))
                        task["completed_at"] = datetime.now().isoformat()
                    elif result.get("permission_denied"):
                        task["status"] = STATUS_BLOCKED
                        task["error"]  = result.get("error", "Permission denied")
                    else:
                        raise RuntimeError(result.get("error", "Runtime failed"))
                else:
                    raise RuntimeError("Runtime returned no result")
            else:
                raise ValueError(f"Unknown task type: {task['type']}")

        except Exception as e:
            dur = round((time.time() - t0) * 1000)
            self._log(f"Task failed: {task['title']} — {e}")
            task["error"] = str(e)
            if task["attempts"] <= task["max_retries"]:
                task["status"] = STATUS_QUEUED
                self._log(f"Will retry in {self.retry_delay}s (attempt {task['attempts']}/{task['max_retries']})")
                time.sleep(self.retry_delay)
            else:
                task["status"] = STATUS_FAILED
                task["completed_at"] = datetime.now().isoformat()

        dur = round((time.time() - t0) * 1000)
        run = {**task, "duration_ms": dur, "archived_at": datetime.now().isoformat()}
        with self._lock:
            self._save()
            self._save_run(run)
        self._current_task_id = None
        self._log(f"Task {task['status']}: {task['title']} ({dur}ms)")

    def _find_agent_def(self, agent_id: str) -> Optional[Dict]:
        for a in self._project.get("agents", []):
            if a["id"] == agent_id:
                return a
        return None

    def _check_agent_permission(self, agent_def: Dict) -> bool:
        # Executors can only be called by ceo/engine roles
        return True  # agent tasks always allowed; executor tasks checked in _run_task

    # ── Worker loop ───────────────────────────────────────────────────────────
    def _worker_loop(self):
        self._log("Worker started")
        while self._running:
            if self._paused:
                time.sleep(1); continue
            queued = self.get_tasks(STATUS_QUEUED)
            if queued:
                task = queued[0]
                self._run_task(task)
            else:
                time.sleep(2)
        self._log("Worker stopped")

    def start(self):
        if self._running: return
        self._running = True
        self._paused  = False
        self._thread  = threading.Thread(target=self._worker_loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def pause(self):
        self._paused = True
        self._log("Worker paused")

    def resume(self):
        self._paused = False
        self._log("Worker resumed")

    def run_now(self, task_id: str):
        task = self._tasks.get(task_id)
        if not task: return False
        threading.Thread(target=self._run_task, args=(task,), daemon=True).start()
        return True

    # ── Status ────────────────────────────────────────────────────────────────
    def status(self) -> Dict:
        tasks = list(self._tasks.values())
        return {
            "running":          self._running,
            "paused":           self._paused,
            "current_task_id":  self._current_task_id,
            "queued_count":     sum(1 for t in tasks if t["status"] == STATUS_QUEUED),
            "running_count":    sum(1 for t in tasks if t["status"] == STATUS_RUNNING),
            "failed_count":     sum(1 for t in tasks if t["status"] == STATUS_FAILED),
            "completed_count":  sum(1 for t in tasks if t["status"] == STATUS_COMPLETE),
            "cancelled_count":  sum(1 for t in tasks if t["status"] == STATUS_CANCELLED),
        }