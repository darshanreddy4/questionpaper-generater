"""Scratch end-to-end check (offline engine -> PDF/DOCX). Run: python scripts/smoke.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.exam.generator import PaperGenerator
from app.exam.llm_engine import GenContext
from app.exam.offline_engine import OfflineEngine
from app.exam.patterns import get_preset
from app.exam.schema import GenerationRequest, TopicSpec
from app.export import build_docx, build_pdf
from app.export.variants import make_variant

out = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ef")
out.mkdir(parents=True, exist_ok=True)

req = GenerationRequest(
    subject="Mathematics", class_level="10", board="CBSE", institution="Sunrise Public School",
    exam_name="Pre-Board Examination", pattern_id="cbse-10-maths", diagram_mode="more", seed=7,
    topics=[TopicSpec(name="Quadratic equations", weight=8), TopicSpec(name="Triangles and Pythagoras theorem", weight=6),
            TopicSpec(name="Statistics - mean", weight=4), TopicSpec(name="Circles and mensuration", weight=5),
            TopicSpec(name="Trigonometry heights and distances", weight=5), TopicSpec(name="Arithmetic progression", weight=4),
            TopicSpec(name="Coordinate geometry graph of a line", weight=4), TopicSpec(name="Probability", weight=3)],
)
pattern = get_preset("cbse-10-maths")
gen = PaperGenerator(OfflineEngine(7), req, pattern, progress=lambda p, m: print(p, m))
content, quality, slots = gen.run()
print("quality", {k: quality[k] for k in ("score", "status", "warnings", "difficulty", "diagrams")})
for f in quality["failed"][:12]:
    print("FAILED", f)
for mode in ("question", "answer", "combined"):
    (out / f"paper_{mode}.pdf").write_bytes(build_pdf(content, mode))
    (out / f"paper_{mode}.docx").write_bytes(build_docx(content, mode))
(out / "paper_B.pdf").write_bytes(build_pdf(make_variant(content, "B", 7), "question"))
print("written to", out)
