from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import load_settings  # noqa: E402
from app.main import create_app  # noqa: E402

BIOLOGY_TEXT = """\
# Chapter 1: Photosynthesis

Photosynthesis is the process by which green plants prepare their own food using sunlight, water and carbon dioxide.
Chlorophyll is the green pigment present in the chloroplasts of leaves that absorbs light energy for photosynthesis.
Stomata are tiny pores on the surface of leaves through which gases are exchanged with the atmosphere.
The light reaction of photosynthesis takes place in the thylakoid membranes of the chloroplast and produces oxygen.
Glucose produced during photosynthesis is stored in the form of starch in the leaves of the plant.
Plants absorb water from the soil through their roots and transport it to the leaves through the xylem vessels.
Carbon dioxide enters the leaves through the stomata and is converted into glucose in the Calvin cycle.

# Chapter 2: Respiration

Respiration is the process of releasing energy from food molecules inside the cells of living organisms.
Aerobic respiration is the type of respiration that uses oxygen to break down glucose completely into carbon dioxide and water.
Anaerobic respiration is the breakdown of glucose without oxygen and produces lactic acid in muscle cells.
Mitochondria are the cell organelles known as the powerhouses of the cell because they release most of the energy.
Adenosine triphosphate is the energy currency of the cell that stores energy released during respiration.
Yeast carries out fermentation, converting sugar into alcohol and carbon dioxide in the absence of oxygen.
The diaphragm and the intercostal muscles move during breathing so that air flows into and out of the lungs.

# Chapter 3: Transport in Plants

Transpiration is the loss of water in the form of vapour from the aerial parts of a plant, mainly through stomata.
Xylem is the vascular tissue that conducts water and minerals from the roots to the leaves of the plant.
Phloem is the vascular tissue that transports the food prepared in the leaves to the other parts of the plant.
Root hairs are long thin outgrowths of root epidermal cells that increase the surface area for absorbing water.
Osmosis is the movement of water molecules through a semipermeable membrane from a region of higher water concentration.
Guard cells are the bean shaped cells that surround each stoma and control its opening and closing.
""" * 2


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("EXAMFORGE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("EXAMFORGE_LLM_PROVIDER", "offline")
    monkeypatch.setenv("EXAMFORGE_ALLOW_REGISTRATION", "true")
    return load_settings(load_env_file=False)


@pytest.fixture()
def client(settings):
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


def register(client: TestClient, email: str = "teacher@example.com", password: str = "correct-horse-battery"):
    r = client.post("/api/auth/register", json={"email": email, "password": password, "name": "Test Teacher"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}
