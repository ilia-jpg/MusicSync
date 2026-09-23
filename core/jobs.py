import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import Dict, Optional, Any, Callable, Tuple, List
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

# --- Enums ---
class JobType(str, Enum):
    DOWNLOAD_COLLECTION = "DOWNLOAD_COLLECTION"
    VERIFY_LIBRARY = "VERIFY_LIBRARY"
    REFRESH_COLLECTION = "REFRESH_COLLECTION"
    DISCOVER_MATCH = "DISCOVER_MATCH"
    UPDATE_EXPORT = "UPDATE_EXPORT"
    UPDATE_CIRCUIT = "UPDATE_CIRCUIT"
    ANALYZE_TRACK = "ANALYZE_TRACK"

class JobStatus(Enum):
    QUEUED = auto()
    RUNNING = auto()
    PAUSED = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()

# --- Job Context & Models ---

class JobContext:
    """Passed into the worker function to allow cooperative updates and pausing."""
    def __init__(self, job_id: str, manager: 'JobManager'):
        self.job_id = job_id
        self._manager = manager
        self._cancel_event = threading.Event()
        self._pause_event = threading.Event()
        
        # A set event means "Not Paused" (allowed to proceed)
        self._pause_event.set()

    @property
    def is_cancelled(self) -> bool:
        """Worker should check this periodically and return cleanly if True."""
        return self._cancel_event.is_set()

    def wait_if_paused(self) -> None:
        """Worker should call this in loops. Blocks efficiently if paused."""
        self._pause_event.wait()

    def update_progress(self, current: int, total: int) -> None:
        self._manager._update_job_progress(self.job_id, current, total)

    def update_current_item(self, item: str) -> None:
        self._manager._update_job_item(self.job_id, item)

    def update_progress_message(self, message: str) -> None:
        self._manager._update_job_message(self.job_id, message)

    def _cancel(self) -> None:
        self._cancel_event.set()
        self._pause_event.set() # Unblock if currently paused

    def _pause(self) -> None:
        self._pause_event.clear()

    def _resume(self) -> None:
        self._pause_event.set()


@dataclass
class Job:
    """Data model representing a job. Safe for UI reads."""
    id: str
    description: str
    job_type: JobType
    
    status: JobStatus = JobStatus.QUEUED
    
    progress_current: int = 0
    progress_total: int = 0
    current_item: Optional[str] = None
    progress_message: Optional[str] = None
    error_message: Optional[str] = None
    
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    depends_on: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    # Internal hidden fields
    _func: Optional[Callable] = field(default=None, repr=False)
    _args: Tuple = field(default_factory=tuple, repr=False)
    _kwargs: Dict[str, Any] = field(default_factory=dict, repr=False)
    _context: Optional[JobContext] = field(default=None, repr=False)
    _lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def duration(self) -> float:
        with self._lock:
            if not self.started_at:
                return 0.0
            end_time = self.completed_at or datetime.now(timezone.utc)
            return (end_time - self.started_at).total_seconds()


# --- Manager ---

