import dataclasses
import datetime as dt
import time

import pytest
from fastapi.testclient import TestClient

from app.exam.patterns import get_preset
from app.llm.base import LLMProvider
from app.llm.usage import UsageMeter
from app.main import create_app
from app.models import AiSetting, AuditLog, Invite, Organization, UsageLog, User
from app.services.ai_settings import resolve_ai
from app.services.tenancy import ensure_default_org
from app.services.usage import record_usage

PW = "correct-horse-battery"
OPERATOR = "operator@platform.test"


class Stub(LLMProvider):
    name, model = "stub", "stub-1"

    def __init__(self):
        super().__init__(1)

    def _complete(self, *a, **k):
        return "{}"


@pytest.fixture()
def multi(settings):
    app = create_app(dataclasses.replace(settings, tenancy="multi", superadmin_emails=[OPERATOR]))
    with TestClient(app) as c:
        yield app, c


def signup(c, email, school="", invite="", name="Test User"):
    body = {"email": email, "password": PW, "name": name}
    if school:
        body["school_name"] = school
    if invite:
        body["invite_code"] = invite
    r = c.post("/api/auth/register", json=body)
    return r


def ok(c, email, school="", invite=""):
    r = signup(c, email, school, invite)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}, r.json()["user"]


def wait_ready(c, pid, h):
    end = time.time() + 60
    while time.time() < end:
        p = c.get(f"/api/papers/{pid}", headers=h).json()
        if p["status"] in ("ready", "failed"):
            return p
        time.sleep(0.2)
    raise AssertionError("timeout")


def make_paper(c, h):
    r = c.post("/api/papers", headers=h, json={"subject": "Mathematics", "pattern_id": "unit-test-20", "seed": 2,
                                               "diagram_mode": "none", "syllabus_text": "Quadratic equations"})
    assert r.status_code == 202, r.text
    return wait_ready(c, r.json()["id"], h)


# ----------------------------------------------------------------- sign-up

def test_schools_register_separately_and_the_first_user_administers(multi):
    app, c = multi
    assert c.get("/api/auth/config").json() == {"tenancy": "multi", "allow_registration": True, "allow_school_signup": True,
                                             "allow_individual_signup": True}
    assert signup(c, "x@a.test").status_code == 422  # neither a school name nor an invitation
    _, a = ok(c, "a@a.test", "Sunrise Public School")
    _, b = ok(c, "b@b.test", "Hillview College")
    assert a["role"] == "admin" and b["role"] == "admin" and a["org_id"] != b["org_id"]
    assert a["organization"] == "Sunrise Public School"
    assert not a["is_superadmin"]


def test_closed_signup_still_admits_invitations_and_the_operator(settings):
    app = create_app(dataclasses.replace(settings, tenancy="multi", allow_school_signup=False, superadmin_emails=[OPERATOR]))
    with TestClient(app) as c:
        assert signup(c, "a@a.test", "Some School").status_code == 403
        h, op = ok(c, OPERATOR)  # the operator bootstraps their own workspace
        assert op["is_superadmin"] and op["role"] == "admin"
        made = c.post("/api/platform/orgs", headers=h, json={"name": "Invited School"}).json()
        _, adm = ok(c, "head@invited.test", invite=made["admin_invite_code"])
        assert adm["role"] == "admin" and adm["org_id"] == made["id"]


def test_invitations_are_single_use_expiring_email_bound_and_stored_hashed(multi):
    app, c = multi
    admin, a = ok(c, "a@a.test", "School A")
    teacher, _ = ok(c, "t@a.test", "School A2")  # a different school's admin, to test permissions below
    made = c.post("/api/admin/invites", headers=admin, json={"email": "new@a.test", "role": "teacher"}).json()
    code = made["code"]
    with app.state.ctx.db.session() as db:
        assert code not in db.query(Invite).one().code_hash
    assert signup(c, "other@a.test", invite=code).status_code == 422  # wrong address
    h, user = ok(c, "new@a.test", invite=code)
    assert user["role"] == "teacher" and user["org_id"] == a["org_id"]
    assert signup(c, "again@a.test", invite=code).status_code == 422  # already used
    assert c.get("/api/admin/invites", headers=admin).json()["invites"][0]["status"] == "used"
    assert c.post("/api/admin/invites", headers=h, json={}).status_code == 403  # teachers cannot invite

    open_invite = c.post("/api/admin/invites", headers=admin, json={"role": "admin"}).json()
    with app.state.ctx.db.session() as db:
        db.get(Invite, open_invite["id"]).expires_at = dt.datetime.utcnow() - dt.timedelta(days=1)
    assert signup(c, "late@a.test", invite=open_invite["code"]).status_code == 422  # expired
    fresh = c.post("/api/admin/invites", headers=admin, json={}).json()
    assert c.delete(f"/api/admin/invites/{fresh['id']}", headers=admin).status_code == 204
    assert signup(c, "revoked@a.test", invite=fresh["code"]).status_code == 422
    assert c.delete(f"/api/admin/invites/{fresh['id']}", headers=teacher).status_code == 404  # not their school's


