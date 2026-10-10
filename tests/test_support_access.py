"""Platform support can look inside a school only while that school's administrator allows it, read-only, and leaves a trail."""
import dataclasses
import datetime as dt
import time

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import SupportGrant

from conftest import BIOLOGY_TEXT

PW = "correct-horse-battery"
OPERATOR = "operator@platform.test"


def _join(c, email, name, **kw):
    r = c.post("/api/auth/register", json={"email": email, "password": PW, "name": name, **kw})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


def _wait(c, url, h):
    end = time.time() + 60
    while time.time() < end:
        d = c.get(url, headers=h).json()
        if d["status"] in ("ready", "failed"):
            assert d["status"] == "ready", d
            return d
        time.sleep(0.2)
    raise AssertionError("timeout")


@pytest.fixture()
def world(settings):
    app = create_app(dataclasses.replace(settings, tenancy="multi", superadmin_emails=[OPERATOR]))
    with TestClient(app) as c:
        w = {"app": app, "c": c}
        w["op"] = _join(c, OPERATOR, "Platform Operator")
        w["admin_a"] = _join(c, "admin@a.test", "Asha Admin", school_name="School A")
        w["admin_b"] = _join(c, "admin@b.test", "Bala Admin", school_name="School B")
        code = c.post("/api/admin/invites", headers=w["admin_a"], json={"email": "t@a.test"}).json()["code"]
        w["t_a"] = _join(c, "t@a.test", "Anita Teacher", invite_code=code)
        w["doc"] = c.post("/api/documents", headers=w["t_a"], files=[("files", ("notes.txt", BIOLOGY_TEXT.encode(), "text/plain"))]).json()["documents"][0]["id"]
        _wait(c, f"/api/documents/{w['doc']}", w["t_a"])
        r = c.post("/api/papers", headers=w["t_a"], json={"subject": "Mathematics", "pattern_id": "unit-test-20", "seed": 3,
                                                          "diagram_mode": "none", "syllabus_text": "Quadratic equations"})
        w["paper"] = _wait(c, f"/api/papers/{r.json()['id']}", w["t_a"])["id"]
        w["bpaper"] = None
        orgs = {o["name"]: o["id"] for o in c.get("/api/platform/orgs", headers=w["op"]).json()["orgs"]}
        w["org_a"], w["org_b"] = orgs["School A"], orgs["School B"]
        yield w


def _session(w, org_key="org_a"):
    r = w["c"].post(f"/api/platform/orgs/{w[org_key]}/support-session", headers=w["op"])
    return r


def _bearer(r):
    return {"Authorization": "Bearer " + r.json()["token"]}


def test_without_the_administrators_permission_support_cannot_open_a_school(world):
    c = world["c"]
    assert c.get("/api/platform/orgs", headers=world["op"]).json()["orgs"][0]["support"] == {"active": False}
    r = _session(world)
    assert r.status_code == 403 and "not allowed support access" in r.json()["detail"]
    assert c.get(f"/api/papers/{world['paper']}", headers=world["op"]).status_code == 404  # the operator's own login still sees nothing


def test_only_the_school_administrator_can_allow_or_stop_support_and_only_for_their_own_school(world):
    c = world["c"]
    assert c.post("/api/admin/support", headers=world["t_a"], json={"hours": 1}).status_code == 403
    assert c.get("/api/admin/support", headers=world["t_a"]).status_code == 403
    assert c.delete("/api/admin/support", headers=world["t_a"]).status_code == 403
    assert c.post("/api/admin/support", headers=world["admin_a"], json={"hours": 0}).status_code == 422
    assert c.post("/api/admin/support", headers=world["admin_a"], json={"hours": 73}).status_code == 422
    assert c.post("/api/admin/support", headers=world["admin_a"], json={"hours": 2, "note": "Help with Section B"}).status_code == 201
    orgs = {o["name"]: o for o in c.get("/api/platform/orgs", headers=world["op"]).json()["orgs"]}
    assert orgs["School A"]["support"]["active"] and orgs["School A"]["support"]["note"] == "Help with Section B"
    assert orgs["School B"]["support"] == {"active": False}  # School A's permission says nothing about School B
    assert _session(world, "org_b").status_code == 403
    assert _session(world, "org_a").status_code == 201
    # nobody but the operator can open a session
    for who in ("admin_a", "admin_b", "t_a"):
        assert c.post(f"/api/platform/orgs/{world['org_a']}/support-session", headers=world[who]).status_code == 403


