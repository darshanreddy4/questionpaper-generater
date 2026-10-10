"""What each role in a school can see and change, and that nothing crosses from one school to another."""
import dataclasses
import io
import os
import time

import pytest
from fastapi.testclient import TestClient

from app.exam.patterns import get_preset
from app.main import create_app
from app.models import Document

from conftest import BIOLOGY_TEXT, register

PW = "correct-horse-battery"


def _join(c, email, name, *, school="", invite=""):
    body = {"email": email, "password": PW, "name": name}
    if school:
        body["school_name"] = school
    if invite:
        body["invite_code"] = invite
    r = c.post("/api/auth/register", json=body)
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}


@pytest.fixture()
def world(settings):
    """School A: an administrator and two teachers. School B: an administrator and one teacher."""
    app = create_app(dataclasses.replace(settings, tenancy="multi"))
    with TestClient(app) as c:
        w = {"app": app, "c": c}
        w["admin_a"] = _join(c, "admin@a.test", "Asha Admin", school="School A")
        w["admin_b"] = _join(c, "admin@b.test", "Bala Admin", school="School B")
        for key, name, admin, email in (("t_a1", "Anita Teacher", "admin_a", "anita@a.test"),
                                        ("t_a2", "Arun Teacher", "admin_a", "arun@a.test"),
                                        ("t_b1", "Bina Teacher", "admin_b", "bina@b.test")):
            code = c.post("/api/admin/invites", headers=w[admin], json={"email": email, "role": "teacher"}).json()["code"]
            w[key] = _join(c, email, name, invite=code)
        yield w


A = ("admin_a", "t_a1", "t_a2")  # everyone in school A
B = ("admin_b", "t_b1")


def _upload(c, headers, name="notes.txt", text=BIOLOGY_TEXT):
    r = c.post("/api/documents", headers=headers, files=[("files", (name, text.encode(), "text/plain"))])
    assert r.status_code == 202, r.text
    doc = r.json()["documents"][0]
    end = time.time() + 30
    while time.time() < end:
        d = c.get(f"/api/documents/{doc['id']}", headers=headers).json()
        if d["status"] in ("ready", "failed"):
            assert d["status"] == "ready", d
            return d
        time.sleep(0.2)
    raise AssertionError("document not ready")


PAPER = {"subject": "Mathematics", "pattern_id": "unit-test-20", "seed": 3, "diagram_mode": "none",
         "syllabus_text": "Quadratic equations"}


def _paper(c, headers, **extra):
    r = c.post("/api/papers", headers=headers, json=dict(PAPER, **extra))
    assert r.status_code == 202, r.text
    end = time.time() + 60
    while time.time() < end:
        p = c.get(f"/api/papers/{r.json()['id']}", headers=headers).json()
        if p["status"] in ("ready", "failed"):
            assert p["status"] == "ready", p["message"]
            return p
        time.sleep(0.2)
    raise AssertionError("paper not ready")


def _ids(rows):
    return {r["id"] for r in rows}


# ----------------------------------------------------------------------------- source files

def test_sources_added_by_anyone_in_a_school_are_seen_by_that_whole_school_and_no_other(world):
    c = world["c"]
    by_teacher = _upload(c, world["t_a1"], "teacher_notes.txt")
    by_admin = _upload(c, world["admin_a"], "admin_notes.txt")
    both = {by_teacher["id"], by_admin["id"]}
    for who in A:
        h = world[who]
        assert _ids(c.get("/api/documents", headers=h).json()["documents"]) == both, who
        for doc_id in both:
            assert c.get(f"/api/documents/{doc_id}", headers=h).status_code == 200
            assert c.get(f"/api/documents/{doc_id}/figures", headers=h).status_code == 200
    for who in B:
        h = world[who]
        assert c.get("/api/documents", headers=h).json()["documents"] == [], who
        for doc_id in both:
            assert c.get(f"/api/documents/{doc_id}", headers=h).status_code == 404
            assert c.get(f"/api/documents/{doc_id}/figures", headers=h).status_code == 404
            assert c.get(f"/api/documents/{doc_id}/figures/x/image", headers=h).status_code == 404
    seen = {d["filename"]: d for d in c.get("/api/documents", headers=world["t_a2"]).json()["documents"]}
    assert seen["teacher_notes.txt"]["uploaded_by"] == "Anita Teacher" and seen["admin_notes.txt"]["uploaded_by"] == "Asha Admin"
    assert not seen["teacher_notes.txt"]["can_delete"] and not seen["admin_notes.txt"]["can_delete"]
    mine = {d["filename"]: d for d in c.get("/api/documents", headers=world["admin_a"]).json()["documents"]}
    assert mine["teacher_notes.txt"]["can_delete"] and mine["admin_notes.txt"]["can_delete"]


