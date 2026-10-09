"""Research Desk: Streamlit entry point. Page layout and navigation only.

Backend work happens in research_agent.py; drawing happens in ui_components.py.
State lives in st.session_state (no database):
  history      list of {"report": report dict}, oldest first
  current      id of the report open on the Research page
  current_doc  id of the report open on the Documents page
  chats        follow-up conversations per report id
  exports      prepared export files per (report, format, length)
Clicking tabs, expanders, history items or downloads never re-runs research:
research only runs when the "Research this topic" button is pressed.
"""

from __future__ import annotations

import streamlit as st

import exporters
import research_agent
import ui_components as ui
from documents import read_document, read_pasted
from utils import AppError, check_groq_key, friendly_error

st.set_page_config(page_title="Research Desk", page_icon=":material/menu_book:", layout="wide",
                   initial_sidebar_state="auto")
ui.inject_css()

state = st.session_state
state.setdefault("history", [])
state.setdefault("current", None)
state.setdefault("current_doc", None)


# ---------------------------------------------------------------------------
# Helpers that change state (used as button callbacks)
# ---------------------------------------------------------------------------
def find_report(report_id: str | None) -> dict | None:
    return next((h["report"] for h in state.history if h["report"]["id"] == report_id), None)


def open_report(report_id: str) -> None:
    report = find_report(report_id)
    if report and report["kind"] == "document":
        state.current_doc = report_id
        state.go_to = "documents"
    else:
        state.current = report_id
        state.go_to = "research"


def clear_history() -> None:
    state.history = []
    for key in ("chats", "exports", "outlines", "faith"):
        state[key] = {}
    state.current = state.current_doc = None


def save_report(report: dict) -> None:
    state.history.append({"report": report})
    state.history = state.history[-20:]          # keep memory use small


def slide_outline(report: dict, count: int) -> list[dict]:
    """Slide outlines are cached per report and count, so re-downloading is free."""
    outlines = state.setdefault("outlines", {})
    key = f"{report['id']}|{count}|{len(report['addenda'])}"
    if key not in outlines:
        outlines[key] = research_agent.make_slide_outline(report, count)
    return outlines[key]


def key_problem_notice() -> bool:
    ok, message = check_groq_key()
    if not ok:
        ui.notice("The app isn't connected to Groq yet.",
                  message + " On Streamlit Cloud, add it under App settings → Secrets.", kind="error")
    return ok


# ---------------------------------------------------------------------------
# Research page
# ---------------------------------------------------------------------------
def research_page() -> None:
    report = find_report(state.current)
    if report:
        show_report(report)
        return

    if "topic_pending" in state:                 # an example topic was clicked
        state.topic = state.pop("topic_pending")
    request = ui.research_form(state)
    if not request:
        if state.get("last_error"):
            ui.error_notice(state.last_error)
        return
    state.last_error = None
    if not key_problem_notice():
        return
    with st.status("Planning …", expanded=True) as status:
        try:
            report = research_agent.run_research(**request, progress=ui.progress_reporter(status))
            status.update(label="Report ready", state="complete", expanded=False)
        except AppError as error:
            status.update(label="Research stopped", state="error", expanded=True)
            state.last_error = error
        except Exception as error:               # never show a raw traceback
            status.update(label="Research stopped", state="error", expanded=True)
            state.last_error = friendly_error(error)
    if state.get("last_error"):
        ui.error_notice(state.last_error)
        st.caption("Your options are kept. Press the button again to retry.")
        return
    save_report(report)
    state.current = report["id"]
    st.rerun()


def show_report(report: dict) -> None:
    top_left, top_right = st.columns([4, 1], vertical_alignment="bottom")
    with top_right:
        if st.button("New research", icon=":material/add:", use_container_width=True, key="new"):
            state.current = None
            st.rerun()
    with top_left:
        ui.report_header(report)
    if report["warnings"]:
        ui.notice("Read with care", items=report["warnings"])

    body_html, contents = ui.report_html(report)
    reading, side = st.columns([7, 3], gap="large")
    with side:
        with st.container(key="side-column"):
            ui.side_column(report, contents)
    with reading:
        with st.container(key="mobile-toc"):
            with st.expander("Contents"):
                st.html(f"<div class='rd-side'>{ui.contents_html(contents)}</div>")
        ui.scorecard(report)
        faithfulness_section(report)
        st.html(body_html)
        ui.research_details(report)
        st.divider()
        ui.export_panel(report, slide_outline)
        st.divider()
        ui.followup_panel(report, research_agent.run_followup, research_agent.add_addendum)


