"""Small render functions, one per part of the screen.

app.py decides *what* to show; these functions decide *how*. They only read
the report dict and session state; they never search or call the model
directly (the buttons call research_agent functions passed in by app.py).
"""

from __future__ import annotations

import html
import re
from pathlib import Path

import streamlit as st

import exporters
from modes import DEPTHS, LENGTHS, MODES, TIME_FOCUS
from utils import GROQ_TPD_LIMIT, LIMITER, AppError

CSS_FILE = Path(__file__).parent / "assets" / "style.css"

# Theme tokens for style.css (same values as config.toml and exporters.py).
THEME_VARS = {
    "light": {"ink": "#1A1A1A", "muted": "#5C5A55", "rule": "#E2DDD3", "accent": "#1F4D3A",
              "evidence-bg": "#F2EFE9", "high": "#2F6B3F", "high-bg": "#E3EFE6", "medium": "#7A5200",
              "medium-bg": "#F5ECD9", "low": "#9B2C2C", "low-bg": "#F6E2E0", "warn": "#7A5200", "warn-bg": "#F7F0E1"},
    "dark": {"ink": "#E9E6DF", "muted": "#A39F96", "rule": "#33312C", "accent": "#8CC3A6",
             "evidence-bg": "#1F1E1B", "high": "#93CFA6", "high-bg": "#1D2A21", "medium": "#E2B867",
             "medium-bg": "#2D2617", "low": "#EE9F97", "low-bg": "#33201E", "warn": "#E2B867", "warn-bg": "#2A2418"},
}

STAGES = {"planning": "Planning", "searching": "Searching", "reading": "Reading", "writing": "Writing",
          "waiting": "Waiting", "checking": "Checking", "done": "Done"}


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------
def inject_css() -> None:
    """Load assets/style.css with colour variables for the current theme."""
    try:
        theme = st.context.theme.type or "light"
    except Exception:
        theme = "light"
    variables = "".join(f"--rd-{k}:{v};" for k, v in THEME_VARS.get(theme, THEME_VARS["light"]).items())
    css = CSS_FILE.read_text(encoding="utf-8") if CSS_FILE.exists() else ""
    st.html(f"<style>:root{{{variables}}}\n{css}</style>")


def esc(text: str) -> str:
    """Escape model text for HTML, then allow **bold** only."""
    safe = html.escape(text or "")
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)


def with_citations(text: str, sources: dict[int, dict]) -> str:
    """Escape text and turn [3] or [2, 5] into links that open the source in a new tab."""
    def link(number: int) -> str:
        source = sources.get(number)
        if not source:
            return ""
        tip = html.escape(f"[{number}] {source['title']} · {source['domain']} · Tier {source['tier']}")
        return (f'<a class="rd-cite" href="{html.escape(source["url"])}" target="_blank" rel="noopener" '
                f'title="{tip}">[{number}]</a>')

    def group(match: re.Match) -> str:
        return "".join(link(int(n)) for n in re.findall(r"\d+", match.group(1)))

    return re.sub(r"\[(\d+(?:\s*,\s*\d+)*)\]", group, esc(text))


def slug(text: str) -> str:
    return "rd-" + re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50]


def notice(title: str, body: str = "", kind: str = "warn", items: list[str] | None = None) -> None:
    """A designed notice (instead of Streamlit's default coloured boxes)."""
    extra = f"<p style='margin:0'>{esc(body)}</p>" if body else ""
    if items:
        extra += "<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>"
    css = {"warn": "", "error": " rd-notice--error", "quiet": " rd-notice--quiet"}[kind]
    st.html(f"<div class='rd-notice{css}' role='{'alert' if kind == 'error' else 'note'}'>"
            f"<strong>{esc(title)}</strong>{extra}</div>")


def error_notice(error: AppError) -> None:
    """What happened and what to do. Details stay in the logs."""
    notice(error.title, error.action, kind="error")


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
def sidebar_history(history: list[dict], on_open, on_clear) -> None:
    """Earlier reports in this browser session; reopening never re-runs research."""
    st.sidebar.markdown("**History**")
    if not history:
        st.sidebar.caption("Reports you create appear here. History lasts until you close or refresh this tab.")
        return
    box = st.sidebar.container(key="history")
    for item in reversed(history):
        report = item["report"]
        label = report["metadata"].get("topic", report["title"])
        time = (report["metadata"].get("created_at") or "")[11:16]
        box.button(label[:60] + ("…" if len(label) > 60 else ""), key=f"hist-{report['id']}",
                          help=f"{report['metadata'].get('mode_label', '')} · {time} UTC", on_click=on_open,
                          args=(report["id"],), use_container_width=True, type="tertiary")
    st.sidebar.caption("Kept until you close or refresh this tab.")
    st.sidebar.button("Clear history", on_click=on_clear, key="clear-history")