def test_only_the_school_administrator_can_delete_source_files(world):
    c, app = world["c"], world["app"]
    by_teacher = _upload(c, world["t_a1"], "teacher_notes.txt")
    by_admin = _upload(c, world["admin_a"], "admin_notes.txt")
    # teachers cannot delete: not the colleague's, not the administrator's, not even their own upload
    for who, doc in (("t_a2", by_teacher), ("t_a2", by_admin), ("t_a1", by_teacher), ("t_a1", by_admin)):
        r = c.delete(f"/api/documents/{doc['id']}", headers=world[who])
        assert r.status_code == 403, (who, r.text)
    # another school's administrator and teachers cannot even tell the file exists
    for who in B:
        assert c.delete(f"/api/documents/{by_teacher['id']}", headers=world[who]).status_code == 404
    for who in A:
        assert _ids(c.get("/api/documents", headers=world[who]).json()["documents"]) == {by_teacher["id"], by_admin["id"]}

    with app.state.ctx.db.session() as db:
        folder = os.path.dirname(db.get(Document, by_teacher["id"]).stored_path)
    assert os.path.isdir(folder)
    assert c.delete(f"/api/documents/{by_teacher['id']}", headers=world["admin_a"]).status_code == 204
    assert not os.path.exists(folder)  # the stored file went with it
    for who in A:  # gone for the administrator and both teachers
        h = world[who]
        assert _ids(c.get("/api/documents", headers=h).json()["documents"]) == {by_admin["id"]}, who
        assert c.get(f"/api/documents/{by_teacher['id']}", headers=h).status_code == 404
    assert c.delete(f"/api/documents/{by_teacher['id']}", headers=world["admin_a"]).status_code == 404
    # the other school's administrator cannot delete the remaining one either
    assert c.delete(f"/api/documents/{by_admin['id']}", headers=world["admin_b"]).status_code == 404
    assert c.get(f"/api/documents/{by_admin['id']}", headers=world["t_a1"]).status_code == 200
    # the deletion is in school A's audit log and nowhere in school B's
    a_log = [e["action"] for e in c.get("/api/admin/audit", headers=world["admin_a"]).json()["events"]]
    b_log = [e["action"] for e in c.get("/api/admin/audit", headers=world["admin_b"]).json()["events"]]
    assert "document.delete" in a_log and "document.delete" not in b_log and "document.upload" not in b_log


def test_a_school_can_only_use_its_own_sources_to_write_a_paper(world):
    c = world["c"]
    doc = _upload(c, world["t_a1"])
    body = dict(PAPER, subject="Biology", document_ids=[doc["id"]])
    for who in ("admin_a", "t_a1", "t_a2"):
        assert c.post("/api/papers/blueprint", headers=world[who], json=body).status_code == 200, who
    for who in B:
        assert c.post("/api/papers/blueprint", headers=world[who], json=body).status_code == 404
        assert c.post("/api/papers", headers=world[who], json=body).status_code == 404


# ----------------------------------------------------------------------------- question papers

def test_papers_are_seen_across_the_school_but_changed_only_by_their_creator_or_the_administrator(world):
    c = world["c"]
    paper = _paper(c, world["t_a1"])
    pid = paper["id"]
    for who in A:
        h = world[who]
        assert pid in _ids(c.get("/api/papers", headers=h).json()["papers"]), who
        got = c.get(f"/api/papers/{pid}", headers=h)
        assert got.status_code == 200 and got.json()["created_by"] == "Anita Teacher"
        assert c.get(f"/api/papers/{pid}/export", headers=h, params={"format": "pdf"}).status_code == 200
        assert c.get(f"/api/papers/{pid}/export-all", headers=h).status_code == 200
    for who in B:
        h = world[who]
        assert c.get("/api/papers", headers=h).json()["papers"] == []
        for path in (f"/api/papers/{pid}", f"/api/papers/{pid}/export", f"/api/papers/{pid}/export-all"):
            assert c.get(path, headers=h).status_code == 404, (who, path)
        assert c.put(f"/api/papers/{pid}", headers=h, json=paper["content"]).status_code == 404
        assert c.delete(f"/api/papers/{pid}", headers=h).status_code == 404
        assert c.post(f"/api/papers/{pid}/questions/q1/regenerate", headers=h, json={}).status_code == 404

    # a colleague can read it but not change it
    colleague = world["t_a2"]
    assert c.get(f"/api/papers/{pid}", headers=colleague).json()["can_edit"] is False
    assert c.put(f"/api/papers/{pid}", headers=colleague, json=paper["content"]).status_code == 403
    assert c.delete(f"/api/papers/{pid}", headers=colleague).status_code == 403
    assert c.post(f"/api/papers/{pid}/questions/q1/regenerate", headers=colleague, json={}).status_code == 403
    assert c.get(f"/api/papers/{pid}", headers=world["t_a1"]).json()["revision"] == paper["revision"]  # untouched

    # the creator and the administrator can
    content = paper["content"]
    content["sections"][0]["questions"][0]["text"] = "Edited by the creator about quadratic equations $x^2=4$?"
    assert c.put(f"/api/papers/{pid}", headers=world["t_a1"], json=content).status_code == 200
    content["sections"][0]["questions"][0]["text"] = "Edited by the administrator about quadratic equations $x^2=9$?"
    assert c.put(f"/api/papers/{pid}", headers=world["admin_a"], json=content).status_code == 200
    assert c.get(f"/api/papers/{pid}", headers=colleague).json()["revision"] == paper["revision"] + 2
    assert c.get(f"/api/papers/{pid}", headers=world["admin_a"]).json()["can_edit"] is True