@st.fragment
def faithfulness_section(report: dict) -> None:
    """Optional LLM-as-judge check, off by default because it uses tokens."""
    results = state.setdefault("faith", {}).get(report["id"])
    if results is None:
        if report["key_findings"] and st.button("Run faithfulness check (uses about 3–4K tokens)",
                                                key=f"faith-{report['id']}", type="tertiary",
                                                icon=":material/rule:"):
            from evaluation import faithfulness_check
            try:
                with st.spinner("Checking sampled claims against their cited source text …"):
                    state.faith[report["id"]] = faithfulness_check(report)
            except AppError as error:
                ui.error_notice(error)
                return
            st.rerun(scope="fragment")
        return
    with st.expander(f"Faithfulness check: {results['checked']} claims checked", expanded=True):
        if not results["results"]:
            st.markdown("No finding cites a source whose full text was read, so nothing could be checked.")
        for item in results["results"]:
            st.markdown(f"**Finding {item['finding']}: {item['verdict']}.** {ui.esc(item['claim'])}  \n"
                        f"<span class='rd-src-meta'>{ui.esc(item['reason'])}</span>", unsafe_allow_html=True)
        st.caption(results["note"])


# ---------------------------------------------------------------------------
# My Document Workspace
# ---------------------------------------------------------------------------
DOC_LENGTH_CHOICES = {
    "PDF": ["1 page (~500 words)", "2 pages (~1,000 words)", "3–4 pages (~1,800 words)", "5+ pages (~2,500 words)"],
    "Word document": ["1 page (~500 words)", "2 pages (~1,000 words)", "3–4 pages (~1,800 words)",
                      "5+ pages (~2,500 words)"],
    "Summary": ["short", "medium", "long"],
}