def sidebar_usage() -> None:
    """Rough token use for this app process (shared by everyone using this deployment)."""
    used = LIMITER.used_today
    st.sidebar.caption(f"Groq tokens today (estimate, this app): {used:,} of {GROQ_TPD_LIMIT:,}")
    st.sidebar.progress(min(1.0, used / GROQ_TPD_LIMIT))


# ---------------------------------------------------------------------------
# Research input
# ---------------------------------------------------------------------------
def research_form(state: dict) -> dict | None:
    """Topic, mode and options. Returns the request when the button is pressed."""
    st.html("<div class='rd-wordmark'>Research Desk</div>"
            "<p class='rd-lede'>Enter a topic and an agent searches the web, reads the most credible pages it can "
            "reach, and writes a report where every claim points to a numbered source. It can be wrong: tiers are "
            "heuristic, free search is limited, and anything it couldn't verify is listed as such.</p>")

    mode_key = st.pills("Research mode", options=list(MODES), format_func=lambda k: MODES[k]["label"],
                        key="mode", default=state.get("mode", "general"), selection_mode="single") or "general"
    st.html(f"<p class='rd-help'>{esc(MODES[mode_key]['tagline'])}</p>")

    topic = st.text_area("Topic or question", key="topic", height=96, max_chars=400,
                         placeholder="e.g. " + MODES[mode_key]["examples"][0])

    st.caption("Examples for this mode")
    example = st.pills("Examples", MODES[mode_key]["examples"], key=f"examples-{mode_key}",
                       label_visibility="collapsed", selection_mode="single")
    if example and example != state.get("last_example"):
        state["last_example"] = example
        st.session_state["topic_pending"] = example
        st.rerun()

    left, middle, right = st.columns(3)
    with left:
        depth = st.segmented_control("Depth", list(DEPTHS), default="Standard", key="depth") or "Standard"
        d = DEPTHS[depth]
        st.caption(f"{d['queries']} queries · reads {d['pages_to_read']} pages · {d['estimate']}")
    with middle:
        length = st.segmented_control("Report length", list(LENGTHS), default="Standard", key="length") or "Standard"
        st.caption(LENGTHS[length]["words"])
    with right:
        focus = st.segmented_control("Time focus", list(TIME_FOCUS), default="Any time", key="focus") or "Any time"
        st.caption(TIME_FOCUS[focus]["help"])

    with st.expander("More options"):
        background = st.checkbox(
            "Add a background note from the model's general knowledge (not researched)", key="background",
            help="Off by default. Shown in a separate, clearly labelled section and never cited.")

    clicked = st.button("Research this topic", type="primary", key="go")
    if clicked:
        clean = " ".join((topic or "").split())
        if len(clean) < 8 or len(clean.split()) < 2:
            notice("Please write a slightly longer topic.",
                   "At least a few words helps the agent plan good searches, e.g. "
                   "'How do heat pumps work in cold climates?'", kind="quiet")
            return None
        return {"topic": clean, "mode_key": mode_key, "depth_name": depth, "length_name": length,
                "time_focus": focus, "include_background": background}
    return None


def progress_reporter(status):
    """Return a progress(stage, message) function that writes real backend steps into st.status."""
    seen = []

    def report(stage: str, message: str) -> None:
        label = STAGES.get(stage, stage.title())
        if stage != "waiting":
            status.update(label=f"{label} …" if stage != "done" else "Done")
        if not seen or seen[-1] != message:
            seen.append(message)
            status.markdown(f"<span style='font-family:IBM Plex Mono,monospace;font-size:.78rem;opacity:.7'>"
                            f"{label.upper()}</span>&nbsp; {esc(message)}", unsafe_allow_html=True)
    return report


# ---------------------------------------------------------------------------
# Report view
# ---------------------------------------------------------------------------
def report_header(report: dict) -> None:
    meta = report["metadata"]
    if report["kind"] == "document":
        bits = [meta.get("source_name", ""), meta.get("output_type", ""), meta.get("length", ""),
                (meta.get("created_at") or "")[:16].replace("T", " ") + " UTC"]
    else:
        bits = [meta.get("mode_label", ""), f"{meta.get('depth', '')} depth", meta.get("time_focus", ""),
                f"{len(report['sources'])} sources", f"{meta.get('duration_seconds', '?')} s",
                (meta.get("created_at") or "")[:16].replace("T", " ") + " UTC"]
        if meta.get("from_cache"):
            bits.append("from cache")
    st.html(f"<h1 class='rd-title'>{esc(report['title'])}</h1>"
            f"<div class='rd-meta'>{''.join(f'<span>{esc(b)}</span>' for b in bits if b)}</div>")