def test_what_the_administrator_makes_is_seen_by_the_teachers_and_removed_by_the_administrator_for_everyone(world):
    c = world["c"]
    mine = _paper(c, world["admin_a"])
    theirs = _paper(c, world["t_a2"], seed=9)
    for who in A:
        assert _ids(c.get("/api/papers", headers=world[who]).json()["papers"]) == {mine["id"], theirs["id"]}, who
    assert c.delete(f"/api/papers/{mine['id']}", headers=world["t_a1"]).status_code == 403  # teachers cannot remove the administrator's
    assert c.delete(f"/api/papers/{theirs['id']}", headers=world["t_a2"]).status_code == 204  # the creator can
    assert c.delete(f"/api/papers/{mine['id']}", headers=world["admin_a"]).status_code == 204
    for who in A:
        assert c.get("/api/papers", headers=world[who]).json()["papers"] == []
        assert c.get(f"/api/papers/{theirs['id']}", headers=world[who]).status_code == 404


def test_the_administrator_can_remove_a_teachers_paper_and_it_disappears_for_the_whole_school(world):
    c = world["c"]
    paper = _paper(c, world["t_a1"])
    assert c.delete(f"/api/papers/{paper['id']}", headers=world["admin_a"]).status_code == 204
    for who in A:
        assert c.get(f"/api/papers/{paper['id']}", headers=world[who]).status_code == 404


# ----------------------------------------------------------------------------- question bank and patterns

def test_the_question_bank_is_the_schools_and_reuse_never_crosses_schools(world):
    c = world["c"]
    _paper(c, world["t_a1"])
    request = dict(PAPER, reuse_bank=True)
    for who in A:
        bank = c.get("/api/bank", headers=world[who]).json()
        assert bank["total"] > 0 and {i["created_by"] for i in bank["items"]} == {"Anita Teacher"}, who
    for who in B:
        assert c.get("/api/bank", headers=world[who]).json()["total"] == 0
    # a colleague's new paper reuses what the first teacher wrote; another school's does not
    assert c.post("/api/papers/blueprint", headers=world["t_a2"], json=request).json()["writers"]["bank"] > 0
    assert c.post("/api/papers/blueprint", headers=world["admin_a"], json=request).json()["writers"]["bank"] > 0
    assert c.post("/api/papers/blueprint", headers=world["t_b1"], json=request).json()["writers"]["bank"] == 0
    # only the school administrator removes a bank question: not a colleague, not even the teacher whose paper it came from
    item = c.get("/api/bank", headers=world["t_a2"]).json()["items"][0]["id"]
    assert c.delete(f"/api/bank/{item}", headers=world["t_a2"]).status_code == 403
    assert c.delete(f"/api/bank/{item}", headers=world["t_a1"]).status_code == 403
    assert not any(i["can_delete"] for i in c.get("/api/bank", headers=world["t_a1"]).json()["items"])
    assert all(i["can_delete"] for i in c.get("/api/bank", headers=world["admin_a"]).json()["items"])
    assert c.delete(f"/api/bank/{item}", headers=world["admin_b"]).status_code == 404
    assert c.delete(f"/api/bank/{item}", headers=world["admin_a"]).status_code == 204
    for who in A:
        assert item not in _ids(c.get("/api/bank", headers=world[who]).json()["items"])