def documents_page() -> None:
    report = find_report(state.current_doc)
    st.html("<div class='rd-wordmark'>My Document Workspace</div>"
            "<p class='rd-lede'>Bring your own report or notes and turn them into slides, a PDF, a Word document "
            "or a summary. Your content stays faithful; anything the assistant adds is labelled, and facts checked "
            "on the web are cited.</p>")
    if report:
        if st.button("Start a new document", icon=":material/add:", key="new-doc"):
            state.current_doc = None
            st.rerun()
        show_document_result(report)
        return

    source = st.segmented_control("Input", ["Upload a file", "Paste text"], default="Upload a file",
                                  key="doc-input") or "Upload a file"
    document, name = None, ""
    try:
        if source == "Paste text":
            pasted = st.text_area("Paste your text", height=240, key="doc-paste")
            if pasted.strip():
                document, name = read_pasted(pasted), "Pasted text"
        else:
            upload = st.file_uploader("PDF, DOCX, TXT or MD (max 20 MB)", type=["pdf", "docx", "txt", "md"],
                                      key="doc-upload")
            if upload is not None:
                document, name = read_document(upload.name, upload.getvalue()), upload.name
    except AppError as error:
        ui.error_notice(error)
        if error.kind == "scanned_pdf":
            st.caption("Tip: Google Drive (open the PDF with Google Docs) and most PDF editors can run OCR for free.")
        return
    if not document:
        return
    pages = f"{document['pages']} pages · " if document.get("pages") else ""
    st.caption(f"Read {pages}{document['words']:,} words. {document['note']}")

    output = st.segmented_control("What do you want?", ["Slides", "PDF", "Word document", "Summary"],
                                  default="Slides", key="doc-output") or "Slides"
    if output == "Slides":
        length = str(st.slider("Number of slides", 5, 16, 8, key="doc-slides",
                               help="Includes the title, agenda and sources slides."))
    else:
        length = st.segmented_control("Length", DOC_LENGTH_CHOICES[output], default=DOC_LENGTH_CHOICES[output][1],
                                      key=f"doc-length-{output}") or DOC_LENGTH_CHOICES[output][1]
    col1, col2 = st.columns(2)
    purpose = col1.text_input("Purpose", placeholder="e.g. class presentation, professional report", key="doc-purpose")
    audience = col2.text_input("Audience", placeholder="e.g. classmates, managers, general public", key="doc-audience")
    enhance = st.checkbox("Enhance with web research: fact-check key claims and add cited sources", key="doc-enhance",
                          help="Uses more time and tokens. Claims that can't be verified are listed separately.")
    calls = (document["words"] // 2000 + 1) + 1 + (1 if output == "Slides" else 0) + (1 if enhance else 0)
    st.caption(f"This needs about {calls} model call(s). Long documents take longer because of Groq's free "
               "per-minute limit.")
    if not st.button("Create", type="primary", key="doc-go"):
        return
    if not key_problem_notice():
        return
    with st.status("Reading your document …", expanded=True) as status:
        try:
            progress = ui.progress_reporter(status)
            report = research_agent.run_document_job(document["text"], name, output, length, purpose, audience,
                                                     enhance, progress)
            if output == "Slides":
                progress("writing", "Writing the slide outline …")
                state.setdefault("outlines", {})[f"{report['id']}|{int(length)}|0"] = \
                    research_agent.make_slide_outline(report, int(length), progress)
            status.update(label="Ready", state="complete", expanded=False)
        except AppError as error:
            status.update(label="Stopped", state="error")
            ui.error_notice(error)
            return
        except Exception as error:
            status.update(label="Stopped", state="error")
            ui.error_notice(friendly_error(error))
            return
    save_report(report)
    state.current_doc = report["id"]
    st.rerun()


def show_document_result(report: dict) -> None:
    ui.report_header(report)
    if report["warnings"]:
        ui.notice("Please note", items=report["warnings"])
    output = report["metadata"]["output_type"]
    files = state.setdefault("exports", {})
    key = f"{report['id']}|main"
    try:
        if key not in files:
            if output == "Slides":
                outline = slide_outline(report, int(report["metadata"]["length"]))
                files[key] = ("pptx", exporters.make_pptx(report, outline))
            elif output == "PDF":
                files[key] = ("pdf", exporters.make_pdf(report))
            elif output == "Word document":
                files[key] = ("docx", exporters.make_docx(report))
            else:
                files[key] = ("md", exporters.make_markdown(report).encode("utf-8"))
        extension, data = files[key]
        mime = dict(ui.EXPORT_FORMATS.values())[extension]
        st.download_button(f"Download {output.lower()}", data=data, file_name=exporters.file_name(report, extension),
                           mime=mime, type="primary", on_click="ignore", icon=":material/download:",
                           key=f"doc-dl-{report['id']}")
    except AppError as error:
        ui.error_notice(error)
    if output == "Slides":
        with st.expander("Slide outline preview", expanded=True):
            for number, slide in enumerate(slide_outline(report, int(report["metadata"]["length"])), 3):
                st.markdown(f"**{number}. {ui.esc(slide['title'])}**\n\n"
                            + "\n".join(f"- {ui.esc(b)}" for b in slide["bullets"]))
    body_html, _ = ui.report_html(report)
    st.html(body_html)
    st.divider()
    ui.export_panel(report, slide_outline)


# ---------------------------------------------------------------------------
# Navigation and sidebar
# ---------------------------------------------------------------------------
pages = {
    "research": st.Page(research_page, title="Research", icon=":material/search:", url_path="research", default=True),
    "documents": st.Page(documents_page, title="My Documents", icon=":material/description:", url_path="documents"),
}
navigation = st.navigation(list(pages.values()), position="sidebar")
target = state.pop("go_to", None)
if target and pages[target].url_path != navigation.url_path:
    st.switch_page(pages[target])
with st.sidebar:
    ui.sidebar_history(state.history, open_report, clear_history)
    st.divider()
    ui.sidebar_usage()
    st.caption("Single CrewAI agent on Groq (openai/gpt-oss-120b) · free web search · no database.")
navigation.run()
