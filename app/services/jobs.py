"""Durable background jobs.

The database is the queue. A row's ``status`` says whether it is waiting ("queued") or running; a
worker claims a row with a single conditional UPDATE, so several server processes can share one
database without running a job twice. Running jobs renew a heartbeat; a job whose heartbeat goes
stale (crashed or killed process) is put back in the queue, up to ``job_max_attempts`` times.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import socket
import threading
import time
import uuid
from typing import Callable, Dict, Optional, Tuple, Type

from app.models import Base, Document, Paper
from app.state import AppState

log = logging.getLogger("examforge.jobs")

_HEARTBEAT_EVERY = 15.0
_REAP_EVERY = 15.0


def _now() -> dt.datetime:
    return dt.datetime.utcnow()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class JobRunner:
    # kind -> (model, running status, concurrency)
    KINDS: Dict[str, Tuple[Type[Base], str, int]] = {
        "document": (Document, "processing", 2),
        "paper": (Paper, "generating", 2),
    }

    def __init__(self, state: AppState, handlers: Dict[str, Callable[[AppState, str], None]]):
        self.state = state
        self.handlers = handlers
        self.worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._running: Dict[str, str] = {}  # row id -> kind
        self._thread: Optional[threading.Thread] = None

    # ---------------------------------------------------------------- lifecycle

    def start(self) -> None:
        self.recover_dead_workers()
        self._thread = threading.Thread(target=self._loop, name="job-runner", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def wake(self) -> None:
        self._wake.set()

    # ---------------------------------------------------------------- queue operations

    def _in_flight(self, kind: str) -> int:
        with self._lock:
            return sum(1 for k in self._running.values() if k == kind)

    def _claim_one(self, kind: str) -> Optional[str]:
        model, running, _ = self.KINDS[kind]
        with self.state.db.session() as s:
            row_id = (s.query(model.id).filter(model.status == "queued").order_by(model.created_at).limit(1).scalar())
            if not row_id:
                return None
            claimed = (s.query(model).filter(model.id == row_id, model.status == "queued")
                       .update({"status": running, "attempts": model.attempts + 1, "heartbeat_at": _now(),
                                "worker": self.worker_id}, synchronize_session=False))
            return row_id if claimed == 1 else None

    def _dispatch(self) -> None:
        pools = {"document": self.state.ingest_pool, "paper": self.state.generate_pool}
        for kind, (_, _, capacity) in self.KINDS.items():
            while not self._stop.is_set() and self._in_flight(kind) < capacity:
                row_id = self._claim_one(kind)
                if row_id is None:
                    break
                with self._lock:
                    self._running[row_id] = kind
                pools[kind].submit(self._run, kind, row_id)

    def _run(self, kind: str, row_id: str) -> None:
        try:
            self.handlers[kind](self.state, row_id)
        except Exception:  # handlers already record failures; this is the last line of defence
            log.exception("job %s %s crashed", kind, row_id)
        finally:
            with self._lock:
                self._running.pop(row_id, None)
            self.wake()

    def _heartbeat(self) -> None:
        with self._lock:
            held = dict(self._running)
        for kind in self.KINDS:
            ids = [i for i, k in held.items() if k == kind]
            if ids:
                model = self.KINDS[kind][0]
                with self.state.db.session() as s:
                    s.query(model).filter(model.id.in_(ids)).update({"heartbeat_at": _now()}, synchronize_session=False)

    def _requeue_or_fail(self, kind: str, rows, reason: str) -> int:
        count = 0
        for row in rows:
            if row.attempts >= self.state.settings.job_max_attempts:
                row.status = "failed"
                row.message = f"{reason} It was retried {row.attempts} times; please submit it again."[:1000]
            else:
                row.status, row.worker = "queued", ""
                row.message = "Resuming after an interruption."
            count += 1
        return count

    def reap_stale(self) -> int:
        cutoff = _now() - dt.timedelta(seconds=self.state.settings.job_lease_seconds)
        total = 0
        for kind, (model, running, _) in self.KINDS.items():
            with self.state.db.session() as s:
                rows = s.query(model).filter(model.status == running,
                                             (model.heartbeat_at == None) | (model.heartbeat_at < cutoff)).all()  # noqa: E711
                rows = [r for r in rows if r.id not in self._running]
                total += self._requeue_or_fail(kind, rows, "The worker running this job stopped responding.")
        if total:
            log.warning("reclaimed %d stale job(s)", total)
        return total

    def recover_dead_workers(self) -> int:
        """At boot, immediately reclaim jobs left by a process on this host that no longer exists."""
        host, total = socket.gethostname(), 0
        for kind, (model, running, _) in self.KINDS.items():
            with self.state.db.session() as s:
                rows = []
                for r in s.query(model).filter(model.status == running).all():
                    parts = (r.worker or "").split(":")
                    if len(parts) == 3 and parts[0] == host and parts[1].isdigit():
                        if int(parts[1]) != os.getpid() and not _pid_alive(int(parts[1])):
                            rows.append(r)
                    elif not r.worker:  # rows written before leases existed
                        rows.append(r)
                total += self._requeue_or_fail(kind, rows, "The server restarted while this job was running.")
        if total:
            log.warning("re-queued %d job(s) interrupted by a restart", total)
        return total

    # ---------------------------------------------------------------- loop

    def _loop(self) -> None:
        last_beat = last_reap = 0.0
        while not self._stop.is_set():
            try:
                self._dispatch()
                now = time.monotonic()
                if now - last_beat >= _HEARTBEAT_EVERY:
                    self._heartbeat()
                    last_beat = now
                if now - last_reap >= _REAP_EVERY:
                    self.reap_stale()
                    last_reap = now
            except Exception:
                log.exception("job runner iteration failed")
            self._wake.wait(1.0)
            self._wake.clear()