def report_html(report: dict, detail: str = "Standard") -> tuple[str, list[tuple[str, str]]]:
    """The report as HTML, built from exporters.report_blocks so it matches every export.

    Returns (html, contents) where contents is [(anchor, heading)] for the table of contents.
    """
    sources = {s["id"]: s for s in report["sources"]}
    parts, contents, box = ["<div class='rd-report'>"], [], None

    def close_box():
        nonlocal box
        if box:
            parts.append("</div>")
            box = None

    for block in exporters.report_blocks(report, detail):
        kind = block["type"]
        if kind == "notice":
            continue                                # shown above the report instead
        if kind == "heading":
            close_box()
            anchor = slug(block["text"])
            if block["level"] == 1:
                contents.append((anchor, block["text"]))
            tag = "h2" if block["level"] == 1 else "h3"
            parts.append(f"<{tag} id='{anchor}'>{esc(block['text'])}</{tag}>")
            if block["text"].startswith("Could Not Verify"):
                parts.append("<div class='rd-box rd-box--verify'>")
                box = "verify"
            elif block["text"].startswith("Limitations"):
                parts.append("<div class='rd-box'>")
                box = "limits"
        elif kind == "para":
            parts.append(f"<p>{with_citations(block['text'], sources)}</p>")
        elif kind == "label":
            parts.append(f"<span class='rd-tag rd-tag--added'>{esc(block['text'])}</span>")
        elif kind == "list":
            parts.append("<ul>" + "".join(f"<li>{with_citations(i, sources)}</li>" for i in block["items"]) + "</ul>")
        elif kind == "labelled":
            parts.append(f"<p><strong>{esc(block['label'])}.</strong> {with_citations(block['text'], sources)}</p>")
        elif kind == "finding":
            verdict = f" · Web check: {esc(block['verdict'])}" if block.get("verdict") else ""
            parts.append(
                f"<div class='rd-finding'><h3 id='{slug('finding-' + str(block['number']))}'>{block['number']}. "
                f"{esc(block['claim'])}</h3>"
                f"<span class='rd-label'>Evidence · what sources say</span>"
                f"<div class='rd-evidence'><p>{with_citations(block['evidence'], sources)}</p></div>"
                + (f"<span class='rd-label rd-label--accent'>Interpretation · analysis, not stated by sources</span>"
                   f"<div class='rd-interp'><p>{with_citations(block['interpretation'], sources)}</p></div>"
                   if block.get("interpretation") else "")
                + f"<p class='rd-confline'><span class='rd-tag rd-tag--{block['confidence']}'>"
                  f"Confidence: {block['confidence']}</span>{esc(block.get('confidence_reason', ''))}{verdict} "
                  f"· {with_citations(' '.join(f'[{n}]' for n in block['citations']), sources)}</p></div>")
        elif kind == "table":
            head = "".join(f"<th scope='col'>{esc(h)}</th>" for h in block["header"])
            rows = "".join("<tr>" + "".join(f"<td>{with_citations(str(c), sources)}</td>" for c in row) + "</tr>"
                           for row in block["rows"])
            parts.append(f"<div class='rd-table-wrap'><table class='rd-table'><thead><tr>{head}</tr></thead>"
                         f"<tbody>{rows}</tbody></table></div>")
        elif kind == "sources":
            close_box()
            if block["intro"]:
                parts.append(f"<p class='rd-confline'>{esc(block['intro'])}</p>")
            parts.append("<ol class='rd-sources'>" + "".join(source_item(s) for s in block["items"]) + "</ol>")
    close_box()
    parts.append("</div>")
    return "".join(parts), contents


def source_item(s: dict) -> str:
    preprint = " · preprint" if s.get("preprint") else ""
    added = " · added by follow-up" if s.get("added_by_followup") else ""
    return (f"<li id='src-{s['id']}'><span class='rd-num'>[{s['id']}]</span><span>"
            f"<a href='{html.escape(s['url'])}' target='_blank' rel='noopener'>{esc(s['title'])}</a>"
            f"<span class='rd-src-meta'>{esc(s['domain'])} · Tier {s['tier']} · {esc(s.get('date') or 'date unknown')}"
            f" · {esc(s['read_status'])}{preprint}{added}</span></span></li>")


