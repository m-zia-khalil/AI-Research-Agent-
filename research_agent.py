"""The single CrewAI agent and every job it does.

Design ("code collects, agent thinks"):
- search_tools.py collects and numbers sources. The agent never searches,
  never sees a URL it could copy wrongly, and has no tools.
- The agent gets short, budgeted excerpts labelled [1], [2]... and returns
  JSON matching schema.py. Code validates it, checks every citation and
  assembles the final report dict that the UI and exporters use.

One agent, several small tasks: Groq's free tier allows ~8K tokens per minute,
so a long report is written in 1-3 small calls (findings, then analysis)
instead of one huge one. Each call is a Crew with our one agent and one task.

Public functions:
  run_research()      topic -> report dict
  run_followup()      question about a report -> answer dict
  add_addendum()      put a follow-up answer into the report
  make_slide_outline() report -> slide outline for exporters.make_pptx()
  run_document_job()  your own document -> report dict (kind="document")
  judge_faithfulness() optional evaluation: do cited sources support claims?
"""

from __future__ import annotations

import copy
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Callable, TypeVar

from pydantic import BaseModel

import schema
from modes import DEPTHS, LENGTHS, TIME_FOCUS, get_mode
from search_tools import collect_sources, search_more, web_search, fetch_page, classify_tier, domain_of, \
    best_excerpt, normalize_url, TIER_LABELS
from utils import (LIMITER, SAFE_CALL_TOKENS, AppError, cache_get, cache_set, cited_numbers, estimate_tokens,
                   extract_json, friendly_error, get_logger, get_secret, trim_to_tokens, verify_citations)

log = get_logger(__name__)

MODEL = "groq/openai/gpt-oss-120b"     # verified Oct 2026: "groq/" prefix + Groq model ID
REPORT_CACHE_SECONDS = 6 * 60 * 60     # identical research requests reuse results for 6 hours

# (stage, message). Stages: planning, searching, reading, writing, waiting, checking, done
ProgressFn = Callable[[str, str], None] | None
M = TypeVar("M", bound=BaseModel)

# For tests: set to a function(max_tokens) -> LLM to use a fake model (see tests/).
LLM_FACTORY: Callable[[int], object] | None = None


# ---------------------------------------------------------------------------
# The one agent
# ---------------------------------------------------------------------------
AGENT_ROLE = "Careful research analyst"
AGENT_GOAL = "Turn numbered source excerpts into accurate, well-cited, clearly structured research."
AGENT_BACKSTORY = """You work only from the numbered sources you are given. Rules you never break:
1. Cite only with source numbers in square brackets, e.g. [3] or [2, 5]. Never write URLs. Never invent sources, studies, statistics, quotes or citation numbers.
2. You have not visited any website; you only see the excerpts provided. Never claim otherwise.
3. Evidence = what sources actually say, attributed and cited. Interpretation = your own analysis, clearly separate.
4. Prefer claims backed by several independent sources. Say when only one source supports something.
5. If the sources do not support something, list it as could-not-verify instead of guessing.
6. Never present assumptions as facts. State uncertainty plainly.
7. No false balance: if evidence strongly favours one position, say so.
8. Tier 3 sources are weaker and preprints are not peer-reviewed: say so when you rely on them.
9. Reply with exactly one JSON object and nothing else."""


def make_llm(max_tokens: int):
    """Create the Groq LLM for one call. max_tokens caps the answer length."""
    if LLM_FACTORY is not None:
        return LLM_FACTORY(max_tokens)
    from crewai import LLM

    key = get_secret("GROQ_API_KEY")
    if not key:
        raise AppError("missing_key")
    return LLM(
        model=MODEL,
        api_key=key,
        temperature=0.2,          # low = more factual, less creative
        max_tokens=max_tokens,
        timeout=120,
        reasoning_effort="low",   # gpt-oss "thinking" tokens count as output; keep them small
    )


def make_agent(max_tokens: int):
    """Our single agent. Same role, goal and rules every time; only max_tokens varies."""
    from crewai import Agent

    return Agent(
        role=AGENT_ROLE,
        goal=AGENT_GOAL,
        backstory=AGENT_BACKSTORY,
        llm=make_llm(max_tokens),
        tools=[],                 # no tools: Python does the collecting
        allow_delegation=False,   # single agent, nobody to delegate to
        max_iter=2,
        verbose=False,
    )


def _retry_seconds(error_text: str) -> float:
    """Read 'try again in 12.3s' / '850ms' / '1m2s' from a Groq rate-limit message."""
    match = re.search(r"try again in (?:(\d+)m)?([\d.]+)(ms|s)", error_text)
    if not match:
        return 20.0
    minutes = float(match.group(1) or 0)
    value = float(match.group(2)) / (1000 if match.group(3) == "ms" else 1)
    return minutes * 60 + value + 1