def test_custom_exam_patterns_are_the_schools_to_use_and_the_creators_to_remove(world):
    c = world["c"]
    spec = get_preset("unit-test-20").model_dump()
    spec.update(id="", name="School A house pattern")
    made = c.post("/api/patterns", headers=world["t_a1"], json=spec)
    assert made.status_code == 201, made.text
    pid = made.json()["id"]

    def custom(h):
        return [p for p in c.get("/api/meta", headers=h).json()["patterns"] if p.get("custom")]

    for who in A:
        assert [p["id"] for p in custom(world[who])] == [pid], who
        assert c.post("/api/papers/blueprint", headers=world[who], json=dict(PAPER, pattern_id=pid)).status_code == 200
    for who in B:
        assert custom(world[who]) == []
        assert c.post("/api/papers/blueprint", headers=world[who], json=dict(PAPER, pattern_id=pid)).status_code == 404
    assert c.delete(f"/api/patterns/{pid}", headers=world["t_a2"]).status_code == 403
    assert c.delete(f"/api/patterns/{pid}", headers=world["admin_b"]).status_code == 404
    assert c.delete(f"/api/patterns/{pid}", headers=world["admin_a"]).status_code == 204


# ----------------------------------------------------------------------------- activity, people and money

def test_activity_people_and_usage_are_private_to_each_school_and_admin_only(world):
    c = world["c"]
    _upload(c, world["t_a1"])
    _paper(c, world["t_a2"])
    users_a = {u["email"] for u in c.get("/api/admin/users", headers=world["admin_a"]).json()["users"]}
    assert users_a == {"admin@a.test", "anita@a.test", "arun@a.test"}
    users_b = {u["email"] for u in c.get("/api/admin/users", headers=world["admin_b"]).json()["users"]}
    assert users_b == {"admin@b.test", "bina@b.test"}
    actors_a = {e["user_id"] for e in c.get("/api/admin/audit", headers=world["admin_a"]).json()["events"]}
    actors_b = {e["user_id"] for e in c.get("/api/admin/audit", headers=world["admin_b"]).json()["events"]}
    assert len(actors_a) >= 3 and not actors_a & actors_b
    for who in ("t_a1", "t_a2", "t_b1"):  # teachers have no administration at all
        h = world[who]
        for path in ("/api/admin/users", "/api/admin/audit", "/api/admin/invites", "/api/admin/usage", "/api/admin/billing",
                     "/api/admin/school"):
            assert c.get(path, headers=h).status_code == 403, (who, path)
        assert c.post("/api/admin/invites", headers=h, json={}).status_code == 403
        assert c.get("/api/platform/orgs", headers=h).status_code == 403
    for who in ("admin_a", "admin_b"):
        assert c.get("/api/platform/orgs", headers=world[who]).status_code == 403  # customers cannot onboard schools
        assert c.post("/api/platform/orgs", headers=world[who], json={"name": "Rival School"}).status_code == 403


def test_a_disabled_teacher_loses_access_and_new_teachers_see_the_existing_library(world):
    c = world["c"]
    doc = _upload(c, world["admin_a"])
    paper = _paper(c, world["t_a1"])
    teacher_id = next(u["id"] for u in c.get("/api/admin/users", headers=world["admin_a"]).json()["users"]
                      if u["email"] == "arun@a.test")
    assert c.patch(f"/api/admin/users/{teacher_id}", headers=world["admin_a"], json={"is_active": False}).status_code == 200
    assert c.get("/api/documents", headers=world["t_a2"]).status_code in (401, 403)
    code = c.post("/api/admin/invites", headers=world["admin_a"], json={"email": "new@a.test"}).json()["code"]
    newcomer = _join(c, "new@a.test", "New Teacher", invite=code)
    assert doc["id"] in _ids(c.get("/api/documents", headers=newcomer).json()["documents"])
    assert paper["id"] in _ids(c.get("/api/papers", headers=newcomer).json()["papers"])


# ----------------------------------------------------------------------------- single-school installs are unchanged

def test_a_single_school_install_keeps_each_teachers_work_private(client):
    first = register(client, "head@x.test")
    second = register(client, "teach@x.test")
    mine = _upload(client, first)
    assert client.get("/api/documents", headers=second).json()["documents"] == []
    assert client.get(f"/api/documents/{mine['id']}", headers=second).status_code == 404
    own = _upload(client, second, "own.txt")
    assert client.delete(f"/api/documents/{own['id']}", headers=second).status_code == 204  # owners still delete their own here
    assert client.get(f"/api/documents/{mine['id']}", headers=first).status_code == 200