class JobManager:
    def __init__(self, max_workers: int = 4):
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="MusicSyncWorker")
        self._jobs: Dict[str, Job] = {}
        self._manager_lock = threading.Lock()
        self._is_shutting_down = False

    def submit(self, *args, **kwargs) -> str:
        """
        Universal dynamic parser. Guarantees backwards compatibility by auto-detecting
        the job type, description, and worker function regardless of CLI argument naming.
        """
        with self._manager_lock:
            if self._is_shutting_down:
                raise RuntimeError("Cannot submit jobs: JobManager is shutting down.")
                
            parsed_args = list(args)
            job_type = None
            description = None
            target_func = None
            
            # 1. Identify the worker function (callable)
            for i, arg in enumerate(parsed_args):
                if callable(arg):
                    target_func = parsed_args.pop(i)
                    break
            if not target_func:
                for k, v in list(kwargs.items()):
                    if callable(v):
                        target_func = kwargs.pop(k)
                        break

            # 2. Identify JobType
            for i, arg in enumerate(parsed_args):
                if isinstance(arg, JobType):
                    job_type = parsed_args.pop(i)
                    break
            if not job_type and 'job_type' in kwargs:
                val = kwargs.pop('job_type')
                job_type = val if isinstance(val, JobType) else JobType[val]

            # 3. Identify Description string
            for i, arg in enumerate(parsed_args):
                if isinstance(arg, str):
                    description = parsed_args.pop(i)
                    break
            if 'description' in kwargs:
                description = kwargs.pop('description')
            depends_on = list(kwargs.pop('depends_on', []) or [])
            metadata = dict(kwargs.pop('metadata', {}) or {})
                
            # Safeties
            if not target_func:
                raise ValueError(f"CRITICAL: No callable function found in submit() args={args} kwargs={kwargs}")
            if not job_type: job_type = JobType.DOWNLOAD_COLLECTION
            if not description: description = "Background Task"

            job_id = str(uuid.uuid4())
            context = JobContext(job_id, self)
            
            job = Job(
                id=job_id,
                description=description,
                job_type=job_type,
                _func=target_func,
                _args=tuple(parsed_args), # Remaining unpopped args go to the worker
                _kwargs=kwargs,           # Remaining unpopped kwargs go to the worker
                _context=context,
                depends_on=depends_on,
                metadata=metadata,
            )
            
            self._jobs[job_id] = job
            self._executor.submit(self._run_job, job_id)
            
            return job_id

    def get_job(self, job_id: str) -> Optional[Job]:
        with self._manager_lock:
            return self._jobs.get(job_id)

    def get_all_jobs(self) -> List[Job]:
        with self._manager_lock:
            return list(self._jobs.values())

    def find_active_jobs(self, *, job_type: JobType | None = None, metadata: Dict[str, Any] | None = None) -> List[Job]:
        metadata = metadata or {}
        with self._manager_lock:
            jobs = list(self._jobs.values())
        matches = []
        for job in jobs:
            with job._lock:
                if job.status not in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED):
                    continue
                if job_type and job.job_type != job_type:
                    continue
                if any(job.metadata.get(key) != value for key, value in metadata.items()):
                    continue
                matches.append(job)
        return matches

    def get_available_actions(self, job_id: str) -> List[str]:
        job = self.get_job(job_id)
        if not job: return []

        with job._lock:
            if job.status == JobStatus.QUEUED: return ["Cancel"]
            elif job.status == JobStatus.RUNNING: return ["Pause", "Cancel"]
            elif job.status == JobStatus.PAUSED: return ["Resume", "Cancel"]
            elif job.status == JobStatus.FAILED: return ["Retry", "Remove"]
            elif job.status in (JobStatus.COMPLETED, JobStatus.CANCELLED): return ["Remove"]
            return []

    # --- Job Lifecycle Controls ---

    def cancel(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job:
            with job._lock:
                if job.status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED):
                    job.status = JobStatus.CANCELLED
                    if job._context:
                        job._context._cancel()
                    if not job.started_at:
                        job.completed_at = datetime.now(timezone.utc)

    def pause(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job:
            with job._lock:
                if job.status == JobStatus.RUNNING:
                    job.status = JobStatus.PAUSED
                    if job._context:
                        job._context._pause()

    def resume(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job:
            with job._lock:
                if job.status == JobStatus.PAUSED:
                    job.status = JobStatus.RUNNING
                    if job._context:
                        job._context._resume()

    def retry(self, job_id: str) -> str:
        job = self.get_job(job_id)
        if not job: raise ValueError(f"Job {job_id} not found.")
            
        with job._lock:
            if job.status != JobStatus.FAILED:
                raise ValueError("Only failed jobs can be retried.")
            target = job._func
            desc = job.description
            jtype = job.job_type
            args = job._args
            kwargs = job._kwargs
            depends_on = job.depends_on
            metadata = job.metadata
            
        # Resubmit explicitly
        return self.submit(jtype, desc, target, *args, depends_on=depends_on, metadata=metadata, **kwargs)

    def remove_job(self, job_id: str) -> None:
        with self._manager_lock:
            job = self._jobs.get(job_id)
            if not job: return
            with job._lock:
                if job.status not in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
                    raise RuntimeError(f"Cannot remove a job while it is {job.status.name}")
            del self._jobs[job_id]

    def cleanup_finished_jobs(self) -> None:
        with self._manager_lock:
            to_remove = [
                jid for jid, job in self._jobs.items() 
                if job.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED)
            ]
            for jid in to_remove:
                del self._jobs[jid]

    # --- Internal Worker / Execution Methods ---

    def _run_job(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if not job: return

        if not self._wait_for_dependencies(job):
            return

        with job._lock:
            if job.status == JobStatus.CANCELLED: return 
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(timezone.utc)

        try:
            # FIX: Explicitly inject job_context as a keyword argument to prevent positional mapping collisions
            exec_kwargs = dict(job._kwargs)
            exec_kwargs['job_context'] = job._context
            
            job._func(*job._args, **exec_kwargs)
            
            with job._lock:
                if job._context and job._context.is_cancelled:
                    job.status = JobStatus.CANCELLED
                else:
                    job.status = JobStatus.COMPLETED

        except Exception as e:
            logger.exception("Job failed: %s (%s)", job.description, job.id)
            with job._lock:
                job.status = JobStatus.FAILED
                job.error_message = re.sub(r"\x1b\[[0-9;]*m", "", str(e))
                
        finally:
            with job._lock:
                job.completed_at = datetime.now(timezone.utc)

    def _wait_for_dependencies(self, job: Job) -> bool:
        while True:
            with job._lock:
                if job.status == JobStatus.CANCELLED or (job._context and job._context.is_cancelled):
                    job.completed_at = datetime.now(timezone.utc)
                    return False
                dependencies = list(job.depends_on)
            if not dependencies:
                return True

            all_completed = True
            for dependency_id in dependencies:
                dependency = self.get_job(dependency_id)
                if not dependency:
                    continue
                with dependency._lock:
                    dependency_status = dependency.status
                    dependency_description = dependency.description
                if dependency_status == JobStatus.COMPLETED:
                    continue
                if dependency_status in (JobStatus.FAILED, JobStatus.CANCELLED):
                    with job._lock:
                        job.status = JobStatus.CANCELLED
                        job.error_message = f"Dependency did not complete: {dependency_description}"
                        job.completed_at = datetime.now(timezone.utc)
                    return False
                all_completed = False

            if all_completed:
                return True
            with job._lock:
                job.current_item = "Waiting for dependent job"
                job.progress_message = "Waiting"
            if job._context:
                job._context.wait_if_paused()
            threading.Event().wait(0.5)

    # --- Internal Context Update Hooks ---

    def _update_job_progress(self, job_id: str, current: int, total: int) -> None:
        job = self.get_job(job_id)
        if job:
            with job._lock:
                job.progress_current = current
                job.progress_total = total

    def _update_job_item(self, job_id: str, item: str) -> None:
        job = self.get_job(job_id)
        if job:
            with job._lock:
                job.current_item = item

    def _update_job_message(self, job_id: str, message: str) -> None:
        job = self.get_job(job_id)
        if job:
            with job._lock:
                job.progress_message = message

    # --- Shutdown ---

    def shutdown(self, wait: bool = True) -> None:
        with self._manager_lock:
            self._is_shutting_down = True
            for job in self._jobs.values():
                with job._lock:
                    if job.status in (JobStatus.QUEUED, JobStatus.RUNNING, JobStatus.PAUSED):
                        if job._context:
                            job._context._cancel()
                        job.status = JobStatus.CANCELLED
                        
        self._executor.shutdown(wait=wait, cancel_futures=True)