def contents_html(contents: list[tuple[str, str]]) -> str:
    return "<ol>" + "".join(f"<li><a href='#{a}'>{esc(t)}</a></li>" for a, t in contents) + "</ol>"


def side_column(report: dict, contents: list[tuple[str, str]]) -> None:
    """Contents and a compact list of cited sources, beside the reading column."""
    cited = [s for s in report["sources"] if s.get("cited")][:15]
    items = "".join(f"<li><span class='rd-num'>[{s['id']}]</span><a href='{html.escape(s['url'])}' target='_blank' "
                    f"rel='noopener'>{esc(s['title'][:70])}</a><span class='rd-src-meta'>{esc(s['domain'])} · "
                    f"Tier {s['tier']}</span></li>" for s in cited)
    st.html(f"<div class='rd-side'><span class='rd-label'>Contents</span>{contents_html(contents)}"
            f"<span class='rd-label'>Cited sources</span><ul>{items or '<li>None</li>'}</ul>"
            "<p class='rd-src-meta'>Tiers are heuristic: they describe the type of site, not whether a claim is true."
            "</p></div>")


def scorecard(report: dict) -> None:
    """Compact, expandable quality scorecard. Metrics are never combined into one number."""
    card = report.get("scorecard")
    if not card:
        return
    graded = [m for m in card["metrics"] if m["status"] in ("good", "fair", "poor")]
    counts = {s: sum(1 for m in graded if m["status"] == s) for s in ("good", "fair", "poor")}
    title = f"Quality checks: {counts['good']} good · {counts['fair']} fair · {counts['poor']} need attention"
    with st.expander(title, icon=":material/fact_check:"):
        rows = "".join(
            f"<tr><td><strong>{esc(m['name'])}</strong>{' ·&nbsp;mode focus' if m['focus'] else ''}</td>"
            f"<td>{esc(m['display'])}</td><td><span class='rd-tag rd-tag--"
            f"{ {'good': 'High', 'fair': 'Medium', 'poor': 'Low'}.get(m['status'], 'tier') }'>{m['status']}</span></td>"
            f"<td>{esc(m['target'])}</td></tr><tr><td colspan='4' class='rd-src-meta' style='border-bottom-width:1px'>"
            f"{esc(m['explanation'])}</td></tr>" for m in card["metrics"])
        st.html(f"<div class='rd-table-wrap'><table class='rd-table'><thead><tr><th>Check</th><th>Result</th>"
                f"<th>Status</th><th>Good looks like</th></tr></thead><tbody>{rows}</tbody></table></div>"
                f"<p class='rd-src-meta'>{esc(card['note'])}</p>")


def research_details(report: dict) -> None:
    """Queries, search statistics, citation checks and run metadata."""
    meta = report["metadata"]
    with st.expander("Research details", icon=":material/manage_search:"):
        if meta.get("queries"):
            st.markdown("**Queries the agent planned**")
            st.markdown("\n".join(f"{i}. {q}" for i, q in enumerate(meta["queries"], 1)))
        stats = meta.get("search_stats")
        if stats:
            st.markdown("**Search and reading**")
            st.markdown(
                f"- {stats['queries_run']} searches, {stats['search_errors']} failed\n"
                f"- {stats['results_returned']} results returned, {stats['duplicates_removed']} duplicates removed, "
                f"{len(stats['excluded'])} excluded\n"
                f"- {stats['pages_read']} of {stats['pages_attempted']} pages read in full")
            if stats["page_failures"]:
                st.markdown("Pages that couldn't be read: " + "; ".join(
                    f"`{f['url'][:70]}` ({f['reason']})" for f in stats["page_failures"][:8]))
            if stats["excluded"]:
                st.markdown("Excluded: " + "; ".join(f"`{e['url'][:60]}` ({e['reason']})" for e in stats["excluded"][:8]))
        st.markdown("**Citation checks**")
        if report["flags"]:
            st.markdown("\n".join(f"- {f['where']}: {f['problem']} → {f['action']}" for f in report["flags"]))
        else:
            st.markdown("Every citation matched a source in the registry. No URLs outside the registry were found.")
        st.caption(f"Model: {meta.get('model')} · tokens used (estimate): {meta.get('tokens_estimate', '?')} · "
                   f"created {meta.get('created_at')}")