# ----------------------------------------------------------------- isolation

def test_one_school_can_never_see_or_touch_another_schools_data(multi):
    app, c = multi
    a_admin, a = ok(c, "admin@a.test", "School A")
    inv = c.post("/api/admin/invites", headers=a_admin, json={}).json()["code"]
    a_teacher, _ = ok(c, "teach@a.test", invite=inv)
    b_admin, b = ok(c, "admin@b.test", "School B")

    paper = make_paper(c, a_teacher)
    assert paper["status"] == "ready"
    doc = c.post("/api/documents", headers=a_teacher, files=[("files", ("n.txt", b"Photosynthesis is how plants make food. " * 20, "text/plain"))]).json()["documents"][0]
    pid = paper["id"]

    # A's administrator sees the teacher's work; B's administrator sees nothing of it.
    assert [p["id"] for p in c.get("/api/papers", headers=a_admin).json()["papers"]] == [pid]
    assert c.get("/api/papers", headers=b_admin).json()["papers"] == []
    assert c.get("/api/documents", headers=b_admin).json()["documents"] == []
    assert c.get("/api/bank", headers=b_admin).json()["total"] == 0
    assert c.get("/api/bank", headers=a_admin).json()["total"] > 0
    for path in (f"/api/papers/{pid}", f"/api/papers/{pid}/export?format=pdf", f"/api/documents/{doc['id']}"):
        assert c.get(path, headers=b_admin).status_code == 404, path
    assert c.delete(f"/api/papers/{pid}", headers=b_admin).status_code == 404
    assert c.post(f"/api/papers/{pid}/questions/q1/regenerate", headers=b_admin, json={}).status_code == 404
    bad = c.post("/api/papers", headers=b_admin, json={"subject": "Biology", "pattern_id": "unit-test-20", "document_ids": [doc["id"]]})
    assert bad.status_code == 404  # another school's document cannot be used as a source

    users = c.get("/api/admin/users", headers=b_admin).json()["users"]
    assert [u["email"] for u in users] == ["admin@b.test"]
    teacher_id = next(u["id"] for u in c.get("/api/admin/users", headers=a_admin).json()["users"] if u["email"] == "teach@a.test")
    assert c.patch(f"/api/admin/users/{teacher_id}", headers=b_admin, json={"is_active": False}).status_code == 404
    events = c.get("/api/admin/audit", headers=b_admin).json()["events"]
    assert events and all(e["user_id"] != teacher_id for e in events)
    assert c.get("/api/admin/billing", headers=b_admin).json()["by_user"] == []
    assert "lifetime" not in c.get("/api/admin/usage", headers=b_admin).json()["provider"]


def test_school_wide_ai_key_and_cache_are_per_school(multi):
    app, c = multi
    a_admin, a = ok(c, "admin@a.test", "School A")
    b_admin, b = ok(c, "admin@b.test", "School B")
    put = c.put("/api/ai/settings", headers=a_admin, json={"scope": "workspace", "provider": "openai", "model": "m",
                                                          "api_key": "sk-test-1234567890abcd"})
    assert put.status_code == 200
    assert c.get("/api/ai/settings", headers=a_admin).json()["effective"]["source"] == "workspace"
    assert c.get("/api/ai/settings", headers=b_admin).json()["effective"]["source"] == "offline"
    assert c.get("/api/ai/settings", headers=b_admin).json()["workspace"] is None
    with app.state.ctx.db.session() as db:
        assert db.query(AiSetting).one().owner_id == a["org_id"]
    app.state.ctx.provider = Stub()
    ra, rb = resolve_ai(app.state.ctx, b["id"]), resolve_ai(app.state.ctx, a["id"])
    assert ra.source == "platform" and rb.source == "workspace"  # A has its own key; B uses the platform's
    other = ok(c, "second@b.test", invite=c.post("/api/admin/invites", headers=b_admin, json={}).json()["code"])[1]
    assert resolve_ai(app.state.ctx, other["id"]).provider.cache_scope != "org:" + a["org_id"]
    assert ra.provider.cache_scope == "org:" + b["org_id"]
    assert ra.provider.totals is app.state.ctx.provider.totals  # one meter for the platform, namespaced caches


