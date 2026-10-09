"""Markdown, Word, PDF and PowerPoint files, all generated from the report dict.

How it stays consistent: report_blocks() turns a report into an ordered list of
simple "blocks" (heading, paragraph, finding, table, list, sources...). Every
exporter, and the on-screen report, walks the same list, so all formats always
have the same sections in the same order.

Design system (shared with assets/style.css): warm paper, near-black ink, one
deep ink-green accent, hairline rules, confidence colours always paired with
a text label. Fonts: PDFs embed Source Serif 4 / IBM Plex (assets/fonts, OFL
licence). Word and PowerPoint can't embed fonts from Python, so they use
Georgia and Arial, which nearly every computer has.
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from utils import AppError, get_logger

log = get_logger(__name__)

FONT_DIR = Path(__file__).parent / "assets" / "fonts"

# Design tokens (hex without '#').
INK, MUTED, RULE, PAPER = "1A1A1A", "5C5A55", "D9D4CA", "FBFAF7"
ACCENT, ACCENT_SOFT = "1F4D3A", "E7EFEA"
EVIDENCE_BG, INTERP_BG = "F1EEE7", "FFFFFF"
CONFIDENCE_COLORS = {"High": ("2F6B3F", "E3EFE6"), "Medium": ("7A5200", "F5ECD9"), "Low": ("9B2C2C", "F6E2E0")}
VERDICT_COLORS = {"Supported": "2F6B3F", "Partially supported": "7A5200", "Contradicted": "9B2C2C",
                  "Not supported": "9B2C2C"}

DETAIL_LEVELS = ["Summary", "Standard", "Full + research details"]

FIXED_LIMITATIONS = [
    "Credibility tiers are assigned by a heuristic based on the type of website, not by checking each claim.",
    "The agent analysed excerpts of each source, not complete documents, to stay within free-tier token limits.",
    "Search results depend on what free search engines returned at the time of research.",
]


# ---------------------------------------------------------------------------
# Shared: report -> blocks
# ---------------------------------------------------------------------------
def strip_md(text: str) -> str:
    """Remove simple Markdown (bold, italics, heading marks) for Word/PDF/PPTX."""
    text = re.sub(r"\*\*(.+?)\*\*|__(.+?)__", lambda m: m.group(1) or m.group(2), text or "")
    text = re.sub(r"(?m)^#{1,6}\s*", "", text)
    return text.replace("`", "")


def source_line(source: dict) -> str:
    """'Title. domain · Tier 1 · 2024-05-01 · full text read'."""
    bits = [source["domain"], f"Tier {source['tier']}", source.get("date") or "date unknown", source["read_status"]]
    if source.get("preprint"):
        bits.append("preprint")
    return f"{source['title']}. " + " · ".join(bits)


def report_blocks(report: dict, detail: str = "Standard") -> list[dict]:
    """Ordered content blocks for a report. See module docstring."""
    blocks: list[dict] = []
    H = lambda text, level=1: blocks.append({"type": "heading", "text": text, "level": level})
    P = lambda text: text and blocks.append({"type": "para", "text": text})
    L = lambda items: items and blocks.append({"type": "list", "items": items})
    is_doc = report["kind"] == "document"
    summary_only = detail == "Summary"

    if report.get("warnings"):
        blocks.append({"type": "notice", "items": report["warnings"]})

    H("Summary" if is_doc else "Executive Summary")
    P(report["executive_summary"])

    if not is_doc and not summary_only and report["introduction_and_scope"]:
        H("Introduction and Scope")
        P(report["introduction_and_scope"])

    if is_doc and not summary_only:
        for section in report["detailed_analysis"]:
            H(section["heading"] or "Section")
            if section.get("origin") == "added":
                blocks.append({"type": "label", "text": "Added by the assistant · not from your document"})
            P(section["body"])

    if report["key_findings"]:
        H("Fact-check of Key Claims" if is_doc else "Key Findings")
        for number, finding in enumerate(report["key_findings"], 1):
            blocks.append({"type": "finding", "number": number, **finding})

    if not is_doc and not summary_only:
        if report["detailed_analysis"]:
            H("Detailed Analysis")
            for section in report["detailed_analysis"]:
                H(section["heading"], 2)
                P(section["body"])
        for section in report["extra_sections"]:
            H(section["heading"])
            P(section["body"])
        if report["perspectives"]:
            H("Perspectives Compared")
            blocks.append({"type": "table", "header": ["Position", "Held by", "Main claim", "Evidence", "Strength"],
                           "rows": [[p["position"], p["held_by"], p["summary"],
                                     p["evidence"], p["evidence_strength"]] for p in report["perspectives"]]})
            for label, key in (("Where sources agree", "agreement"), ("Where sources disagree", "disagreement"),
                               ("Weight of evidence", "weight_of_evidence")):
                if report[key]:
                    blocks.append({"type": "labelled", "label": label, "text": report[key]})

    if report["could_not_verify"]:
        H("Could Not Verify" if is_doc else "Could Not Verify / Open Questions")
        L([f"{u['item']}" + (f" ({u['reason']})" if u.get("reason") else "") for u in report["could_not_verify"]])

    if not is_doc:
        H("Limitations of This Research")
        L(report["limitations"] + FIXED_LIMITATIONS if not summary_only else FIXED_LIMITATIONS[:1] + report["limitations"][:2])

    if not summary_only and report["conclusion"]:
        H("Conclusion")
        P(report["conclusion"])

    if report.get("addenda"):
        H("Addenda: Follow-up Questions")
        for addendum in report["addenda"]:
            blocks.append({"type": "labelled", "label": f"Q: {addendum['question']}",
                           "text": addendum["answer"] + (" (This answer used an additional web search.)"
                                                         if addendum.get("searched") else "")})

    if report.get("background_note") and not summary_only:
        H("Background Knowledge (Not Researched)")
        blocks.append({"type": "label", "text": "From the model's general knowledge. Not checked against sources."})
        P(report["background_note"])

    if report["sources"]:
        H("Sources")
        cited = [s for s in report["sources"] if s.get("cited")]
        others = [s for s in report["sources"] if not s.get("cited")]
        blocks.append({"type": "sources", "items": cited or report["sources"],
                       "intro": "Generated from the source registry by code. Tiers are heuristic."})
        if others and cited and detail != "Summary":
            H("Also Returned by Search (Not Cited)", 2)
            blocks.append({"type": "sources", "items": others, "intro": ""})

    if detail == "Full + research details" and not is_doc:
        meta = report["metadata"]
        H("Appendix: Research Details")
        L([f"Mode: {meta.get('mode_label')} · Depth: {meta.get('depth')} · Length: {meta.get('length')} · "
           f"Time focus: {meta.get('time_focus')}",
           "Queries: " + " | ".join(meta.get("queries", [])),
           f"Model: {meta.get('model')} · Created: {meta.get('created_at')} · "
           f"Duration: {meta.get('duration_seconds')} s · Tokens (estimate): {meta.get('tokens_estimate')}"])
        if report.get("flags"):
            H("Citation checks", 2)
            L([f"{f['where']}: {f['problem']} ({f['action']})" for f in report["flags"]])
        if report.get("scorecard"):
            H("Quality scorecard", 2)
            L([f"{m['name']}: {m['display']}" for m in report["scorecard"]["metrics"]])
    return blocks


def _meta_line(report: dict) -> str:
    meta = report["metadata"]
    created = (meta.get("created_at") or "")[:10]
    if report["kind"] == "document":
        return f"From: {meta.get('source_name', 'your document')} · {meta.get('output_type')} · {created}"
    return (f"{meta.get('mode_label', '')} · {meta.get('depth', '')} depth · {meta.get('time_focus', '')} · "
            f"{len(report['sources'])} sources · {created}")


def file_name(report: dict, extension: str) -> str:
    """A safe file name from the report title."""
    slug = re.sub(r"[^a-z0-9]+", "-", report["title"].lower()).strip("-")[:60] or "report"
    return f"{slug}.{extension}"


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------
def make_markdown(report: dict, detail: str = "Standard") -> str:
    """Report as Markdown text, with clickable source links."""
    urls = {s["id"]: s["url"] for s in report["sources"]}

    def link_cites(text: str) -> str:
        return re.sub(r"\[(\d+)\]", lambda m: f"[[{m.group(1)}]]({urls[int(m.group(1))]})"
                      if int(m.group(1)) in urls else m.group(0), text or "")

    out = [f"# {report['title']}", "", f"*{_meta_line(report)}*", ""]
    for block in report_blocks(report, detail):
        kind = block["type"]
        if kind == "heading":
            out += ["#" * (block["level"] + 1) + " " + block["text"], ""]
        elif kind == "para":
            out += [link_cites(block["text"]), ""]
        elif kind == "notice":
            out += ["> **Notices**"] + [f"> - {w}" for w in block["items"]] + [""]
        elif kind == "label":
            out += [f"*{block['text']}*", ""]
        elif kind == "list":
            out += [f"- {link_cites(i)}" for i in block["items"]] + [""]
        elif kind == "labelled":
            out += [f"**{block['label']}.** {link_cites(block['text'])}", ""]
        elif kind == "finding":
            cites = ", ".join(f"[{n}]({urls.get(n, '')})" for n in block["citations"])
            out += [f"### {block['number']}. {block['claim']}", "",
                    f"**Evidence (what sources say):** {link_cites(block['evidence'])}", ""]
            if block.get("interpretation"):
                out += [f"**Interpretation (analysis, not stated by sources):** {link_cites(block['interpretation'])}", ""]
            out += [f"**Confidence: {block['confidence']}.** {block.get('confidence_reason', '')} · Sources: {cites}", ""]
        elif kind == "table":
            out += ["| " + " | ".join(block["header"]) + " |", "|" + "---|" * len(block["header"])]
            out += ["| " + " | ".join(link_cites(str(c)).replace("|", "/").replace("\n", " ") for c in row) + " |"
                    for row in block["rows"]] + [""]
        elif kind == "sources":
            if block["intro"]:
                out += [f"*{block['intro']}*", ""]
            out += [f"{s['id']}. [{s['title']}]({s['url']}) · {s['domain']} · {s['tier_label']} · "
                    f"{s.get('date') or 'date unknown'} · {s['read_status']}" for s in block["items"]] + [""]
    return "\n".join(out).strip() + "\n"


# ---------------------------------------------------------------------------
# Word (.docx)
# ---------------------------------------------------------------------------
def make_docx(report: dict, detail: str = "Standard") -> bytes:
    """Word document with real heading styles, a table of contents field, page numbers and links."""
    try:
        return _make_docx(report, detail)
    except Exception as error:
        log.exception("DOCX export failed")
        raise AppError("export", str(error))


def _docx_hyperlink(paragraph, url: str, text: str):
    """python-docx has no hyperlink API; build the XML element directly."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    part = paragraph.part
    r_id = part.relate_to(url, "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink",
                          is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    color = OxmlElement("w:color")
    color.set(qn("w:val"), ACCENT)
    underline = OxmlElement("w:u")
    underline.set(qn("w:val"), "single")
    props.extend([color, underline])
    run.append(props)
    text_el = OxmlElement("w:t")
    text_el.text = text
    text_el.set(qn("xml:space"), "preserve")
    run.append(text_el)
    link.append(run)
    paragraph._p.append(link)


def _docx_shade(paragraph, fill: str, left_border: str | None = None) -> None:
    """Background shading and an optional coloured left rule for a paragraph."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    props = paragraph._p.get_or_add_pPr()
    shade = OxmlElement("w:shd")
    shade.set(qn("w:val"), "clear")
    shade.set(qn("w:color"), "auto")
    shade.set(qn("w:fill"), fill)
    props.append(shade)
    if left_border:
        borders = OxmlElement("w:pBdr")
        left = OxmlElement("w:left")
        for key, value in (("w:val", "single"), ("w:sz", "18"), ("w:space", "8"), ("w:color", left_border)):
            left.set(qn(key), value)
        borders.append(left)
        props.append(borders)


def _docx_field(paragraph, instruction: str, placeholder: str = "") -> None:
    """Insert a Word field (e.g. 'TOC \\o "1-2" \\h' or 'PAGE'). Word fills it in."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    def char(kind: str):
        run = OxmlElement("w:r")
        fld = OxmlElement("w:fldChar")
        fld.set(qn("w:fldCharType"), kind)
        run.append(fld)
        return run

    instr_run = OxmlElement("w:r")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = instruction
    instr_run.append(instr)
    text_run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = placeholder
    text_run.append(text)
    for element in (char("begin"), instr_run, char("separate"), text_run, char("end")):
        paragraph._p.append(element)


def _make_docx(report: dict, detail: str) -> bytes:
    import docx
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt, RGBColor, Cm

    rgb = lambda h: RGBColor.from_string(h)
    document = docx.Document()
    section = document.sections[0]
    section.left_margin = section.right_margin = Cm(2.4)

    styles = document.styles
    styles["Normal"].font.name = "Georgia"
    styles["Normal"].font.size = Pt(11)
    styles["Normal"].font.color.rgb = rgb(INK)
    styles["Normal"].paragraph_format.space_after = Pt(6)
    styles["Normal"].paragraph_format.line_spacing = 1.2
    for name, size in (("Title", 26), ("Heading 1", 16), ("Heading 2", 13), ("Heading 3", 11.5)):
        style = styles[name]
        style.font.name = "Georgia"
        style.font.size = Pt(size)
        style.font.bold = name != "Title"
        style.font.color.rgb = rgb(INK if name == "Title" else ACCENT)

    def small(paragraph, text: str, color: str = MUTED, bold: bool = False, font: str = "Arial", size: float = 8.5):
        run = paragraph.add_run(text)
        run.font.name, run.font.size, run.font.bold = font, Pt(size), bold
        run.font.color.rgb = rgb(color)
        return run

    urls = {s["id"]: s for s in report["sources"]}

    def add_text(paragraph, text: str) -> None:
        """Add text, turning [n] markers into links to the source."""
        for piece in re.split(r"(\[\d+\])", strip_md(text)):
            match = re.fullmatch(r"\[(\d+)\]", piece)
            if match and int(match.group(1)) in urls:
                _docx_hyperlink(paragraph, urls[int(match.group(1))]["url"], piece)
            elif piece:
                paragraph.add_run(piece)

    # Title page
    document.add_paragraph(style="Title").add_run(report["title"])
    small(document.add_paragraph(), _meta_line(report), size=9.5, font="Consolas")
    if report["kind"] == "research":
        small(document.add_paragraph(), f"Topic: {report['metadata'].get('topic', '')}", size=10, color=INK)
    small(document.add_paragraph(), "Evidence is what sources say. Interpretation is the assistant's analysis. "
                                    "Citations [n] link to the sources list.", size=9)
    document.add_paragraph().add_run().add_break(docx.enum.text.WD_BREAK.PAGE)

    # Table of contents (a Word field: Word fills it when the file is opened/updated)
    document.add_heading("Contents", level=1)
    _docx_field(document.add_paragraph(), 'TOC \\o "1-2" \\h \\z \\u',
                "Right-click and choose 'Update Field' (or press F9) to build the table of contents.")
    document.add_paragraph().add_run().add_break(docx.enum.text.WD_BREAK.PAGE)

    # Footer page numbers
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.RIGHT
    small(footer, report["title"][:70] + "  ·  page ")
    _docx_field(footer, "PAGE", "1")

    for block in report_blocks(report, detail):
        kind = block["type"]
        if kind == "heading":
            document.add_heading(strip_md(block["text"]), level=block["level"])
        elif kind == "para":
            add_text(document.add_paragraph(), block["text"])
        elif kind == "label":
            small(document.add_paragraph(), block["text"].upper(), color=ACCENT, bold=True)
        elif kind == "notice":
            for item in block["items"]:
                paragraph = document.add_paragraph()
                small(paragraph, "NOTICE  ", color="7A5200", bold=True)
                paragraph.add_run(item).font.size = Pt(10)
                _docx_shade(paragraph, "F5ECD9")
        elif kind == "list":
            for item in block["items"]:
                add_text(document.add_paragraph(style="List Bullet"), item)
        elif kind == "labelled":
            paragraph = document.add_paragraph()
            paragraph.add_run(strip_md(block["label"]) + ". ").bold = True
            add_text(paragraph, block["text"])
        elif kind == "finding":
            document.add_heading(f"{block['number']}. {strip_md(block['claim'])}", level=2)
            evidence = document.add_paragraph()
            small(evidence, "EVIDENCE · WHAT SOURCES SAY\n", color=INK, bold=True)
            add_text(evidence, block["evidence"])
            _docx_shade(evidence, EVIDENCE_BG)
            if block.get("interpretation"):
                interp = document.add_paragraph()
                small(interp, "INTERPRETATION · ANALYSIS, NOT STATED BY SOURCES\n", color=ACCENT, bold=True)
                run_start = len(interp.runs)
                add_text(interp, block["interpretation"])
                for run in interp.runs[run_start:]:
                    run.italic = True
                _docx_shade(interp, INTERP_BG, left_border=ACCENT)
            conf = document.add_paragraph()
            color = CONFIDENCE_COLORS.get(block["confidence"], CONFIDENCE_COLORS["Low"])[0]
            small(conf, f"CONFIDENCE: {block['confidence'].upper()}", color=color, bold=True)
            small(conf, f"  ·  {block.get('confidence_reason', '')}  ·  Sources: ")
            add_text(conf, " ".join(f"[{n}]" for n in block["citations"]))
        elif kind == "table":
            table = document.add_table(rows=1, cols=len(block["header"]))
            table.style = "Table Grid"
            for cell, text in zip(table.rows[0].cells, block["header"]):
                cell.text = ""
                small(cell.paragraphs[0], text.upper(), color=INK, bold=True)
            for row in block["rows"]:
                cells = table.add_row().cells
                for cell, text in zip(cells, row):
                    cell.text = ""
                    add_text(cell.paragraphs[0], str(text))
                    for run in cell.paragraphs[0].runs:
                        run.font.size = Pt(9.5)
            document.add_paragraph()
        elif kind == "sources":
            if block["intro"]:
                small(document.add_paragraph(), block["intro"])
            for source in block["items"]:
                paragraph = document.add_paragraph()
                paragraph.paragraph_format.left_indent = Cm(0.8)
                paragraph.paragraph_format.first_line_indent = Cm(-0.8)
                small(paragraph, f"[{source['id']}]  ", color=ACCENT, bold=True, font="Consolas", size=9.5)
                _docx_hyperlink(paragraph, source["url"], source["title"])
                small(paragraph, f"  {source['domain']} · {source['tier_label']} · "
                                 f"{source.get('date') or 'date unknown'} · {source['read_status']}", size=8.5)

    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------
def make_pdf(report: dict, detail: str = "Standard") -> bytes:
    """PDF with a title page, contents with page numbers, bookmarks and clickable sources."""
    try:
        return _make_pdf(report, detail)
    except Exception as error:
        log.exception("PDF export failed")
        raise AppError("export", str(error))


def _hex(color: str) -> tuple[int, int, int]:
    return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))


