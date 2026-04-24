"""
RockoAgents Scheduler
APScheduler-based scheduler. Supports interval and cron schedules.
Persists schedules to disk. Never bypasses approval gates.
"""
import json, uuid, threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

try:
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.interval import IntervalTrigger
    from apscheduler.triggers.cron import CronTrigger
    APSCHEDULER_AVAILABLE = True
except ImportError:
    APSCHEDULER_AVAILABLE = False

VALID_TYPES    = {"task", "pipeline", "agent", "executor"}
VALID_SCHEDULES = {"interval", "cron"}

class SchedulerManager:
    def __init__(self, data_dir: Path, run_fn: Callable):
        """
        run_fn(schedule_def) -> dict
        Called when a schedule fires. Must handle all schedule types.
        """
        self.data_dir       = data_dir
        self.schedules_file = data_dir / "schedules.json"
        self._run_fn        = run_fn
        self._schedules:    Dict[str, Dict] = {}
        self._lock          = threading.Lock()
        self._log_fn:       Callable = print
        self._started       = False

        if APSCHEDULER_AVAILABLE:
            self._scheduler = BackgroundScheduler(
                job_defaults={"misfire_grace_time": 60, "coalesce": True, "max_instances": 1}
            )
        else:
            self._scheduler = None

        self.load()

    def init(self, log_fn: Callable = print):
        self._log_fn = log_fn

    def _log(self, msg: str):
        self._log_fn(f"[SCHED  ] {msg}")

    # ── Persistence ───────────────────────────────────────────────────────────
    def load(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.schedules_file.exists():
            try:
                with open(self.schedules_file) as f:
                    self._schedules = json.load(f)
            except Exception:
                self._schedules = {}

    def _save(self):
        try:
            with open(self.schedules_file, "w") as f:
                json.dump(self._schedules, f, indent=2)
        except Exception as e:
            self._log(f"Save error: {e}")

    # ── Validation ────────────────────────────────────────────────────────────
    def _validate(self, defn: Dict) -> Optional[str]:
        if not defn.get("name"):             return "name is required"
        if defn.get("type") not in VALID_TYPES: return f"type must be one of {VALID_TYPES}"
        if not defn.get("target_id"):        return "target_id is required"
        if defn.get("schedule_type") not in VALID_SCHEDULES:
            return f"schedule_type must be one of {VALID_SCHEDULES}"
        if defn["schedule_type"] == "interval" and not defn.get("interval_seconds"):
            return "interval_seconds required for interval schedule"
        if defn["schedule_type"] == "cron" and not defn.get("cron"):
            return "cron expression required for cron schedule"
        return None

    # ── APScheduler trigger ───────────────────────────────────────────────────
    def _make_trigger(self, defn: Dict):
        if not APSCHEDULER_AVAILABLE: return None
        if defn["schedule_type"] == "interval":
            return IntervalTrigger(seconds=int(defn["interval_seconds"]))
        elif defn["schedule_type"] == "cron":
            parts = defn["cron"].split()
            if len(parts) == 5:
                minute, hour, dom, month, dow = parts
                return CronTrigger(minute=minute, hour=hour, day=dom, month=month, day_of_week=dow)
        return None

    def _fire(self, schedule_id: str):
        """Called by APScheduler when a schedule triggers."""
        with self._lock:
            defn = self._schedules.get(schedule_id)
        if not defn or not defn.get("enabled", True):
            return
        self._log(f"Schedule firing: {defn['name']}")
        defn["last_run_at"] = datetime.now().isoformat()
        try:
            result = self._run_fn(defn)
            defn["last_status"] = "success" if (result or {}).get("ok", True) else "error"
        except Exception as e:
            defn["last_status"] = "error"
            self._log(f"Schedule error [{defn['name']}]: {e}")
        with self._lock:
            if APSCHEDULER_AVAILABLE and self._scheduler:
                job = self._scheduler.get_job(schedule_id)
                if job:
                    next_run = job.next_run_time
                    defn["next_run_at"] = next_run.isoformat() if next_run else None
            self._save()

    # ── CRUD ──────────────────────────────────────────────────────────────────
    def add_schedule(self, defn: Dict) -> Dict:
        err = self._validate(defn)
        if err: raise ValueError(err)
        schedule_id = defn.get("id") or f"sched_{uuid.uuid4().hex[:8]}"
        record = {
            "id":              schedule_id,
            "name":            defn["name"],
            "type":            defn["type"],
            "target_id":       defn["target_id"],
            "schedule_type":   defn["schedule_type"],
            "interval_seconds": defn.get("interval_seconds"),
            "cron":            defn.get("cron"),
            "enabled":         defn.get("enabled", True),
            "input":           defn.get("input", {}),
            "created_at":      datetime.now().isoformat(),
            "last_run_at":     None,
            "next_run_at":     None,
            "last_status":     "never",
        }
        with self._lock:
            self._schedules[schedule_id] = record
            self._save()
        if self._started and record["enabled"]:
            self._register_job(schedule_id, record)
        self._log(f"Schedule added: {record['name']} ({record['schedule_type']})")
        return record

    def _register_job(self, schedule_id: str, record: Dict):
        if not APSCHEDULER_AVAILABLE or not self._scheduler: return
        trigger = self._make_trigger(record)
        if not trigger: return
        try:
            self._scheduler.add_job(
                self._fire, trigger, id=schedule_id,
                args=[schedule_id], replace_existing=True
            )
            job = self._scheduler.get_job(schedule_id)
            if job and job.next_run_time:
                record["next_run_at"] = job.next_run_time.isoformat()
        except Exception as e:
            self._log(f"Job register error: {e}")

    def remove_schedule(self, schedule_id: str) -> bool:
        with self._lock:
            if schedule_id not in self._schedules: return False
            del self._schedules[schedule_id]
            self._save()
        if APSCHEDULER_AVAILABLE and self._scheduler:
            try: self._scheduler.remove_job(schedule_id)
            except Exception: pass
        return True

    def pause_schedule(self, schedule_id: str) -> bool:
        with self._lock:
            s = self._schedules.get(schedule_id)
            if not s: return False
            s["enabled"] = False
            self._save()
        if APSCHEDULER_AVAILABLE and self._scheduler:
            try: self._scheduler.pause_job(schedule_id)
            except Exception: pass
        return True

    def resume_schedule(self, schedule_id: str) -> bool:
        with self._lock:
            s = self._schedules.get(schedule_id)
            if not s: return False
            s["enabled"] = True
            self._save()
        if APSCHEDULER_AVAILABLE and self._scheduler:
            try: self._scheduler.resume_job(schedule_id)
            except Exception: pass
        return True

    def update_schedule(self, schedule_id: str, updates: Dict) -> Optional[Dict]:
        with self._lock:
            s = self._schedules.get(schedule_id)
            if not s: return None
            s.update({k: v for k, v in updates.items() if k not in ("id", "created_at")})
            self._save()
        # Re-register with new trigger
        if self._started:
            self._register_job(schedule_id, s)
        return s

    def run_now(self, schedule_id: str) -> bool:
        s = self._schedules.get(schedule_id)
        if not s: return False
        threading.Thread(target=self._fire, args=(schedule_id,), daemon=True).start()
        return True

    def list_schedules(self) -> List[Dict]:
        return list(self._schedules.values())

    def get_schedule(self, schedule_id: str) -> Optional[Dict]:
        return self._schedules.get(schedule_id)

    # ── Lifecycle ─────────────────────────────────────────────────────────────
    def start(self):
        if not APSCHEDULER_AVAILABLE:
            self._log("APScheduler not installed — pip install apscheduler")
            return
        self._scheduler.start()
        self._started = True
        # Register all enabled schedules
        for sid, record in self._schedules.items():
            if record.get("enabled", True):
                self._register_job(sid, record)
        self._log(f"Scheduler started ({len(self._schedules)} schedules loaded)")

    def stop(self):
        if APSCHEDULER_AVAILABLE and self._scheduler and self._started:
            self._scheduler.shutdown(wait=False)
        self._started = False

    def is_available(self) -> bool:
        return APSCHEDULER_AVAILABLE