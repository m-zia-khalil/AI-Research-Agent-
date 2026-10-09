"""Exporters alone, using a small hand-written report. No internet, no tokens.

    python tests/test_exporters.py      (files are written to tests/output/)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import exporters  # noqa: E402

report = {
    "id": "demo", "kind": "research", "title": "Sample report for export testing",
    "metadata": {"topic": "Sample topic", "mode": "general", "mode_label": "General Learning", "depth": "Quick",
                 "length": "Brief", "time_focus": "Any time", "created_at": "2026-10-09T10:00:00+00:00",
                 "model": "groq/openai/gpt-oss-120b", "queries": ["sample query"]},
    "executive_summary": "This is a sample summary citing a source [1].",
    "introduction_and_scope": "Scope text.",
    "key_findings": [{"claim": "A sample claim.", "evidence": "Source [1] says so.",
                      "interpretation": "Our reading of it.", "confidence": "Medium",
                      "confidence_reason": "one source", "citations": [1]}],
    "detailed_analysis": [{"heading": "Details", "body": "Body text [1].", "citations": [1]}],
    "perspectives": [{"position": "View A", "held_by": "Group A", "summary": "Claims X", "evidence": "[1]",
                      "evidence_strength": "Moderate", "citations": [1]}],
    "agreement": "", "disagreement": "", "weight_of_evidence": "Balanced.", "extra_sections": [],
    "could_not_verify": [{"item": "Something unverifiable", "reason": "no sources"}],
    "limitations": ["Only one source."], "conclusion": "Conclusion [1].", "background_note": "",
    "addenda": [], "warnings": ["This is test content."], "flags": [],
    "sources": [{"id": 1, "title": "Example source", "url": "https://example.org/a", "domain": "example.org",
                 "tier": 3, "tier_label": "Tier 3 · other", "date": "2025-01-01", "read_status": "full text",
                 "preprint": False, "cited": True, "text": "", "snippet": ""}],
}
out = Path(__file__).parent / "output"
out.mkdir(exist_ok=True)
(out / "export-test.md").write_text(exporters.make_markdown(report), encoding="utf-8")
(out / "export-test.docx").write_bytes(exporters.make_docx(report))
(out / "export-test.pdf").write_bytes(exporters.make_pdf(report))
outline = [{"title": "A sample slide", "bullets": ["First point", "Second point"], "notes": "Detail [1].",
            "citations": [1]}]
(out / "export-test.pptx").write_bytes(exporters.make_pptx(report, outline))
print(f"[ok] Markdown, Word, PDF and PowerPoint written to {out}")