def _make_pdf(report: dict, detail: str) -> bytes:
    from fpdf import FPDF, FontFace
    from fpdf.enums import XPos, YPos

    blocks = report_blocks(report, detail)
    title = report["title"]

    class PDF(FPDF):
        def header(self):
            if self.page_no() > 1:
                self.set_font("sans", "", 7.5)
                self.set_text_color(*_hex(MUTED))
                self.cell(0, 6, title[:90], new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                self.set_draw_color(*_hex(RULE))
                self.line(self.l_margin, self.get_y(), self.w - self.r_margin, self.get_y())
                self.ln(4)

        def footer(self):
            if self.page_no() > 1:
                self.set_y(-14)
                self.set_font("mono", "", 7.5)
                self.set_text_color(*_hex(MUTED))
                self.cell(0, 8, f"{self.page_no()}", align="R")

    pdf = PDF(format="A4")
    pdf.set_margins(24, 20, 24)
    pdf.set_auto_page_break(True, margin=20)
    pdf.add_font("serif", "", str(FONT_DIR / "SourceSerif4-Regular.ttf"))
    pdf.add_font("serif", "B", str(FONT_DIR / "SourceSerif4-SemiBold.ttf"))
    pdf.add_font("serif", "I", str(FONT_DIR / "SourceSerif4-Italic.ttf"))
    pdf.add_font("sans", "", str(FONT_DIR / "IBMPlexSans-Regular.ttf"))
    pdf.add_font("sans", "B", str(FONT_DIR / "IBMPlexSans-SemiBold.ttf"))
    pdf.add_font("mono", "", str(FONT_DIR / "IBMPlexMono-Regular.ttf"))
    pdf.set_title(title)
    pdf.set_creator("Research Desk")
    width = pdf.w - pdf.l_margin - pdf.r_margin

    def ink(color: str = INK):
        pdf.set_text_color(*_hex(color))

    def body(text: str, size: float = 10.5, style: str = "", color: str = INK, fill: str | None = None):
        pdf.set_font("serif", style, size)
        ink(color)
        if fill:
            pdf.set_fill_color(*_hex(fill))
        pdf.multi_cell(width, size * 0.55, strip_md(text), fill=bool(fill), padding=(2, 3) if fill else 0,
                       align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def label(text: str, color: str = MUTED):
        pdf.set_font("sans", "B", 7.5)
        ink(color)
        pdf.cell(0, 5, text.upper(), new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Title page
    pdf.add_page()
    pdf.set_y(70)
    pdf.set_draw_color(*_hex(ACCENT))
    pdf.set_line_width(0.8)
    pdf.line(pdf.l_margin, pdf.get_y(), pdf.l_margin + 30, pdf.get_y())
    pdf.ln(8)
    pdf.set_font("serif", "B", 24)
    ink()
    pdf.multi_cell(width, 11, title, align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(4)
    pdf.set_font("mono", "", 8.5)
    ink(MUTED)
    pdf.multi_cell(width, 5, _meta_line(report), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(30)
    pdf.set_font("sans", "", 8.5)
    pdf.multi_cell(width, 4.6, "How to read this report: Evidence (shaded) is what sources say. Interpretation "
                               "(ruled) is the assistant's analysis. Numbers in brackets refer to the Sources list, "
                               "where every link is clickable. Credibility tiers are heuristic.",
                   align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    # Contents page (filled at the end with real page numbers)
    def render_toc(pdf_, outline):
        pdf_.set_font("serif", "B", 16)
        pdf_.set_text_color(*_hex(INK))
        pdf_.set_x(pdf_.l_margin)
        pdf_.cell(0, 12, "Contents", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf_.ln(2)
        for section in outline:
            if section.level > 1:
                continue
            pdf_.set_font("sans", "B" if section.level == 0 else "", 9.5 if section.level == 0 else 8.5)
            indent = 6 * section.level
            pdf_.set_x(pdf_.l_margin + indent)
            link = pdf_.add_link(page=section.page_number)
            pdf_.cell(width - indent - 12, 6.5, section.name[:80], link=link)
            pdf_.set_font("mono", "", 8.5)
            pdf_.cell(12, 6.5, str(section.page_number), align="R", link=link,
                      new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    headings = sum(1 for b in blocks if b["type"] == "heading" and b["level"] == 1) + \
        sum(1 for b in blocks if b["type"] == "finding") + sum(1 for b in blocks if b["type"] == "heading" and b["level"] == 2)
    pdf.add_page()
    pdf.insert_toc_placeholder(render_toc, pages=1 + headings // 34)   # starts a new page itself afterwards
    {s["id"]: s for s in report["sources"]}
    for block in blocks:
        kind = block["type"]
        if kind == "heading":
            if block["level"] == 1:
                if pdf.get_y() > pdf.h - 60:
                    pdf.add_page()
                pdf.ln(5)
                pdf.start_section(strip_md(block["text"])[:90], level=0)
                pdf.set_font("serif", "B", 15)
                ink()
                pdf.multi_cell(width, 8, strip_md(block["text"]), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_draw_color(*_hex(RULE))
                pdf.set_line_width(0.2)
                pdf.line(pdf.l_margin, pdf.get_y() + 1, pdf.w - pdf.r_margin, pdf.get_y() + 1)
                pdf.ln(4)
            else:
                pdf.ln(2)
                pdf.start_section(strip_md(block["text"])[:90], level=1)
                pdf.set_font("serif", "B", 12)
                ink(ACCENT)
                pdf.multi_cell(width, 6.5, strip_md(block["text"]), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.ln(1)
        elif kind == "para":
            body(block["text"])
            pdf.ln(2.5)
        elif kind == "label":
            label(block["text"], ACCENT)
        elif kind == "notice":
            label("Notices", "7A5200")
            for item in block["items"]:
                body("• " + item, size=9, fill="F5ECD9")
            pdf.ln(3)
        elif kind == "list":
            for item in block["items"]:
                body("•  " + item, size=10.5)
                pdf.ln(0.8)
            pdf.ln(2)
        elif kind == "labelled":
            pdf.set_font("serif", "B", 10.5)
            ink()
            pdf.multi_cell(width, 5.8, strip_md(block["label"]), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            body(block["text"])
            pdf.ln(2)
        elif kind == "finding":
            if pdf.get_y() > pdf.h - 70:
                pdf.add_page()
            pdf.ln(2)
            pdf.start_section(f"{block['number']}. {strip_md(block['claim'])}"[:90], level=1)
            pdf.set_font("serif", "B", 11.5)
            ink()
            pdf.multi_cell(width, 6.2, f"{block['number']}. {strip_md(block['claim'])}", align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(1.5)
            label("Evidence · what sources say")
            body(block["evidence"], size=10, fill=EVIDENCE_BG)
            if block.get("interpretation"):
                pdf.ln(1.5)
                label("Interpretation · analysis, not stated by sources", ACCENT)
                top = pdf.get_y()
                pdf.set_x(pdf.l_margin + 3)
                pdf.set_font("serif", "I", 10)
                ink()
                pdf.multi_cell(width - 3, 5.5, strip_md(block["interpretation"]), align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_draw_color(*_hex(ACCENT))
                pdf.set_line_width(0.6)
                if pdf.get_y() > top:
                    pdf.line(pdf.l_margin, top, pdf.l_margin, pdf.get_y())
            pdf.ln(1.5)
            color = CONFIDENCE_COLORS.get(block["confidence"], CONFIDENCE_COLORS["Low"])[0]
            pdf.set_font("sans", "B", 8)
            ink(color)
            pdf.cell(pdf.get_string_width(f"CONFIDENCE: {block['confidence'].upper()}") + 2, 5,
                     f"CONFIDENCE: {block['confidence'].upper()}")
            pdf.set_font("sans", "", 8)
            ink(MUTED)
            pdf.multi_cell(0, 5, f" · {block.get('confidence_reason', '')} · Sources: "
                                 + ", ".join(f"[{n}]" for n in block["citations"]),
                           align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
            pdf.ln(3)
        elif kind == "table":
            pdf.set_font("sans", "", 7.8)
            ink()
            pdf.set_draw_color(*_hex(RULE))
            pdf.set_line_width(0.2)
            heading_style = FontFace(family="sans", emphasis="BOLD", size_pt=7.5, fill_color=_hex(EVIDENCE_BG))
            with pdf.table(col_widths=(18, 15, 27, 27, 13), line_height=4.2, padding=1.5,
                           headings_style=heading_style, first_row_as_headings=True) as table:
                header = table.row()
                for text in block["header"]:
                    header.cell(text.upper())
                for row in block["rows"]:
                    cells = table.row()
                    for text in row:
                        cells.cell(strip_md(str(text)))
            pdf.ln(4)
        elif kind == "sources":
            if block["intro"]:
                pdf.set_font("sans", "", 8)
                ink(MUTED)
                pdf.multi_cell(width, 4.5, block["intro"], align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.ln(2)
            for source in block["items"]:
                if pdf.get_y() > pdf.h - 30:
                    pdf.add_page()
                pdf.set_font("mono", "", 8.5)
                ink(ACCENT)
                pdf.cell(11, 5, f"[{source['id']}]")
                pdf.set_font("serif", "", 9.5)
                ink()
                pdf.multi_cell(width - 11, 5, source["title"], link=source["url"], align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_x(pdf.l_margin + 11)
                pdf.set_font("mono", "", 7)
                ink(ACCENT)
                pdf.multi_cell(width - 11, 3.8, source["url"][:140], link=source["url"], align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.set_x(pdf.l_margin + 11)
                pdf.set_font("sans", "", 7.5)
                ink(MUTED)
                pdf.multi_cell(width - 11, 4, f"{source['domain']} · {source['tier_label']} · "
                                              f"{source.get('date') or 'date unknown'} · {source['read_status']}",
                               align="L", new_x=XPos.LMARGIN, new_y=YPos.NEXT)
                pdf.ln(1.8)
    return bytes(pdf.output())


# ---------------------------------------------------------------------------
# PowerPoint (.pptx)
# ---------------------------------------------------------------------------
def make_pptx(report: dict, outline: list[dict]) -> bytes:
    """Slides from an outline (research_agent.make_slide_outline) plus code-made slides."""
    try:
        return _make_pptx(report, outline)
    except Exception as error:
        log.exception("PPTX export failed")
        raise AppError("export", str(error))


def _make_pptx(report: dict, outline: list[dict]) -> bytes:
    from pptx import Presentation
    from pptx.dml.color import RGBColor
    from pptx.enum.text import MSO_ANCHOR
    from pptx.util import Emu, Inches, Pt

    deck = Presentation()
    deck.slide_width, deck.slide_height = Inches(13.333), Inches(7.5)
    blank = deck.slide_layouts[6]
    color = lambda h: RGBColor.from_string(h)
    by_id = {s["id"]: s for s in report["sources"]}

    def new_slide(notes: str = ""):
        slide = deck.slides.add_slide(blank)
        background = slide.background.fill
        background.solid()
        background.fore_color.rgb = color(PAPER)
        if notes:
            slide.notes_slide.notes_text_frame.text = notes
        return slide

    def text_box(slide, left, top, width, height, text, size, font="Arial", bold=False, fill=INK, italic=False):
        box = slide.shapes.add_textbox(left, top, width, height)
        frame = box.text_frame
        frame.word_wrap = True
        frame.vertical_anchor = MSO_ANCHOR.TOP
        paragraph = frame.paragraphs[0]
        run = paragraph.add_run()
        run.text = text
        run.font.name, run.font.size, run.font.bold, run.font.italic = font, Pt(size), bold, italic
        run.font.color.rgb = color(fill)
        return frame

    def rule(slide, top, width=Inches(1.2), fill=ACCENT, height=Emu(38000)):
        line = slide.shapes.add_shape(1, Inches(0.8), top, width, height)   # 1 = rectangle
        line.fill.solid()
        line.fill.fore_color.rgb = color(fill)
        line.line.fill.background()
        line.shadow.inherit = False          # no drop shadow

    def footer(slide, number: int, citations: list[int]):
        text_box(slide, Inches(0.8), Inches(6.85), Inches(9), Inches(0.4),
                 report["title"][:80], 9, fill=MUTED)
        text_box(slide, Inches(10.5), Inches(6.85), Inches(2.1), Inches(0.4),
                 (("Sources " + " ".join(f"[{n}]" for n in citations) + "   ") if citations else "") + str(number),
                 9, font="Consolas", fill=MUTED).paragraphs[0].alignment = 3   # right

    def notes_with_sources(notes: str, citations: list[int]) -> str:
        lines = [strip_md(notes)]
        if citations:
            lines.append("\nSources:")
            lines += [f"[{n}] {by_id[n]['title']} ({by_id[n]['url']})" for n in citations if n in by_id]
        return "\n".join(lines)

    report["metadata"]
    # 1. Title slide
    slide = new_slide("Generated by Research Desk from a structured report. Check sources before presenting.")
    rule(slide, Inches(2.6))
    text_box(slide, Inches(0.8), Inches(2.8), Inches(11.5), Inches(2), report["title"], 40, font="Georgia")
    text_box(slide, Inches(0.8), Inches(5.0), Inches(11.5), Inches(0.5), _meta_line(report), 12, font="Consolas", fill=MUTED)

    # 2. Agenda
    slide = new_slide()
    text_box(slide, Inches(0.8), Inches(0.6), Inches(11.5), Inches(1), "Agenda", 30, font="Georgia")
    rule(slide, Inches(1.55))
    frame = text_box(slide, Inches(0.8), Inches(1.9), Inches(11.5), Inches(4.8), "", 18)
    for index, item in enumerate(outline, 1):
        paragraph = frame.paragraphs[0] if index == 1 else frame.add_paragraph()
        paragraph.space_after = Pt(8)
        run = paragraph.add_run()
        run.text = f"{index:02d}   {strip_md(item['title'])}"
        run.font.name, run.font.size = "Arial", Pt(18)
        run.font.color.rgb = color(INK)
    footer(slide, 2, [])

    # 3. Content slides
    for number, item in enumerate(outline, 3):
        slide = new_slide(notes_with_sources(item.get("notes", ""), item.get("citations", [])))
        text_box(slide, Inches(0.8), Inches(0.6), Inches(11.7), Inches(1.2), strip_md(item["title"]), 28, font="Georgia")
        rule(slide, Inches(1.75))
        frame = text_box(slide, Inches(0.8), Inches(2.1), Inches(11.2), Inches(4.5), "", 20)
        for index, bullet in enumerate(item.get("bullets", [])):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.space_after = Pt(14)
            marker = paragraph.add_run()
            marker.text = "—  "
            marker.font.color.rgb = color(ACCENT)
            marker.font.size = Pt(20)
            run = paragraph.add_run()
            run.text = strip_md(bullet)
            run.font.name, run.font.size = "Arial", Pt(20)
            run.font.color.rgb = color(INK)
        footer(slide, number, item.get("citations", []))

    # 4. Sources slide(s), always from the registry, never from model text
    cited = [s for s in report["sources"] if s.get("cited")] or report["sources"]
    for start in range(0, max(1, len(cited)), 10):
        chunk = cited[start : start + 10]
        slide = new_slide("Full source details, including tiers and dates, are in the written report.")
        text_box(slide, Inches(0.8), Inches(0.6), Inches(11.5), Inches(1),
                 "Sources" + (" (continued)" if start else ""), 28, font="Georgia")
        rule(slide, Inches(1.55))
        frame = text_box(slide, Inches(0.8), Inches(1.85), Inches(11.7), Inches(4.9), "", 12)
        for index, source in enumerate(chunk):
            paragraph = frame.paragraphs[0] if index == 0 else frame.add_paragraph()
            paragraph.space_after = Pt(5)
            num = paragraph.add_run()
            num.text = f"[{source['id']}]  "
            num.font.name, num.font.size, num.font.color.rgb = "Consolas", Pt(12), color(ACCENT)
            run = paragraph.add_run()
            run.text = f"{source['title'][:95]}"
            run.font.name, run.font.size, run.font.color.rgb = "Arial", Pt(12), color(INK)
            run.hyperlink.address = source["url"]
            meta_run = paragraph.add_run()
            meta_run.text = f"  {source['domain']} · Tier {source['tier']}"
            meta_run.font.name, meta_run.font.size, meta_run.font.color.rgb = "Arial", Pt(10), color(MUTED)
        footer(slide, len(outline) + 3 + start // 10, [])

    buffer = io.BytesIO()
    deck.save(buffer)
    return buffer.getvalue()
