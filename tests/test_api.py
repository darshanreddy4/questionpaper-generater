import io
import time
import zipfile

import pytest

from conftest import BIOLOGY_TEXT, register


def _wait(client, url, headers, done=("ready", "failed"), timeout=60):
    end = time.time() + timeout
    while time.time() < end:
        data = client.get(url, headers=headers).json()
        if data["status"] in done:
            return data
        time.sleep(0.2)
    raise AssertionError("timed out waiting for " + url)


def _upload(client, headers, name="biology.txt", content=BIOLOGY_TEXT.encode()):
    return client.post("/api/documents", headers=headers, files=[("files", (name, content, "text/plain"))])


def test_auth_required_and_registration_flow(client):
    assert client.get("/api/papers").status_code == 401
    assert client.get("/api/meta", headers={"Authorization": "Bearer garbage"}).status_code == 401
    short = client.post("/api/auth/register", json={"email": "a@b.co", "password": "short", "name": "x"})
    assert short.status_code == 422
    headers = register(client)
    assert client.get("/api/auth/me", headers=headers).json()["role"] == "admin"  # first user administers
    dup = client.post("/api/auth/register", json={"email": "TEACHER@example.com", "password": "correct-horse-battery", "name": "x"})
    assert dup.status_code == 409
    bad = client.post("/api/auth/login", json={"email": "teacher@example.com", "password": "wrong-password-1"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login", json={"email": "teacher@example.com", "password": "correct-horse-battery"})
    assert ok.status_code == 200 and ok.json()["token"]


def test_login_is_rate_limited(client):
    register(client)
    codes = [client.post("/api/auth/login", json={"email": "teacher@example.com", "password": "nope-nope-nope"}).status_code
             for _ in range(10)]
    assert 429 in codes


def test_meta_reports_offline_engine_and_patterns(client):
    meta = client.get("/api/meta", headers=register(client)).json()
    assert meta["engine"]["offline"] is True
    assert any(p["id"] == "cbse-10-maths" for p in meta["patterns"])


def test_upload_validation(client):
    h = register(client)
    assert _upload(client, h, "malware.exe", b"MZ").status_code == 415
    assert _upload(client, h, "empty.txt", b"").status_code == 422
    r = _upload(client, h, "../../../etc/passwd.txt", b"Some text that is long enough to be a document.")
    assert r.status_code == 202
    assert r.json()["documents"][0]["filename"] == "passwd.txt"  # path components never survive


def test_full_flow_document_to_paper_to_downloads(client):
    h = register(client)
    doc = _upload(client, h).json()["documents"][0]
    detail = _wait(client, f"/api/documents/{doc['id']}", h, done=("ready", "failed"))
    assert detail["status"] == "ready", detail
    assert [c["title"] for c in detail["chapters"]][:3] == ["Chapter 1: Photosynthesis", "Chapter 2: Respiration",
                                                            "Chapter 3: Transport in Plants"]

    body = {"subject": "Biology", "class_level": "9", "board": "State Board", "institution": "Test School",
            "pattern_id": "unit-test-20", "document_ids": [doc["id"]], "seed": 5, "num_sets": 2,
            "difficulty": {"easy": 40, "medium": 40, "hard": 20}}
    preview = client.post("/api/papers/blueprint", headers=h, json=body).json()
    assert preview["total_marks"] == 20 and len(preview["slots"]) == 11
    assert {t["name"] for t in preview["topics"]} >= {"Chapter 1: Photosynthesis", "Chapter 2: Respiration"}

    created = client.post("/api/papers", headers=h, json=body)
    assert created.status_code == 202
    paper_id = created.json()["id"]
    paper = _wait(client, f"/api/papers/{paper_id}", h)
    assert paper["status"] == "ready", paper["message"]
    sections = paper["content"]["sections"]
    assert [len(s["questions"]) for s in sections] == [5, 3, 3]
    assert paper["quality"]["failed"] == [] and paper["quality"]["engine"]["offline"] is True
    assert all(q["answer"]["text"] or q["answer"]["steps"] for s in sections for q in s["questions"])

    for fmt, magic in (("pdf", b"%PDF"), ("docx", b"PK")):
        for mode in ("question", "answer", "combined"):
            r = client.get(f"/api/papers/{paper_id}/export", headers=h, params={"format": fmt, "mode": mode})
            assert r.status_code == 200 and r.content.startswith(magic), (fmt, mode, r.text[:200])
            assert "attachment" in r.headers["content-disposition"]
    assert client.get(f"/api/papers/{paper_id}/export", headers=h, params={"mode": "bogus"}).status_code == 422

    z = client.get(f"/api/papers/{paper_id}/export-all", headers=h, params={"sets": 2})
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert len(names) == 8 and any("setB" in n for n in names)

    # edit + regenerate one question
    content = paper["content"]
    content["sections"][0]["questions"][0]["text"] = "Edited question text about photosynthesis in plants?"
    saved = client.put(f"/api/papers/{paper_id}", headers=h, json=content)
    assert saved.status_code == 200 and saved.json()["revision"] == 2
    qid = content["sections"][1]["questions"][0]["id"]
    regen = client.post(f"/api/papers/{paper_id}/questions/{qid}/regenerate", headers=h, json={})
    assert regen.status_code == 200 and regen.json()["question"]["id"] == qid

    # the bank remembers questions, and a second paper avoids repeating them
    bank = client.get("/api/bank", headers=h).json()
    assert bank["total"] >= 11
    second = client.post("/api/papers", headers=h, json=dict(body, seed=9)).json()["id"]
    assert _wait(client, f"/api/papers/{second}", h)["status"] == "ready"


def test_tenant_isolation(client):
    a = register(client, "a@example.com")
    b = register(client, "b@example.com")
    assert client.get("/api/auth/me", headers=b).json()["role"] == "teacher"
    doc_id = _upload(client, a).json()["documents"][0]["id"]
    _wait(client, f"/api/documents/{doc_id}", a)
    pid = client.post("/api/papers", headers=a, json={"subject": "Mathematics", "pattern_id": "unit-test-20"}).json()["id"]
    _wait(client, f"/api/papers/{pid}", a)
    for method, url in (("get", f"/api/documents/{doc_id}"), ("delete", f"/api/documents/{doc_id}"),
                        ("get", f"/api/papers/{pid}"), ("get", f"/api/papers/{pid}/export"),
                        ("delete", f"/api/papers/{pid}")):
        assert getattr(client, method)(url, headers=b).status_code == 404, url
    use_foreign = client.post("/api/papers", headers=b, json={"subject": "Biology", "pattern_id": "unit-test-20",
                                                              "document_ids": [doc_id]})
    assert use_foreign.status_code == 404
    assert client.get("/api/admin/users", headers=b).status_code == 403
    assert client.get("/api/admin/users", headers=a).status_code == 200
    assert client.get("/api/papers", headers=b).json()["papers"] == []


def test_offline_engine_requires_material_for_non_math(client):
    h = register(client)
    r = client.post("/api/papers", headers=h, json={"subject": "History", "pattern_id": "unit-test-20"})
    assert r.status_code == 422 and "Without AI" in r.json()["detail"]


def test_math_paper_with_diagrams_and_custom_pattern(client):
    h = register(client)
    pattern = {"name": "Quick quiz", "duration_minutes": 30, "sections": [
        {"id": "A", "title": "Objective", "question_type": "mcq", "count": 4, "marks_each": 1},
        {"id": "B", "title": "Problems", "question_type": "numerical", "count": 3, "marks_each": 3, "internal_choice": 1}]}
    saved = client.post("/api/patterns", headers=h, json=pattern)
    assert saved.status_code == 201
    pid = client.post("/api/papers", headers=h, json={
        "subject": "Mathematics", "pattern_id": saved.json()["id"], "diagram_mode": "more", "seed": 2,
        "syllabus_text": "Unit 1: Geometry\n- Triangles\n- Circles\nUnit 2: Algebra\n- Linear equations"}).json()["id"]
    paper = _wait(client, f"/api/papers/{pid}", h)
    assert paper["status"] == "ready" and paper["quality"]["diagrams"]["delivered"] >= 1
    diagram = next(q["diagram"] for s in paper["content"]["sections"] for q in s["questions"] if q["diagram"])
    svg = client.post("/api/diagrams/render", headers=h, json={"spec": diagram})
    assert svg.status_code == 200 and b"<svg" in svg.content
    assert client.post("/api/diagrams/render", headers=h, json={"spec": {"type": "x"}}).status_code == 422
    assert paper["content"]["meta"]["total_marks"] == 13


def test_security_headers(client):
    r = client.get("/api/health")
    assert r.headers["x-content-type-options"] == "nosniff" and "default-src 'self'" in r.headers["content-security-policy"]