# ---------------------------------------------------------------------------
# Export panel
# ---------------------------------------------------------------------------
EXPORT_FORMATS = {"Markdown": ("md", "text/markdown"),
                  "Word": ("docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
                  "PDF": ("pdf", "application/pdf"),
                  "PowerPoint": ("pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation")}


@st.fragment
def export_panel(report: dict, make_outline) -> None:
    """Choose format and length, prepare, download. Runs as a fragment so it never re-runs research."""
    st.markdown("**Export**")
    files = st.session_state.setdefault("exports", {})
    fmt = st.segmented_control("Format", list(EXPORT_FORMATS), default="PDF", key=f"fmt-{report['id']}") or "PDF"
    if fmt == "PowerPoint":
        count = st.slider("Number of slides", 5, 16, 10, key=f"slides-{report['id']}",
                          help="Includes the title, agenda and sources slides.")
        detail = f"{count} slides"
        st.caption("The agent writes the slide outline (one short call); code draws the slides. "
                   "Speaker notes carry the detail and citations.")
    else:
        detail = st.segmented_control("Length", exporters.DETAIL_LEVELS, default="Standard",
                                      key=f"detail-{report['id']}") or "Standard"
        st.caption({"Summary": "Summary, key findings, what couldn't be verified, and cited sources.",
                    "Standard": "The full report.",
                    "Full + research details": "The full report plus queries, citation checks and the scorecard."}[detail])
    key = f"{report['id']}|{fmt}|{detail}|{len(report.get('addenda', []))}"
    extension, mime = EXPORT_FORMATS[fmt]
    if key not in files:
        if st.button(f"Prepare {fmt} file", key=f"prep-{key}", icon=":material/description:"):
            try:
                with st.spinner("Preparing the file …"):
                    if fmt == "Markdown":
                        files[key] = exporters.make_markdown(report, detail).encode("utf-8")
                    elif fmt == "Word":
                        files[key] = exporters.make_docx(report, detail)
                    elif fmt == "PDF":
                        files[key] = exporters.make_pdf(report, detail)
                    else:
                        files[key] = exporters.make_pptx(report, make_outline(report, int(detail.split()[0])))
                st.rerun(scope="fragment")
            except AppError as error:
                error_notice(error)
    if key in files:
        st.download_button(f"Download {fmt}", data=files[key], file_name=exporters.file_name(report, extension),
                           mime=mime, type="primary", key=f"dl-{key}", on_click="ignore", icon=":material/download:")
    st.caption("Limits: no images or charts. PDFs embed their fonts; Word and PowerPoint use Georgia and Arial. "
               "In Word, right-click the contents and choose Update Field to fill in page numbers.")


# ---------------------------------------------------------------------------
# Follow-up chat
# ---------------------------------------------------------------------------
@st.fragment
def followup_panel(report: dict, ask, add_to_report) -> None:
    """Chat about the report. Answers cite the registry; an answer can be added to the report."""
    st.markdown("**Ask a follow-up question**")
    st.caption("Answers use this report's sources first. If they don't cover the question, a small extra search "
               "can add new sources, and the answer says so.")
    chats = st.session_state.setdefault("chats", {}).setdefault(report["id"], [])
    sources = {s["id"]: s for s in report["sources"]}
    for index, item in enumerate(chats):
        with st.chat_message("user", avatar=":material/person:"):
            st.markdown(esc(item["question"]), unsafe_allow_html=True)
        with st.chat_message("assistant", avatar=":material/menu_book:"):
            if item.get("error"):
                notice(item["error"].title, item["error"].action, kind="error")
                continue
            st.html(f"<div class='rd-report' style='font-size:1rem'><p>{with_citations(item['answer'], sources)}</p></div>")
            if item["searched"]:
                st.caption(f"Used an additional web search; added source(s) "
                           f"{', '.join(f'[{n}]' for n in item['new_sources'])} to the registry.")
            added = any(a["question"] == item["question"] for a in report["addenda"])
            if added:
                st.caption("Added to the report as an addendum.")
            elif st.button("Add to report", key=f"add-{report['id']}-{index}", type="secondary",
                           icon=":material/playlist_add:"):
                add_to_report(report, item)
                st.rerun()              # full rerun so the report shows the addendum
    allow = st.checkbox("Allow a small extra web search if needed", value=True, key=f"allow-{report['id']}")
    question = st.chat_input("Ask about this report", key=f"chat-{report['id']}")
    if question:
        with st.status("Answering …", expanded=False) as status:
            try:
                chats.append(ask(report, question, [c for c in chats if not c.get("error")], allow,
                                 progress_reporter(status)))
                status.update(label="Answered", state="complete")
            except AppError as error:
                chats.append({"question": question, "error": error})
                status.update(label="Couldn't answer", state="error")
        st.rerun(scope="fragment")