def test_a_support_session_shows_the_schools_content_read_only(world):
    c = world["c"]
    c.post("/api/admin/support", headers=world["admin_a"], json={"hours": 1})
    sess = _session(world)
    assert sess.json()["school"] == "School A" and 1 <= sess.json()["minutes"] <= 60
    h = _bearer(sess)

    me = c.get("/api/auth/me", headers=h).json()
    assert me["role"] == "support" and me["organization"] == "School A" and me["is_superadmin"] is False
    assert me["support"]["school"] == "School A"

    papers = c.get("/api/papers", headers=h).json()["papers"]
    assert [p["id"] for p in papers] == [world["paper"]] and papers[0]["can_edit"] is False
    assert [d["id"] for d in c.get("/api/documents", headers=h).json()["documents"]] == [world["doc"]]
    assert c.get("/api/bank", headers=h).json()["total"] > 0
    full = c.get(f"/api/papers/{world['paper']}", headers=h).json()
    assert full["created_by"] == "Anita Teacher" and full["content"]["sections"]
    assert c.get(f"/api/documents/{world['doc']}", headers=h).json()["can_delete"] is False
    assert all(i["can_delete"] is False for i in c.get("/api/bank", headers=h).json()["items"])

    # nothing can be changed, created, downloaded or deleted
    content = full["content"]
    assert c.put(f"/api/papers/{world['paper']}", headers=h, json=content).status_code == 403
    assert c.delete(f"/api/papers/{world['paper']}", headers=h).status_code == 403
    assert c.delete(f"/api/documents/{world['doc']}", headers=h).status_code == 403
    assert c.post("/api/papers", headers=h, json={"subject": "Mathematics", "pattern_id": "unit-test-20"}).status_code == 403
    assert c.post(f"/api/papers/{world['paper']}/questions/q1/regenerate", headers=h, json={}).status_code == 403
    assert c.post("/api/documents", headers=h, files=[("files", ("x.txt", b"hello world " * 30, "text/plain"))]).status_code == 403
    assert c.get(f"/api/papers/{world['paper']}/export", headers=h).status_code == 403
    assert c.get(f"/api/papers/{world['paper']}/export-all", headers=h).status_code == 403
    # and it shows content only: no people, keys, billing, usage, logs or platform controls
    for path in ("/api/admin/users", "/api/admin/audit", "/api/admin/invites", "/api/admin/support", "/api/admin/billing",
                 "/api/ai/settings", "/api/billing", "/api/usage", "/api/platform/orgs"):
        assert c.get(path, headers=h).status_code == 403, path
    assert c.post("/api/admin/support", headers=h, json={"hours": 72}).status_code == 403  # support cannot grant itself access
    assert c.post(f"/api/platform/orgs/{world['org_a']}/support-session", headers=h).status_code == 403


def test_a_support_session_on_one_school_cannot_see_another(world):
    c = world["c"]
    other = c.post("/api/papers", headers=world["admin_b"], json={"subject": "Mathematics", "pattern_id": "unit-test-20", "seed": 5,
                                                               "diagram_mode": "none", "syllabus_text": "Triangles"})
    other_id = _wait(c, f"/api/papers/{other.json()['id']}", world["admin_b"])["id"]
    c.post("/api/admin/support", headers=world["admin_a"], json={"hours": 1})
    h = _bearer(_session(world))
    assert c.get(f"/api/papers/{other_id}", headers=h).status_code == 404
    assert other_id not in {p["id"] for p in c.get("/api/papers", headers=h).json()["papers"]}


