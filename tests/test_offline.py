"""End-to-end test with a fake model and fake web: no internet, no tokens.

Run from the project folder:
    python tests/test_offline.py

It runs research, follow-ups, all four exports, the document workspace and the
scorecard, checks the hallucination guards, and writes sample exports to
tests/output/ so you can open them.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fakes  # noqa: E402
import exporters  # noqa: E402
import research_agent  # noqa: E402
import search_tools  # noqa: E402
from documents import chunk_text, read_pasted  # noqa: E402
from evaluation import faithfulness_check  # noqa: E402
from utils import AppError  # noqa: E402

fakes.install(research_agent, search_tools)
OUT = Path(__file__).parent / "output"
OUT.mkdir(exist_ok=True)
passed = 0


def check(condition: bool, message: str) -> None:
    global passed
    if not condition:
        raise AssertionError(message)
    passed += 1
    print(f"[ok] {message}")


# 1. Topic validation uses no tokens.
try:
    research_agent.run_research("AI")
    check(False, "short topic rejected")
except AppError as error:
    check(error.kind == "topic", "too-short topic gives a friendly 'topic' error")

# 2. Research at every depth.
stages = []
for depth in ("Quick", "Standard", "Deep"):
    report = research_agent.run_research("How do heat pumps work in cold climates?", "technical", depth, "Standard",
                                         "Any time", progress=lambda s, m: stages.append(s), use_cache=False)
    check(report["key_findings"], f"{depth}: report has key findings")
    ids = {s["id"] for s in report["sources"]}
    all_cites = [n for f in report["key_findings"] for n in f["citations"]]
    check(all(n in ids for n in all_cites), f"{depth}: every citation exists in the registry")
    check(not any("invented.example.com" in f["evidence"] for f in report["key_findings"]),
          f"{depth}: invented URL removed")
    check(any("99" in f["problem"] for f in report["flags"]), f"{depth}: invalid citation [99] flagged")
    check(any("Uncited" in c["item"] or "uncited" in c["item"] for c in report["could_not_verify"]),
          f"{depth}: uncited finding moved to Could Not Verify")
    check(not any("pinterest" in s["domain"] for s in report["sources"]), f"{depth}: excluded domain dropped")
    check(any(s["read_status"] == "snippet" for s in report["sources"]), f"{depth}: blocked page kept as snippet")
    check(report["scorecard"]["metrics"], f"{depth}: scorecard computed")
check({"planning", "searching", "reading", "writing", "checking", "done"} <= set(stages),
      "progress callback reports every real stage")

# 3. Cache: same request returns instantly with from_cache.
cached = research_agent.run_research("How do heat pumps work in cold climates?", "technical", "Deep", "Standard")
check(cached["metadata"].get("from_cache"), "identical request served from cache")

# 4. Follow-ups.
answer = research_agent.run_followup(report, "How do defrost cycles affect output?")
check("[2]" in answer["answer"] and not answer["searched"], "follow-up answered from stored sources")
before = len(report["sources"])
answer2 = research_agent.run_followup(report, "Something unrelated to the report?")
check(answer2["searched"] and len(report["sources"]) > before, "follow-up searched and added new sources")
research_agent.add_addendum(report, answer)
check(len(report["addenda"]) == 1, "follow-up added to report as addendum")

# 5. Exports.
for detail in exporters.DETAIL_LEVELS:
    md = exporters.make_markdown(report, detail)
    check("## Sources" in md, f"Markdown ({detail}) has Sources")
(OUT / "sample.md").write_text(exporters.make_markdown(report, "Full + research details"), encoding="utf-8")
docx_bytes = exporters.make_docx(report, "Full + research details")
(OUT / "sample.docx").write_bytes(docx_bytes)
check(docx_bytes[:2] == b"PK", "Word file created")
pdf_bytes = exporters.make_pdf(report, "Full + research details")
(OUT / "sample.pdf").write_bytes(pdf_bytes)
check(pdf_bytes[:4] == b"%PDF", "PDF created")
outline = research_agent.make_slide_outline(report, 9)
check(len(outline) == 6 and all(77 not in s["citations"] for s in outline), "slide outline: count and citations checked")
pptx_bytes = exporters.make_pptx(report, outline)
(OUT / "sample.pptx").write_bytes(pptx_bytes)
check(pptx_bytes[:2] == b"PK", "PowerPoint created")

# 6. Faithfulness judge.
judged = faithfulness_check(report)
check(judged["checked"] >= 1, "faithfulness check returns verdicts")

# 7. Document workspace.
text = ("Heat pumps cut household heating emissions by about 50 percent. " * 40 + "\n\n") * 12
doc = read_pasted(text)
check(len(chunk_text(doc["text"], 2800)) > 1, "long document is chunked")
doc_report = research_agent.run_document_job(doc["text"], "notes.txt", "Word document", "2 pages",
                                             "class report", "students", enhance=True)
check(any(s["origin"] == "added" for s in doc_report["detailed_analysis"]), "added content is labelled")
check(doc_report["key_findings"] and doc_report["sources"], "fact-check added cited web sources")
(OUT / "document.docx").write_bytes(exporters.make_docx(doc_report))
(OUT / "document.pdf").write_bytes(exporters.make_pdf(doc_report))
(OUT / "document.pptx").write_bytes(exporters.make_pptx(doc_report, research_agent.make_slide_outline(doc_report, 7)))
check(True, "document exports created")

print(f"\nAll {passed} checks passed. Sample files are in {OUT}")
