"""Platform library: the operator publishes files and questions; schools copy what they want into their own library."""
import dataclasses
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import Chunk, Document

from conftest import BIOLOGY_TEXT
from test_school_sharing import PAPER, _join, _paper, _upload

OPERATOR = "operator@platform.test"


@pytest.fixture()
def world(settings):
    app = create_app(dataclasses.replace(settings, tenancy="multi", superadmin_emails=[OPERATOR]))
    with TestClient(app) as c:
        w = {"app": app, "c": c, "op": _join(c, OPERATOR, "Operator")}
        w["admin_a"] = _join(c, "admin@a.test", "Asha Admin", school="School A")
        code = c.post("/api/admin/invites", headers=w["admin_a"], json={"email": "t@a.test", "role": "teacher"}).json()["code"]
        w["t_a"] = _join(c, "t@a.test", "Anita Teacher", invite=code)
        w["admin_b"] = _join(c, "admin@b.test", "Bala Admin", school="School B")
        yield w


def _publish(c, h, kind, ids, published=True):
    return c.post("/api/catalog/publish", headers=h, json={"kind": kind, "ids": ids, "published": published})


def test_only_the_operator_can_publish_and_only_published_items_are_listed(world):
    c = world["c"]
    doc = _upload(c, world["op"], "chapter.txt")
    assert c.get("/api/catalog/documents", headers=world["admin_a"]).json()["documents"] == []
    assert _publish(c, world["admin_a"], "document", [doc["id"]]).status_code == 403
    assert _publish(c, world["op"], "document", [doc["id"]]).status_code == 200
    listed = c.get("/api/catalog/documents", headers=world["admin_a"]).json()["documents"]
    assert [d["filename"] for d in listed] == ["chapter.txt"] and listed[0]["added"] is False
    assert c.get(f"/api/documents/{doc['id']}", headers=world["admin_a"]).status_code == 404  # listing is not access


def test_a_school_cannot_publish_another_schools_file(world):
    c = world["c"]
    mine = _upload(c, world["admin_a"], "private.txt")
    assert _publish(c, world["op"], "document", [mine["id"]]).status_code == 404


def test_adding_a_file_makes_an_independent_copy_for_the_whole_school(world):
    c, app = world["c"], world["app"]
    doc = _upload(c, world["op"], "chapter.txt")
    _publish(c, world["op"], "document", [doc["id"]])
    r = c.post(f"/api/catalog/documents/{doc['id']}/add", headers=world["t_a"])
    assert r.status_code == 201, r.text
    copy_id = r.json()["id"]
    assert copy_id != doc["id"]
    # the whole school sees it; the other school does not
    assert c.get(f"/api/documents/{copy_id}", headers=world["admin_a"]).status_code == 200
    assert c.get(f"/api/documents/{copy_id}", headers=world["admin_b"]).status_code == 404
    assert c.post(f"/api/catalog/documents/{doc['id']}/add", headers=world["admin_a"]).status_code == 409
    assert c.get("/api/catalog/documents", headers=world["admin_a"]).json()["documents"][0]["added"] is True
    assert c.get("/api/catalog/documents", headers=world["admin_b"]).json()["documents"][0]["added"] is False
    # copies are used like any upload, and withdrawing or deleting the original leaves them alone
    _publish(c, world["op"], "document", [doc["id"]], False)
    c.delete(f"/api/documents/{doc['id']}", headers=world["op"])
    assert c.get(f"/api/documents/{copy_id}", headers=world["admin_a"]).json()["status"] == "ready"
    with app.state.ctx.db.session() as db:
        copy = db.get(Document, copy_id)
        assert Path(copy.stored_path).is_file() and db.query(Chunk).filter(Chunk.document_id == copy_id).count() > 0
    p = _paper(c, world["t_a"], document_ids=[copy_id], syllabus_text="")
    assert p["status"] == "ready"
    # only the school's administrator removes it, as with any source file
    assert c.delete(f"/api/documents/{copy_id}", headers=world["t_a"]).status_code == 403
    assert c.delete(f"/api/documents/{copy_id}", headers=world["admin_a"]).status_code == 204


def test_an_unpublished_file_can_no_longer_be_added(world):
    c = world["c"]
    doc = _upload(c, world["op"], "chapter.txt")
    _publish(c, world["op"], "document", [doc["id"]])
    _publish(c, world["op"], "document", [doc["id"]], False)
    assert c.post(f"/api/catalog/documents/{doc['id']}/add", headers=world["admin_a"]).status_code == 404


def _operator_bank(world):
    c = world["c"]
    _paper(c, world["op"])
    items = c.get("/api/bank?limit=200", headers=world["op"]).json()["items"]
    assert items
    return items


def test_questions_are_published_filtered_and_added_without_duplicates(world):
    c = world["c"]
    items = _operator_bank(world)
    ids = [i["id"] for i in items]
    assert c.get("/api/catalog/questions", headers=world["admin_a"]).json()["total"] == 0
    assert _publish(c, world["op"], "question", ids).status_code == 200
    listing = c.get("/api/catalog/questions?limit=200", headers=world["admin_a"]).json()
    assert listing["total"] == len(ids) and listing["subjects"][0]["subject"] == "Mathematics"
    assert "text_blob" not in listing["items"][0]["question"]
    one = c.post("/api/catalog/questions/add", headers=world["t_a"], json={"ids": [ids[0]]}).json()
    assert one == {"added": 1, "skipped": 0}
    bulk = c.post("/api/catalog/questions/add", headers=world["t_a"], json={"subject": "Mathematics"}).json()
    assert bulk["added"] == len(ids) - 1 and bulk["skipped"] == 1
    again = c.post("/api/catalog/questions/add", headers=world["admin_a"], json={"subject": "Mathematics"}).json()
    assert again["added"] == 0  # the school already has them all
    mine = c.get("/api/bank?limit=200", headers=world["admin_a"]).json()
    assert mine["total"] == len(ids)  # shared school-wide, so the admin sees what the teacher added
    assert c.get("/api/bank", headers=world["admin_b"]).json()["total"] == 0
    assert c.post("/api/catalog/questions/add", headers=world["t_a"], json={}).status_code == 422


def test_unpublishing_stops_new_additions_but_keeps_copies(world):
    c = world["c"]
    ids = [i["id"] for i in _operator_bank(world)]
    _publish(c, world["op"], "question", ids)
    c.post("/api/catalog/questions/add", headers=world["admin_a"], json={"subject": "Mathematics"})
    _publish(c, world["op"], "question", ids, False)
    assert c.get("/api/catalog/questions", headers=world["admin_b"]).json()["total"] == 0
    assert c.post("/api/catalog/questions/add", headers=world["admin_b"], json={"ids": ids}).json()["added"] == 0
    assert c.get("/api/bank", headers=world["admin_a"]).json()["total"] == len(ids)


def test_catalog_is_off_on_a_single_school_install(settings):
    app = create_app(dataclasses.replace(settings, tenancy="single"))
    with TestClient(app) as c:
        h = {"Authorization": "Bearer " + c.post("/api/auth/register", json={"email": "a@a.test", "password": "correct-horse-battery", "name": "A"}).json()["token"]}
        assert c.get("/api/catalog/documents", headers=h).status_code == 404
        assert c.get("/api/meta", headers=h).json()["tenancy"] == "single"