def test_stopping_support_cuts_off_a_session_that_is_already_open(world):
    c = world["c"]
    c.post("/api/admin/support", headers=world["admin_a"], json={"hours": 1})
    h = _bearer(_session(world))
    assert c.get("/api/papers", headers=h).status_code == 200
    assert c.delete("/api/admin/support", headers=world["admin_a"]).status_code == 204
    r = c.get("/api/papers", headers=h)
    assert r.status_code == 403 and "ended" in r.json()["detail"]
    assert _session(world).status_code == 403
    assert c.get("/api/admin/support", headers=world["admin_a"]).json()["active"] is False
    # the operator's ordinary login is untouched
    assert c.get("/api/platform/orgs", headers=world["op"]).status_code == 200


def test_permission_expires_on_its_own_and_a_new_one_replaces_the_old(world):
    c, app = world["c"], world["app"]
    c.post("/api/admin/support", headers=world["admin_a"], json={"hours": 1})
    h = _bearer(_session(world))
    with app.state.ctx.db.session() as db:
        for g in db.query(SupportGrant).all():
            g.expires_at = dt.datetime.utcnow() - dt.timedelta(minutes=1)
    assert c.get("/api/papers", headers=h).status_code == 403
    assert _session(world).status_code == 403
    c.post("/api/admin/support", headers=world["admin_a"], json={"hours": 3})
    c.post("/api/admin/support", headers=world["admin_a"], json={"hours": 5, "note": "second"})
    status = c.get("/api/admin/support", headers=world["admin_a"]).json()
    assert status["active"] and status["note"] == "second"
    assert sum(1 for g in status["history"] if not g["ended_early"] and dt.datetime.fromisoformat(g["expires_at"][:-1]) > dt.datetime.utcnow()) == 1
    # a session never outlasts the permission behind it
    c.post("/api/admin/support", headers=world["admin_a"], json={"hours": 1})
    assert _session(world).json()["minutes"] <= 60


def test_the_school_sees_every_support_visit_in_its_own_audit_log_and_other_schools_do_not(world):
    c = world["c"]
    c.post("/api/admin/support", headers=world["admin_a"], json={"hours": 1, "note": "Section B help"})
    h = _bearer(_session(world))
    c.get("/api/papers", headers=h)  # a list is not logged item by item
    c.get(f"/api/papers/{world['paper']}", headers=h)
    c.get(f"/api/documents/{world['doc']}", headers=h)
    c.delete("/api/admin/support", headers=world["admin_a"])
    events = c.get("/api/admin/audit", headers=world["admin_a"]).json()["events"]
    actions = [e["action"] for e in events]
    for expected in ("support.allowed", "support.session_start", "support.view", "support.revoked"):
        assert expected in actions, (expected, actions)
    views = [e for e in events if e["action"] == "support.view"]
    assert {v["entity"] for v in views} == {"papers", "documents"} and all(OPERATOR in v["detail"] for v in views)
    assert any(OPERATOR in e["detail"] for e in events if e["action"] == "support.session_start")
    b_actions = [e["action"] for e in c.get("/api/admin/audit", headers=world["admin_b"]).json()["events"]]
    assert not any(a.startswith("support.") for a in b_actions)
    assert c.get("/api/admin/audit", headers=world["t_a"]).status_code == 403  # teachers do not see the log


def test_a_forged_or_unprivileged_support_token_is_refused(world):
    from app.models import User
    from app.security import create_token
    c, app = world["c"], world["app"]
    c.post("/api/admin/support", headers=world["admin_a"], json={"hours": 1})
    with app.state.ctx.db.session() as db:
        teacher = db.query(User).filter(User.email == "t@a.test").one()
        forged = create_token(teacher, app.state.ctx.settings.secret_key, 30, support_org=world["org_a"])
        unsigned = "Bearer " + forged[:-3] + "abc"
    assert c.get("/api/papers", headers={"Authorization": "Bearer " + forged}).status_code == 403  # a teacher is not the operator
    assert c.get("/api/papers", headers={"Authorization": unsigned}).status_code == 401
