import datetime as dt
import socket
import sqlite3
import time

from sqlalchemy import inspect

from conftest import register
from app.db import Database
from app.exam.patterns import get_preset
from app.exam.schema import GenerationRequest
from app.models import Paper
from app.services.jobs import JobRunner


def _paper_row(owner_id, status, **kw):
    req = GenerationRequest(subject="Mathematics", pattern=get_preset("unit-test-20"), seed=3)
    return Paper(owner_id=owner_id, subject="Mathematics", title="t", status=status,
                 request=req.model_dump(mode="json"), **kw)


def _owner(client):
    h = register(client)
    return client.get("/api/auth/me", headers=h).json()["id"]


def _wait_status(state, pid, want, timeout=40):
    end = time.time() + timeout
    while time.time() < end:
        with state.db.session() as db:
            p = db.get(Paper, pid)
            if p.status in want:
                return p.status, p.attempts
        time.sleep(0.2)
    raise AssertionError("job did not finish")


def test_job_orphaned_by_a_dead_process_is_resumed_and_finishes(client):
    state = client.app.state.ctx
    uid = _owner(client)
    dead_worker = f"{socket.gethostname()}:99999999:abcdef"
    with state.db.session() as db:
        row = _paper_row(uid, "generating", attempts=1, worker=dead_worker, heartbeat_at=dt.datetime.utcnow())
        db.add(row)
        db.flush()
        pid = row.id
    assert state.jobs.recover_dead_workers() == 1
    state.jobs.wake()
    status, attempts = _wait_status(state, pid, ("ready", "failed"))
    assert status == "ready" and attempts == 2  # one interrupted run, one successful run


def test_stale_heartbeat_is_requeued_until_attempts_run_out(client):
    state = client.app.state.ctx
    uid = _owner(client)
    old = dt.datetime.utcnow() - dt.timedelta(hours=1)
    max_attempts = state.settings.job_max_attempts
    with state.db.session() as db:
        retry = _paper_row(uid, "generating", attempts=1, worker="other:1:x", heartbeat_at=old)
        exhausted = _paper_row(uid, "generating", attempts=max_attempts, worker="other:2:x", heartbeat_at=old)
        fresh = _paper_row(uid, "generating", attempts=1, worker="other:3:x", heartbeat_at=dt.datetime.utcnow())
        db.add_all([retry, exhausted, fresh])
        db.flush()
        ids = (retry.id, exhausted.id, fresh.id)
    state.jobs.stop()  # keep the loop from racing with the assertions below
    assert state.jobs.reap_stale() == 2
    with state.db.session() as db:
        assert db.get(Paper, ids[0]).status == "queued"
        failed = db.get(Paper, ids[1])
        assert failed.status == "failed" and "retried" in failed.message
        assert db.get(Paper, ids[2]).status == "generating"


def test_only_one_worker_can_claim_a_queued_job(client):
    state = client.app.state.ctx
    uid = _owner(client)
    state.jobs.stop()
    with state.db.session() as db:
        row = _paper_row(uid, "queued")
        db.add(row)
        db.flush()
        pid = row.id
    other = JobRunner(state, {})
    claims = [state.jobs._claim_one("paper"), other._claim_one("paper")]
    assert claims.count(pid) == 1 and claims.count(None) == 1


def test_database_adds_columns_missing_from_an_older_schema(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE papers (id VARCHAR(32) PRIMARY KEY, owner_id VARCHAR(32), title VARCHAR(300), "
                "subject VARCHAR(200), status VARCHAR(20), progress INTEGER, message TEXT, request JSON, "
                "content JSON, quality JSON, revision INTEGER, created_at DATETIME, updated_at DATETIME)")
    con.execute("INSERT INTO papers (id, owner_id, status) VALUES ('p1', 'u1', 'ready')")
    con.commit()
    con.close()
    db = Database(f"sqlite:///{path}")
    cols = {c["name"] for c in inspect(db.engine).get_columns("papers")}
    assert {"attempts", "heartbeat_at", "worker"} <= cols
    with db.session() as s:
        assert s.get(Paper, "p1").attempts == 0
    db.dispose()