def call_agent(description: str, shape: str, model: type[M], max_tokens: int,
               progress: ProgressFn = None, label: str = "Writing") -> M:
    """Run one task with our agent and return a validated Pydantic object.

    Handles: per-minute token pacing, Groq rate limits (waits and retries),
    malformed JSON (repairs, then asks once more with the error), and turns
    every other failure into a friendly AppError.
    """
    from crewai import Crew, Process, Task

    say = progress or (lambda stage, message: None)
    expected = f"Only this JSON object, filled in:\n{shape}"
    prompt_tokens = estimate_tokens(AGENT_BACKSTORY + description + expected) + 150
    if prompt_tokens + max_tokens > SAFE_CALL_TOKENS + 400:
        log.warning("Call '%s' is large: ~%d prompt + %d output tokens", label, prompt_tokens, max_tokens)

    extra = ""
    for attempt in range(1, 5):
        LIMITER.wait_for(prompt_tokens + max_tokens,
                         on_wait=lambda s: say("waiting", f"Pausing {s:.0f} s to stay within Groq's free per-minute limit …"))
        try:
            agent = make_agent(max_tokens)
            task = Task(description=description + extra, expected_output=expected, agent=agent)
            result = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False).kickoff()
        except AppError:
            raise
        except Exception as error:
            text = f"{type(error).__name__}: {error}"
            lowered = text.lower()
            LIMITER.record(prompt_tokens)          # a failed call may still have counted
            if ("ratelimit" in lowered or "rate limit" in lowered or "429" in lowered) and "per day" not in lowered \
                    and "tpd" not in lowered and attempt < 4:
                wait = _retry_seconds(text)
                say("waiting", f"Groq rate limit reached; retrying in {wait:.0f} s …")
                log.warning("Rate limited on '%s' (attempt %d): %s", label, attempt, text[:300])
                time.sleep(wait)
                continue
            raise friendly_error(error)

        usage = getattr(result, "token_usage", None)
        used = getattr(usage, "total_tokens", 0) or (prompt_tokens + estimate_tokens(result.raw or ""))
        LIMITER.record(used)
        log.info("Call '%s' used ~%d tokens", label, used)
        try:
            return model.model_validate(extract_json(result.raw or ""))
        except Exception as error:
            if extra:   # already retried once with the error message
                log.error("Unparseable output for '%s': %s", label, (result.raw or "")[:500])
                raise AppError("bad_output", str(error))
            log.warning("Bad JSON for '%s', retrying once: %s", label, error)
            say("writing", "The answer wasn't valid JSON; asking the agent to fix it …")
            extra = (f"\n\nIMPORTANT: your previous answer could not be parsed ({str(error)[:200]}). "
                     "Return ONLY the JSON object, with no text before or after it.")
    raise AppError("rate_limit", "gave up after repeated rate limits")


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------
def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def format_sources(sources: list[dict], budget_tokens: int) -> str:
    """Write sources as a numbered list for the agent, fitting a token budget.

    Full-text sources get most of the budget; snippet-only sources get a line.
    """
    if not sources:
        return "(no sources)"
    full = [s for s in sources if s["read_status"] != "snippet"]
    header_cost = 30 * len(sources)
    snippet_cost = 70 * (len(sources) - len(full))
    per_full = max(120, (budget_tokens - header_cost - snippet_cost) // max(1, len(full)))
    blocks = []
    for s in sources:
        tags = [s["domain"], f"Tier {s['tier']}", s.get("date") or "date unknown", s["read_status"]]
        if s.get("preprint"):
            tags.append("preprint, not peer-reviewed")
        limit = per_full if s["read_status"] != "snippet" else 70
        blocks.append(f"[{s['id']}] {s['title']} ({' · '.join(tags)})\n{trim_to_tokens(s['text'] or s['snippet'], limit)}")
        s["shown_to_agent"] = True
    return "\n\n".join(blocks)


def _budget_for_sources(fixed_prompt: str, max_tokens: int) -> int:
    """Tokens left for source excerpts in one call, after instructions and answer."""
    fixed = estimate_tokens(AGENT_BACKSTORY + fixed_prompt) + 400   # + JSON shape + CrewAI framing
    return max(800, SAFE_CALL_TOKENS - max_tokens - fixed)


# ---------------------------------------------------------------------------
# Research
# ---------------------------------------------------------------------------
def plan_queries(topic: str, mode: dict, depth: dict, time_focus: str, progress: ProgressFn = None) -> list[str]:
    """Agent call 1: plan focused search queries for this topic and mode."""
    count = depth["queries"]
    description = (
        f"Plan {count} web search queries to research this topic: \"{topic}\"\n"
        f"Research mode: {mode['label']}. {mode['tagline']}\n"
        f"Cover these angles where relevant: {', '.join(mode['query_angles'])}; "
        "include at least one query for opposing views or criticism and one for evidence or data.\n"
        f"Time focus: {time_focus}. Today is {_today()}.\n"
        "Each query: 3-10 words, specific, no quotes or operators. Different queries must not overlap much."
    )
    plan = call_agent(description, schema.QUERY_PLAN_SHAPE, schema.QueryPlan, 700, progress, "Planning")
    queries = plan.queries[:count]
    if len(queries) < 2:            # fall back to simple queries rather than fail
        queries = [topic] + [f"{topic} {angle}" for angle in mode["query_angles"][: count - 1]]
    return queries


def _writing_rules(mode: dict, length: dict, include_background: bool) -> str:
    extra = "; ".join(f"'{x['heading']}': {x['instruction']}" for x in mode["extra_sections"]) or "none"
    background = (
        "background_note: up to 80 words of general background knowledge NOT from the sources, no citations. "
        "It will be shown separately and labelled 'not researched'."
        if include_background else "background_note: leave empty.")
    return (
        f"Mode: {mode['label']}. Style: {mode['writing_style']}\n"
        f"Target length: {length['words']}. Today is {_today()}.\n"
        f"Mode-specific extra_sections to include: {extra}.\n{background}"
    )


def write_report(topic: str, mode: dict, depth: dict, length: dict, sources: list[dict],
                 include_background: bool, progress: ProgressFn = None) -> dict:
    """Agent writing calls. Returns the merged draft as a dict.

    Quick: one call writes everything.
    Standard: findings call, then analysis call.
    Deep: two findings calls over different halves of the sources, then analysis.
    """
    say = progress or (lambda stage, message: None)
    rules = _writing_rules(mode, length, include_background)
    max_tokens = length["max_tokens"]
    visible = [s for s in sources if s["text"]][: depth["pages_to_read"] + 8]

    if depth["writing_calls"] == 1:
        say("writing", "Writing the report …")
        task = (f"Write a research report on: \"{topic}\"\n{rules}\n"
                f"Key findings: {length['findings']}. Detailed analysis sections: {length['analysis_sections']}.\n"
                "SOURCES:\n{sources}")
        budget = _budget_for_sources(task, max_tokens)
        draft = call_agent(task.replace("{sources}", format_sources(visible, budget)), schema.REPORT_SHAPE,
                           schema.ReportDraft, max_tokens, progress, "Writing report")
        return draft.model_dump()

    # Findings: one call (Standard) or two calls over different sources (Deep).
    groups = [visible] if depth["writing_calls"] == 2 else [visible[0::2], visible[1::2]]
    low, high = (int(x) for x in length["findings"].split("-"))
    per_group = f"{max(2, low // len(groups))}-{max(3, high // len(groups))}"
    findings: list[dict] = []
    unverified: list[dict] = []
    title = ""
    for number, group in enumerate(groups, start=1):
        say("writing", f"Writing key findings ({number}/{len(groups)}) …")
        task = (f"Topic: \"{topic}\"\n{rules}\n"
                f"Write {per_group} key findings from these sources. For each: claim, evidence (cited), "
                "interpretation (your analysis), confidence with reason, citations.\n"
                "SOURCES:\n{sources}")
        budget = _budget_for_sources(task, max_tokens)
        part = call_agent(task.replace("{sources}", format_sources(group, budget)), schema.FINDINGS_SHAPE,
                          schema.FindingsPart, max_tokens, progress, f"Findings {number}")
        title = title or part.title
        findings += [f.model_dump() for f in part.key_findings]
        unverified += [u.model_dump() for u in part.could_not_verify]

    # Analysis: sees the findings plus shorter excerpts of every visible source.
    say("writing", "Writing analysis, perspectives and conclusion …")
    finding_lines = "\n".join(
        f"F{i}. {f['claim']} (confidence {f['confidence']}; sources {f['citations']})" for i, f in enumerate(findings, 1))
    task = (f"Topic: \"{topic}\"\n{rules}\n"
            f"Detailed analysis sections: {length['analysis_sections']}.\n"
            f"KEY FINDINGS ALREADY WRITTEN (do not repeat them; build on them):\n{finding_lines}\n\n"
            "Now write the summary, introduction and scope, detailed analysis, the main perspectives "
            "(who holds each, its evidence, its strength), agreement, disagreement, weight of evidence, "
            "could-not-verify items, limitations of this research, and conclusion.\n"
            "SOURCES:\n{sources}")
    budget = _budget_for_sources(task, max_tokens)
    analysis = call_agent(task.replace("{sources}", format_sources(visible, budget)), schema.ANALYSIS_SHAPE,
                          schema.AnalysisPart, max_tokens, progress, "Analysis").model_dump()
    analysis["title"] = title
    analysis["key_findings"] = findings
    analysis["could_not_verify"] = unverified + analysis.get("could_not_verify", [])
    return analysis


def _warnings(sources: list[dict], stats: dict, mode_key: str, time_focus: str) -> list[str]:
    """Plain-language notices shown above the report."""
    notes = []
    full = sum(1 for s in sources if s["read_status"] == "full text")
    if full == 0:
        notes.append("No page could be read in full; the report relies on search snippets and abstracts only.")
    elif full < 3:
        notes.append(f"Only {full} page(s) could be read in full; the rest are snippets or abstracts.")
    if not any(s["tier"] in (1, 2) for s in sources):
        notes.append("No Tier 1 or Tier 2 sources were found. Treat every finding with extra caution.")
    if stats.get("search_errors"):
        notes.append(f"{stats['search_errors']} of {stats['queries_run']} searches failed (likely rate limiting), "
                     "so coverage may be thinner than usual.")
    if mode_key == "market":
        notes.append("Free market data is limited. Figures may be estimates or out of date; check the dates in Sources.")
    if mode_key == "current" or time_focus == "Recent news":
        notes.append("Developing stories change quickly. Check publication dates before relying on details.")
    if any(s.get("preprint") for s in sources):
        notes.append("Some sources are preprints, which have not been peer-reviewed.")
    return notes


def run_research(topic: str, mode_key: str = "general", depth_name: str = "Standard",
                 length_name: str = "Standard", time_focus: str = "Any time",
                 include_background: bool = False, progress: ProgressFn = None,
                 use_cache: bool = True) -> dict:
    """Research a topic end to end and return the report dict.

    Raises AppError with a friendly message on any failure. Never writes a
    report without sources.
    """
    say = progress or (lambda stage, message: None)
    topic = " ".join((topic or "").split())
    if len(topic) < 8 or len(topic.split()) < 2:
        raise AppError("topic", f"topic was {topic!r}")
    mode, depth, length = get_mode(mode_key), DEPTHS[depth_name], LENGTHS[length_name]
    focus = TIME_FOCUS[time_focus]

    cache_key = f"report|{topic.lower()}|{mode_key}|{depth_name}|{length_name}|{time_focus}|{include_background}"
    if use_cache:
        cached = cache_get(cache_key, REPORT_CACHE_SECONDS)
        if cached is not None:
            say("done", "Loaded from cache (same request in the last 6 hours). No tokens used.")
            report = copy.deepcopy(cached)        # a copy, so later edits (addenda) don't change the cache
            report["id"] = uuid.uuid4().hex[:10]
            report["metadata"]["from_cache"] = True
            return report

    started = time.time()
    tokens_before = LIMITER.used_today
    try:
        say("planning", "Planning search queries …")
        queries = plan_queries(topic, mode, depth, time_focus, progress)
        say("planning", "Queries: " + " · ".join(queries))

        def search_progress(message: str) -> None:
            say("reading" if message.startswith(("Reading", "Read ")) else "searching", message)

        sources, stats = collect_sources(topic, queries, mode, depth, focus, search_progress)
        draft = write_report(topic, mode, depth, length, sources, include_background, progress)

        say("checking", "Checking every citation against the source registry …")
        report = assemble_report(draft, sources, kind="research")
        report["metadata"].update({
            "topic": topic, "mode": mode_key, "mode_label": mode["label"], "depth": depth_name,
            "length": length_name, "time_focus": time_focus, "include_background": include_background,
            "queries": queries, "search_stats": stats, "duration_seconds": round(time.time() - started),
            "tokens_estimate": LIMITER.used_today - tokens_before,
        })
        report["warnings"] = _warnings(sources, stats, mode_key, time_focus) + report["warnings"]
        from evaluation import compute_scorecard
        report["scorecard"] = compute_scorecard(report)
    except AppError:
        raise
    except Exception as error:
        raise friendly_error(error)

    cache_set(cache_key, copy.deepcopy(report))
    say("done", f"Done in {report['metadata']['duration_seconds']} s.")
    return report


def assemble_report(draft: dict, sources: list[dict], kind: str) -> dict:
    """Combine the agent's draft with code-made parts and run the citation checks."""
    report = {
        "id": uuid.uuid4().hex[:10], "kind": kind,
        "metadata": {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": MODEL},
        "title": draft.get("title") or "Research report",
        "executive_summary": draft.get("executive_summary", ""),
        "introduction_and_scope": draft.get("introduction_and_scope", ""),
        "key_findings": draft.get("key_findings", []),
        "detailed_analysis": draft.get("detailed_analysis", []),
        "perspectives": draft.get("perspectives", []),
        "agreement": draft.get("agreement", ""), "disagreement": draft.get("disagreement", ""),
        "weight_of_evidence": draft.get("weight_of_evidence", ""),
        "extra_sections": draft.get("extra_sections", []),
        "could_not_verify": draft.get("could_not_verify", []),
        "limitations": draft.get("limitations", []),
        "conclusion": draft.get("conclusion", ""),
        "background_note": draft.get("background_note", ""),
        "addenda": [], "sources": sources, "warnings": [], "flags": [],
    }
    # Fill citations lists from [n] markers written in the text, if the agent forgot.
    for item in report["key_findings"]:
        item["citations"] = sorted(set(item.get("citations", [])) | set(cited_numbers(item.get("evidence", ""))))
    for item in report["detailed_analysis"] + report["perspectives"] + report["extra_sections"]:
        text = item.get("body", "") + item.get("evidence", "")
        item["citations"] = sorted(set(item.get("citations", [])) | set(cited_numbers(text)))

    report["flags"] = verify_citations(report, sources)

    # A key finding with no valid citation is not a finding: move it to Could Not Verify.
    kept = []
    for item in report["key_findings"]:
        if item["citations"]:
            kept.append(item)
        else:
            report["could_not_verify"].append({"item": item["claim"], "reason": "the agent gave no valid source for it"})
            report["flags"].append({"where": "Key finding", "problem": f"no valid citation: {item['claim'][:80]}",
                                    "action": "moved to Could Not Verify"})
    report["key_findings"] = kept
    if report["flags"]:
        report["warnings"].append(f"{len(report['flags'])} citation problem(s) were found and fixed automatically. "
                                  "See 'Citation checks' in the research details.")
    # Mark which sources the report actually cites.
    cited = set()
    for item in report["key_findings"] + report["detailed_analysis"] + report["perspectives"] + report["extra_sections"]:
        cited |= set(item.get("citations", []))
    for key in ("executive_summary", "introduction_and_scope", "agreement", "disagreement", "weight_of_evidence", "conclusion"):
        cited |= set(cited_numbers(report[key]))
    for source in sources:
        source["cited"] = source["id"] in cited
    return report


# ---------------------------------------------------------------------------
# Follow-up questions
# ---------------------------------------------------------------------------
def _relevant_sources(question: str, sources: list[dict], limit: int) -> list[dict]:
    words = set(re.findall(r"[a-z0-9]{4,}", question.lower()))
    scored = sorted(sources, key=lambda s: -sum((s["title"] + " " + s["text"]).lower().count(w) for w in words))
    return [s for s in scored[:limit] if s["text"]]


def run_followup(report: dict, question: str, history: list[dict] | None = None,
                 allow_search: bool = True, progress: ProgressFn = None) -> dict:
    """Answer a follow-up question from the stored report and sources.

    If the stored sources can't answer it and allow_search is True, runs a small
    extra search, adds the new sources to the report's registry and says so.
    Returns {question, answer, citations, searched, new_sources}.
    """
    question = " ".join((question or "").split())
    if len(question) < 3:
        raise AppError("topic", "empty question")
    say = progress or (lambda stage, message: None)
    sources = report["sources"]
    summary = trim_to_tokens(report["executive_summary"], 250) + "\n" + "\n".join(
        f"- {f['claim']} {f['citations']}" for f in report["key_findings"])
    past = "\n".join(f"Q: {h['question']}\nA: {trim_to_tokens(h['answer'], 100)}" for h in (history or [])[-3:])

    def ask(candidates: list[dict], may_search: bool) -> schema.FollowupAnswer:
        task = (f"Report topic: \"{report['metadata'].get('topic', report['title'])}\"\n"
                f"REPORT SUMMARY:\n{trim_to_tokens(summary, 700)}\n\n"
                f"EARLIER QUESTIONS:\n{past or '(none)'}\n\n"
                f"NEW QUESTION: {question}\n\n"
                "Answer from the sources below with [n] citations. If they do not contain the answer, say so plainly"
                + (", set needs_search to true and suggest 1-2 search queries." if may_search else ". Do not guess.")
                + "\nSOURCES:\n{sources}")
        budget = min(2600, _budget_for_sources(task, 1300))
        return call_agent(task.replace("{sources}", format_sources(candidates, budget)), schema.FOLLOWUP_SHAPE,
                          schema.FollowupAnswer, 1300, progress, "Follow-up")

    say("writing", "Checking the stored sources …")
    answer = ask(_relevant_sources(question, sources, 5), allow_search)
    new_sources: list[dict] = []
    if answer.needs_search and allow_search:
        say("searching", "The stored sources don't cover this; running a small extra search …")
        try:
            for query in (answer.search_queries or [question])[:2]:
                new_sources += search_more(query, sources, max_pages=2)
        except AppError as error:
            log.warning("Follow-up search failed: %s", error.detail)
        if new_sources:
            answer = ask(new_sources + _relevant_sources(question, sources, 2), False)
    result = {"question": question, "answer": answer.answer, "citations": answer.citations,
              "searched": bool(new_sources), "new_sources": [s["id"] for s in new_sources]}
    holder = {"addenda": [result]}
    flags = verify_citations(holder, sources)
    result["flags"] = flags
    result["citations"] = sorted(set(result["citations"]) | set(cited_numbers(result["answer"])))
    if not result["answer"].strip():
        result["answer"] = "The sources don't contain an answer to this question."
    return result


def add_addendum(report: dict, followup: dict) -> None:
    """Append a follow-up answer to the report as an addendum (shown and exported)."""
    if not any(a["question"] == followup["question"] for a in report["addenda"]):
        report["addenda"].append({k: followup[k] for k in ("question", "answer", "citations", "searched")})
    for source in report["sources"]:
        if source["id"] in followup["citations"]:
            source["cited"] = True


# ---------------------------------------------------------------------------
# Slides
# ---------------------------------------------------------------------------
def make_slide_outline(report: dict, total_slides: int = 10, progress: ProgressFn = None) -> list[dict]:
    """Agent writes the content slides; code adds title, agenda and sources slides.

    total_slides includes those three fixed slides, so the agent writes total-3.
    """
    content = max(3, total_slides - 3)
    is_doc = report["kind"] == "document"
    parts = [f"TITLE: {report['title']}", f"SUMMARY: {trim_to_tokens(report['executive_summary'], 250)}"]
    for i, f in enumerate(report["key_findings"], 1):
        parts.append(f"FINDING {i}: {f['claim']} | evidence: {trim_to_tokens(f['evidence'], 90)} | "
                     f"confidence {f['confidence']} | {f['citations']}")
    for s in report["detailed_analysis"]:
        origin = f" ({s.get('origin', 'document')})" if is_doc else ""
        parts.append(f"SECTION{origin}: {s['heading']}: {trim_to_tokens(s['body'], 220 if is_doc else 120)}")
    for p in report["perspectives"]:
        parts.append(f"PERSPECTIVE: {p['position']} ({p['evidence_strength']}) {trim_to_tokens(p['summary'], 60)} {p['citations']}")
    if report["limitations"]:
        parts.append("LIMITATIONS: " + "; ".join(report["limitations"][:5]))
    parts.append(f"CONCLUSION: {trim_to_tokens(report['conclusion'], 150)}")
    material = trim_to_tokens("\n".join(parts), 3000)
    audience = report["metadata"].get("audience", "")
    plan = ("one slide per key finding (or per main section), a perspectives slide if there are perspectives, "
            "a limitations slide, and a conclusion slide")
    task = (f"Create exactly {content} content slides for a presentation"
            + (f" for {audience}" if audience else "") + f". Include {plan}. Merge or drop items to hit the count.\n"
            "Slides must be concise: at most 5 bullets, each at most 12 words. Put detail, nuance and [n] citations in "
            "the speaker notes. Keep citation numbers exactly as given; never invent new ones.\n"
            f"MATERIAL:\n{material}")
    outline = call_agent(task, schema.SLIDES_SHAPE, schema.SlideOutline, 2400, progress, "Slides")
    slides = [s.model_dump() for s in outline.slides][:content]
    holder = {"extra_sections": [{"heading": s["title"], "body": s["notes"] + " " + " ".join(s["bullets"]),
                                  "citations": s["citations"]} for s in slides]}
    verify_citations(holder, report["sources"])
    for slide, checked in zip(slides, holder["extra_sections"]):
        slide["citations"] = checked["citations"]
        slide["notes"] = verify_text(slide["notes"], report["sources"])
    return slides


def verify_text(text: str, sources: list[dict]) -> str:
    """Remove unknown citation numbers and URLs from a single piece of text."""
    holder = {"conclusion": text}
    verify_citations(holder, sources)
    return holder["conclusion"]


# ---------------------------------------------------------------------------
# My Document Workspace
# ---------------------------------------------------------------------------
DOC_LENGTHS = {
    "Summary: short": (150, 900), "Summary: medium": (350, 1300), "Summary: long": (700, 2000),
}


def run_document_job(text: str, source_name: str, output_type: str, length: str, purpose: str, audience: str,
                     enhance: bool = False, progress: ProgressFn = None) -> dict:
    """Turn the user's own document into a report dict (kind='document').

    1. Long documents are split into chunks; each chunk is summarised faithfully.
    2. The agent shapes the notes for the requested output, length and audience,
       marking anything it adds as 'added'.
    3. Optional: key claims are fact-checked against the web and cited.
    The exporters then make slides, PDF or Word from the result.
    """
    from documents import chunk_text

    say = progress or (lambda stage, message: None)
    started = time.time()
    tokens_before = LIMITER.used_today
    chunks = chunk_text(text, 2800)
    say("planning", f"Document split into {len(chunks)} part(s) for reading.")

    # 1. Faithful notes per chunk (also gives us checkable claims).
    notes, claims = [], []
    for number, chunk in enumerate(chunks, start=1):
        say("reading", f"Reading part {number} of {len(chunks)} …")
        part = call_agent(
            "Summarise this part of the user's document faithfully: keep its meaning, numbers and terms; add nothing. "
            "Also list up to 3 specific, checkable factual claims it makes.\n"
            f"Purpose of the final output: {purpose or 'not given'}.\nTEXT PART {number}/{len(chunks)}:\n{chunk}",
            schema.CHUNK_SHAPE, schema.ChunkNotes, 900, progress, f"Reading part {number}")
        notes += part.points
        claims += part.key_claims
    material = text if len(chunks) == 1 and estimate_tokens(text) < 2800 else "\n".join(f"- {p}" for p in notes)
    material = trim_to_tokens(material, 3000)

    # 2. Shape the output.
    if output_type == "Summary":
        target_words, max_tokens = DOC_LENGTHS.get(f"Summary: {length}", (350, 1300))
        goal = f"a summary of about {target_words} words"
    elif output_type == "Slides":
        max_tokens = 2600
        goal = f"content for a {length}-slide presentation (sections become slides)"
    else:
        max_tokens = 3000
        goal = f"a {output_type} document of about {length}"
    say("writing", f"Writing {goal} …")
    draft = call_agent(
        f"Turn the user's material into {goal}.\nPurpose: {purpose or 'general'}. Audience: {audience or 'general'}.\n"
        "Stay faithful to the material. Any sentence or section you add that is not in the material (context, "
        "transitions, explanations) must be in a section with origin \"added\". Do not add facts, numbers or "
        "sources that are not in the material. No citations are needed for the user's own material.\n"
        f"MATERIAL:\n{material}",
        schema.DOCUMENT_SHAPE, schema.DocumentDraft, max_tokens, progress, "Shaping document")

    report = {
        "id": uuid.uuid4().hex[:10], "kind": "document",
        "metadata": {"created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "model": MODEL,
                     "topic": draft.title or source_name, "source_name": source_name, "output_type": output_type,
                     "length": length, "purpose": purpose, "audience": audience, "enhanced": enhance,
                     "mode_label": "My Document", "chunks": len(chunks)},
        "title": draft.title or source_name, "executive_summary": draft.summary,
        "introduction_and_scope": "", "key_findings": [], "perspectives": [],
        "detailed_analysis": [s.model_dump() for s in draft.sections],
        "agreement": "", "disagreement": "", "weight_of_evidence": "", "extra_sections": [],
        "could_not_verify": [], "limitations": [], "conclusion": draft.conclusion, "background_note": "",
        "addenda": [], "sources": [], "warnings": [], "flags": [],
    }
    if any(s["origin"] == "added" for s in report["detailed_analysis"]):
        report["warnings"].append("Sections marked 'Added' were written by the assistant and are not from your document.")

    # 3. Optional web fact-check of key claims.
    if enhance and claims:
        fact_check_claims(report, claims[: 3 if len(chunks) == 1 else 5], progress)
    report["metadata"]["duration_seconds"] = round(time.time() - started)
    report["metadata"]["tokens_estimate"] = LIMITER.used_today - tokens_before
    from evaluation import compute_scorecard
    report["scorecard"] = compute_scorecard(report)
    say("done", "Done.")
    return report


def fact_check_claims(report: dict, claims: list[str], progress: ProgressFn = None) -> None:
    """Search the web for each claim, then let the agent judge support with citations."""
    say = progress or (lambda stage, message: None)
    registry: list[dict] = report["sources"]
    for number, claim in enumerate(claims, start=1):
        say("searching", f"Fact-checking claim {number} of {len(claims)} …")
        try:
            results = web_search(claim[:200], 4)
        except AppError:
            continue
        read = 0
        for result in results:
            key = normalize_url(result["url"])
            if any(normalize_url(s["url"]) == key for s in registry):
                continue
            tier, reason = classify_tier(result["url"], result["title"])
            if tier is None:
                continue
            page = fetch_page(result["url"]) if read < 1 else {"ok": False}
            read += 1 if page.get("ok") else 0
            registry.append({
                "id": len(registry) + 1, "title": page.get("title") or result["title"], "url": result["url"],
                "domain": domain_of(result["url"]), "tier": tier, "tier_reason": reason, "tier_label": TIER_LABELS[tier],
                "date": page.get("date") or result.get("date", ""), "source_type": "web", "snippet": result["snippet"],
                "preprint": False, "query": f"fact-check: {claim[:80]}", "cited": False,
                "text": best_excerpt(page["text"], [claim], 350) if page.get("ok") else result["snippet"],
                "read_status": "full text" if page.get("ok") else "snippet"})
        time.sleep(0.8)
    if not registry:
        report["warnings"].append("Web fact-checking was requested, but searches returned nothing usable.")
        report["could_not_verify"] = [{"item": c, "reason": "web search returned no usable sources"} for c in claims]
        return
    claim_lines = "\n".join(f"C{i}. {c}" for i, c in enumerate(claims, 1))
    task = ("Check each claim from the user's document against the sources. Verdict: Supported, Contradicted, or "
            "Not verified (sources don't address it). Evidence must say what the sources state, with [n].\n"
            f"CLAIMS:\n{claim_lines}\nSOURCES:\n{{sources}}")
    budget = _budget_for_sources(task, 1500)
    say("writing", "Comparing claims with sources …")
    result = call_agent(task.replace("{sources}", format_sources(registry, budget)), schema.FACTCHECK_SHAPE,
                        schema.FactCheck, 1500, progress, "Fact-check")
    for check in result.checks:
        item = check.model_dump()
        if item["verdict"] == "Not verified" or not item["citations"]:
            report["could_not_verify"].append({"item": item["claim"], "reason": "no source found that addresses it"})
            continue
        report["key_findings"].append({
            "claim": item["claim"], "evidence": item["evidence"],
            "interpretation": f"Web check verdict: {item['verdict']}.", "verdict": item["verdict"],
            "confidence": "High" if item["verdict"] == "Supported" and len(item["citations"]) > 1 else "Medium",
            "confidence_reason": f"{len(item['citations'])} web source(s) checked", "citations": item["citations"]})
    report["flags"] = verify_citations(report, registry)
    for source in registry:
        source["cited"] = any(source["id"] in f["citations"] for f in report["key_findings"])


# ---------------------------------------------------------------------------
# Optional faithfulness check (LLM-as-judge)
# ---------------------------------------------------------------------------
def judge_faithfulness(report: dict, sample: int = 3, progress: ProgressFn = None) -> list[dict]:
    """Ask the agent, as a strict judge, whether cited source text supports sampled findings.

    Only findings citing at least one full-text source can be judged.
    Returns [{finding, claim, verdict, reason}].
    """
    by_id = {s["id"]: s for s in report["sources"]}
    candidates = [(i, f) for i, f in enumerate(report["key_findings"], 1)
                  if any(by_id.get(n, {}).get("read_status") == "full text" for n in f["citations"])][:sample]
    if not candidates:
        return []
    blocks = []
    for i, finding in candidates:
        cited = "\n".join(f"  [{n}] {trim_to_tokens(by_id[n]['text'], 300)}" for n in finding["citations"][:3] if n in by_id)
        blocks.append(f"FINDING {i}: {finding['claim']}\nEVIDENCE AS WRITTEN: {finding['evidence']}\nCITED SOURCE TEXT:\n{cited}")
    task = ("Act as a strict fact-checking judge. For each finding, decide only from the cited source text whether it "
            "is Supported, Partially supported, or Not supported. Be strict: if a detail is missing from the text, "
            "it is not supported.\n\n" + "\n\n".join(blocks))
    result = call_agent(task, schema.JUDGE_SHAPE, schema.JudgeResult, 900, progress, "Faithfulness check")
    claims = dict(candidates)
    return [{"finding": j.finding, "claim": claims.get(j.finding, {}).get("claim", ""), "verdict": j.verdict,
             "reason": j.reason} for j in result.judgements if j.finding in claims]