# ----------------------------------------------------------------- operator

def test_suspending_a_school_locks_it_out_without_touching_others(multi):
    app, c = multi
    a_admin, a = ok(c, "admin@a.test", "School A")
    b_admin, _ = ok(c, "admin@b.test", "School B")
    op, _ = ok(c, OPERATOR)
    assert c.patch(f"/api/platform/orgs/{a['org_id']}", headers=op, json={"is_active": False}).status_code == 200
    assert c.get("/api/papers", headers=a_admin).status_code == 403
    assert c.post("/api/auth/login", json={"email": "admin@a.test", "password": PW}).status_code == 403
    assert c.get("/api/papers", headers=b_admin).status_code == 200
    assert c.patch(f"/api/platform/orgs/{a['org_id']}", headers=op, json={"is_active": True}).status_code == 200
    assert c.get("/api/papers", headers=a_admin).status_code == 200
    own = c.get("/api/auth/me", headers=op).json()["org_id"]
    assert c.patch(f"/api/platform/orgs/{own}", headers=op, json={"is_active": False}).status_code == 409


def test_operator_console_is_operator_only_and_never_exposes_school_content(multi):
    app, c = multi
    a_admin, a = ok(c, "admin@a.test", "School A")
    op, _ = ok(c, OPERATOR)
    assert c.get("/api/platform/orgs", headers=a_admin).status_code == 403
    paper = make_paper(c, a_admin)
    assert c.get(f"/api/papers/{paper['id']}", headers=op).status_code == 404
    assert c.get("/api/papers", headers=op).json()["papers"] == []
    listing = c.get("/api/platform/orgs", headers=op).json()
    assert {o["name"] for o in listing["orgs"]} == {"School A", "ExamForge Platform"}
    assert "papers" in listing["orgs"][0] and "content" not in str(listing)


def test_each_school_is_billed_at_its_own_negotiated_rates(settings):
    app = create_app(dataclasses.replace(settings, tenancy="multi", superadmin_emails=[OPERATOR],
                                         price_input_per_million=3.0, price_output_per_million=15.0,
                                         platform_fee_monthly=10.0))
    with TestClient(app) as c:
        a_admin, a = ok(c, "admin@a.test", "School A")
        b_admin, b = ok(c, "admin@b.test", "School B")
        op, _ = ok(c, OPERATOR)
        app.state.ctx.provider = Stub()
        r = c.patch(f"/api/platform/orgs/{a['org_id']}", headers=op,
                    json={"platform_fee_monthly": 50.0, "price_input_per_million": 100.0})
        assert r.status_code == 200
        meter = UsageMeter()
        meter.add(1000, 500)
        for who in (a, b):
            record_usage(app.state.ctx, who["id"], "paper", "p", meter, resolve_ai(app.state.ctx, who["id"]))
        sa = c.get("/api/admin/billing", headers=a_admin).json()["summary"]
        sb = c.get("/api/admin/billing", headers=b_admin).json()["summary"]
        assert sa["platform"]["charge"] == pytest.approx((1000 * 100.0 + 500 * 15.0) / 1e6)  # input override, default output
        assert sa["platform_fee"] == 50.0 and sb["platform_fee"] == 10.0
        assert sb["platform"]["charge"] == pytest.approx((1000 * 3.0 + 500 * 15.0) / 1e6)
        listing = {o["name"]: o for o in c.get("/api/platform/orgs", headers=op).json()["orgs"]}
        assert listing["School A"]["terms"]["custom"] is True and listing["School B"]["terms"]["custom"] is False
        assert listing["School A"]["month"]["total"] == pytest.approx(50.0 + sa["platform"]["charge"])
        # null sends a school back to the platform default
        c.patch(f"/api/platform/orgs/{a['org_id']}", headers=op, json={"platform_fee_monthly": None, "price_input_per_million": None})
        assert c.get("/api/admin/billing", headers=a_admin).json()["summary"]["platform_fee"] == 10.0


def test_operator_can_onboard_a_school_by_hand(multi):
    app, c = multi
    op, _ = ok(c, OPERATOR)
    made = c.post("/api/platform/orgs", headers=op, json={"name": "Greenfield High", "admin_email": "head@green.test"}).json()
    assert signup(c, "intruder@x.test", invite=made["admin_invite_code"]).status_code == 422  # bound to one address
    h, head = ok(c, "head@green.test", invite=made["admin_invite_code"])
    assert head["role"] == "admin" and head["organization"] == "Greenfield High"
    assert c.get("/api/admin/school", headers=h).json()["name"] == "Greenfield High"
    assert c.patch("/api/admin/school", headers=h, json={"name": "Greenfield Senior School"}).status_code == 200
    assert c.get("/api/auth/me", headers=h).json()["organization"] == "Greenfield Senior School"


# ----------------------------------------------------------------- upgrade path

def test_a_database_from_before_multi_school_is_adopted_into_one_school(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        state = app.state.ctx
        with state.db.session() as db:
            db.add_all([User(email="old-admin@x.test", name="Old", organization="Legacy School", password_hash="x", role="admin"),
                        User(email="old-teacher@x.test", name="T", password_hash="x", role="teacher")])
            db.flush()
            admin = db.query(User).filter(User.email == "old-admin@x.test").one()
            db.add(UsageLog(user_id=admin.id, kind="paper", prompt_tokens=5))
            db.add(AuditLog(user_id=admin.id, action="x"))
            db.add(AiSetting(scope="workspace", owner_id="", provider="openai", model="m", key_cipher="c"))
        org_id = ensure_default_org(state)
        assert org_id
        with state.db.session() as db:
            assert {u.org_id for u in db.query(User).filter(User.email.like("old-%")).all()} == {org_id}
            assert db.get(Organization, org_id).name == "Legacy School"
            assert db.query(UsageLog).one().org_id == org_id and db.query(AuditLog).one().org_id == org_id
            assert db.query(AiSetting).one().owner_id == org_id
        assert ensure_default_org(state) is None  # running it again changes nothing


# ----------------------------------------------------------------- individual teachers

def solo(c, email, name="Tutor"):
    return c.post("/api/auth/register", json={"email": email, "password": PW, "name": name, "individual": True})


def test_individual_teacher_gets_a_private_workspace_of_one(multi):
    app, c = multi
    r = solo(c, "tutor@home.test", "Meera")
    assert r.status_code == 200, r.text
    me = r.json()["user"]
    assert me["role"] == "admin" and me["organization"] == "Meera (individual)"
    h = {"Authorization": "Bearer " + r.json()["token"]}
    other, _ = ok(c, "x@other.test", "Other School")
    mine = make_paper(c, h)
    assert c.get(f"/api/papers/{mine['id']}", headers=other).status_code == 404
    assert c.get("/api/papers", headers=other).json()["papers"] == []
    with app.state.ctx.db.session() as db:
        assert db.get(Organization, me["org_id"]).kind == "individual"


def test_two_individuals_never_share_even_with_the_same_name(multi):
    app, c = multi
    a = solo(c, "one@home.test", "Sam").json()
    b = solo(c, "two@home.test", "Sam").json()
    assert a["user"]["org_id"] != b["user"]["org_id"]


def test_individual_signup_can_be_closed_without_closing_schools(settings):
    app = create_app(dataclasses.replace(settings, tenancy="multi", allow_individual_signup=False,
                                         superadmin_emails=[OPERATOR]))
    with TestClient(app) as c:
        assert c.get("/api/auth/config").json()["allow_individual_signup"] is False
        assert solo(c, "tutor@home.test").status_code == 403
        assert signup(c, "a@a.test", "A School").status_code == 200


def test_operator_console_labels_individual_workspaces(multi):
    app, c = multi
    solo(c, "tutor@home.test", "Meera")
    ok(c, "a@a.test", "A School")
    op, _ = ok(c, OPERATOR)
    kinds = {o["name"]: o["kind"] for o in c.get("/api/platform/orgs", headers=op).json()["orgs"]}
    assert kinds["Meera (individual)"] == "individual" and kinds["A School"] == "school"
