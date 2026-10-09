"""Quality evaluation.

Part 1: compute_scorecard()  deterministic checks on every report. Free, no tokens.
Part 2: faithfulness_check() optional LLM-as-judge on a few claims (uses tokens).
Part 3: run_benchmark()      offline benchmark over eval_set.json, saved as CSV + Markdown.

The scorecard deliberately does NOT add metrics into one "quality score":
a single number would hide which part is weak (for example, perfect citations
but only Tier 3 sources), and any weighting would be an arbitrary choice.

Run the benchmark from the project folder:
    python evaluation.py --benchmark                 # whole set, Quick depth
    python evaluation.py --benchmark --limit 3       # first 3 topics only
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from modes import get_mode

RECENCY_DAYS = {"current": 90, "technical": 730, "market": 730}


def _metric(key: str, name: str, value, display: str, status: str, target: str, explanation: str,
            focus: bool = False) -> dict:
    return {"key": key, "name": name, "value": value, "display": display, "status": status,
            "target": target, "explanation": explanation, "focus": focus}


def _grade(value: float, good: float, fair: float) -> str:
    return "good" if value >= good else "fair" if value >= fair else "poor"


def _age_days(date_text: str) -> int | None:
    for fmt, size in (("%Y-%m-%d", 10), ("%Y-%m", 7), ("%Y", 4)):
        try:
            when = datetime.strptime(date_text[:size], fmt).replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - when).days
        except (ValueError, TypeError):
            continue
    return None


def compute_scorecard(report: dict) -> dict:
    """Deterministic quality metrics for one report. See each explanation."""
    mode_key = report["metadata"].get("mode", "")
    focus = set(get_mode(mode_key)["evaluation_focus"]) if report["kind"] == "research" else set()
    flags = report.get("flags", [])
    findings = report["key_findings"]
    sources = report["sources"]
    cited = [s for s in sources if s.get("cited")]
    metrics = []

    # 1. Citation validity: how many citation numbers the agent wrote were real.
    invalid = sum(1 for f in flags if f["problem"].startswith("citation ["))
    valid = sum(len(item.get("citations", [])) for key in ("key_findings", "detailed_analysis", "perspectives",
                                                         "extra_sections") for item in report[key])
    total = valid + invalid
    if total:
        share = valid / total
        metrics.append(_metric("citation_validity", "Citation validity", share, f"{share:.0%} ({invalid} invalid removed)",
                               _grade(share, 0.98, 0.9), "98–100%",
                               "Share of citation numbers written by the agent that match a real source in the "
                               "registry. Invalid ones were removed before you saw the report."))

    # 2. Citation coverage: findings that kept at least one valid citation.
    moved = sum(1 for f in flags if f["action"] == "moved to Could Not Verify")
    if findings or moved:
        share = len(findings) / (len(findings) + moved)
        metrics.append(_metric("coverage", "Citation coverage", share,
                               f"{share:.0%} of {len(findings) + moved} findings",
                               _grade(share, 1.0, 0.8), "100%",
                               "Share of key findings with at least one valid citation. Findings without one were "
                               "moved to Could Not Verify.", "coverage" in focus))

    # 3. Sources and diversity.
    if sources:
        domains = {s["domain"] for s in cited}
        diversity = len(domains) / len(cited) if cited else 0
        metrics.append(_metric("diversity", "Sources and diversity", diversity,
                               f"{len(cited)} cited of {len(sources)} found · {len(domains)} distinct sites",
                               "good" if len(cited) >= 5 and diversity >= 0.7 else "fair" if len(cited) >= 3 else "poor",
                               "5+ cited, mostly different sites",
                               "How many sources the report cites, and whether they come from different websites "
                               "(independent sources are stronger than many pages from one site).", "diversity" in focus))

        # 4. Credibility mix.
        base = cited or sources
        shares = {t: sum(1 for s in base if s["tier"] == t) / len(base) for t in (1, 2, 3)}
        strong = shares[1] + shares[2]
        metrics.append(_metric("tier1_share", "Credibility mix", strong,
                               f"T1 {shares[1]:.0%} · T2 {shares[2]:.0%} · T3 {shares[3]:.0%}",
                               _grade(strong, 0.7, 0.4), "Tier 1+2 at least 70%",
                               "Share of cited sources in each heuristic credibility tier. Tiers describe the type "
                               "of website, not whether a claim is true.", bool({"tier1_share", "credibility"} & focus)))

        # 5. Full text vs snippets.
        full = sum(1 for s in base if s["read_status"] == "full text") / len(base)
        abstracts = sum(1 for s in base if s["read_status"] == "abstract") / len(base)
        metrics.append(_metric("full_text", "Full text read", full,
                               f"{full:.0%} full text · {abstracts:.0%} abstracts · {1 - full - abstracts:.0%} snippets",
                               _grade(full + abstracts * 0.7, 0.5, 0.25), "50%+ full text",
                               "Share of cited sources whose page text was actually read, versus only a search "
                               "snippet or an abstract. Claims from snippets are weaker.", "full_text" in focus))

        # 6. Recency (modes where dates matter).
        if mode_key in RECENCY_DAYS:
            window = RECENCY_DAYS[mode_key]
            ages = [_age_days(s.get("date", "")) for s in base]
            dated = [a for a in ages if a is not None]
            recent = sum(1 for a in dated if a <= window) / len(base)
            metrics.append(_metric("recency", "Recency", recent,
                                   f"{recent:.0%} within {window} days · {len(base) - len(dated)} undated",
                                   _grade(recent, 0.6, 0.3), f"60%+ within {window} days",
                                   "Share of cited sources with a publication date inside the window that matters "
                                   "for this mode. Undated sources count as not recent.", True))

    # 7. Section completeness.
    if report["kind"] == "research":
        mode = get_mode(mode_key)
        required = {"Executive summary": report["executive_summary"], "Introduction": report["introduction_and_scope"],
                    "Key findings": findings, "Detailed analysis": report["detailed_analysis"],
                    "Perspectives": report["perspectives"], "Limitations": report["limitations"],
                    "Conclusion": report["conclusion"]}
        headings = {s["heading"].lower() for s in report["extra_sections"]}
        for extra in mode["extra_sections"]:
            required[extra["heading"]] = extra["heading"].lower() in headings
        missing = [name for name, value in required.items() if not value]
        share = 1 - len(missing) / len(required)
        metrics.append(_metric("completeness", "Section completeness", share,
                               f"{len(required) - len(missing)}/{len(required)}" + (f" · missing: {', '.join(missing)}" if missing else ""),
                               _grade(share, 1.0, 0.8), "All sections present",
                               "Whether every section expected for this mode was written (empty sections count as missing)."))
    else:
        added = sum(1 for s in report["detailed_analysis"] if s.get("origin") == "added")
        metrics.append(_metric("added_share", "Added content", added,
                               f"{added} of {len(report['detailed_analysis'])} sections added by the assistant",
                               "good" if added <= 1 else "fair", "Few or none",
                               "Sections the assistant wrote that are not from your document. They are labelled."))

    # 8. Evidence vs interpretation separation.
    if findings and report["kind"] == "research":
        separated = sum(1 for f in findings if f["evidence"].strip() and f["interpretation"].strip()
                        and f["evidence"].strip() != f["interpretation"].strip())
        share = separated / len(findings)
        metrics.append(_metric("separation", "Evidence vs interpretation", share,
                               f"{separated}/{len(findings)} findings separated",
                               _grade(share, 1.0, 0.8), "Every finding",
                               "Share of key findings that have both a non-empty evidence part and a different, "
                               "non-empty interpretation part."))

    # 9. Honesty counters (informational, no grade).
    metrics.append(_metric("honesty", "Could not verify · fixes", len(report["could_not_verify"]),
                           f"{len(report['could_not_verify'])} could-not-verify items · {len(flags)} citation fixes",
                           "n/a", "Informational",
                           "Unverifiable items are a sign of honesty, not failure. Many citation fixes suggest the "
                           "agent struggled with the sources."))
    return {"metrics": metrics,
            "note": "Metrics are shown separately on purpose. A combined score would hide which part is weak."}


def faithfulness_check(report: dict, sample: int = 3, progress=None) -> dict:
    """Optional LLM-as-judge: do cited source texts support sampled findings? Uses ~3-4K tokens."""
    from research_agent import judge_faithfulness

    results = judge_faithfulness(report, sample, progress)
    counts = {v: sum(1 for r in results if r["verdict"] == v)
              for v in ("Supported", "Partially supported", "Not supported")}
    return {"results": results, "counts": counts, "checked": len(results),
            "note": "Only findings citing a full-text source can be checked. The judge is the same model, so treat "
                    "this as a helpful second look, not proof."}


# ---------------------------------------------------------------------------
# Offline benchmark
# ---------------------------------------------------------------------------
FIELDS = ["run_at", "id", "mode", "topic", "depth", "outcome", "error_kind", "expected", "behaviour_ok",
          "sources", "cited", "findings", "could_not_verify", "citation_fixes", "citation_validity", "coverage",
          "tier12_share", "full_text", "completeness", "separation", "seconds", "tokens_estimate"]


def _value(scorecard: dict, key: str) -> str:
    for metric in scorecard["metrics"]:
        if metric["key"] == key and isinstance(metric["value"], (int, float)):
            return f"{metric['value']:.2f}"
    return ""


def _behaviour_ok(case: dict, outcome: str, error_kind: str, report: dict | None) -> bool:
    """Did the app do what the eval case expects?"""
    expected = case["expected"]
    if expected == "report":
        return outcome == "report" and bool(report and report["key_findings"])
    if expected == "no_confident_report":   # fictional topic: fail honestly, or only low-confidence claims
        if outcome != "report":
            return error_kind in ("no_sources", "search_failed")
        return not any(f["confidence"] == "High" for f in report["key_findings"]) and bool(report["could_not_verify"])
    if expected == "hedged_report":          # hard to verify: must contain Could Not Verify items
        return outcome == "report" and bool(report["could_not_verify"])
    if expected == "topic_error":
        return error_kind == "topic"
    return False


def run_benchmark(set_path: str = "eval_set.json", out_dir: str = "eval_results", depth: str = "Quick",
                  limit: int | None = None, delay_seconds: int = 70) -> Path:
    """Run every case in eval_set.json and append results to a CSV (resumable).

    Re-running skips cases already in today's CSV, so you can continue the next
    day when Groq's daily token budget runs out. A Markdown table is written too.
    """
    from research_agent import run_research
    from utils import AppError

    cases = json.loads(Path(set_path).read_text(encoding="utf-8"))["cases"][: limit or None]
    out = Path(out_dir)
    out.mkdir(exist_ok=True)
    csv_path = out / f"results-{datetime.now():%Y-%m-%d}-{depth.lower()}.csv"
    done = set()
    if csv_path.exists():
        with csv_path.open(encoding="utf-8") as handle:
            done = {row["id"] for row in csv.DictReader(handle)}
    for number, case in enumerate(cases, 1):
        if case["id"] in done:
            print(f"[{number}/{len(cases)}] {case['id']}: already done, skipping")
            continue
        print(f"[{number}/{len(cases)}] {case['id']}: {case['topic']!r} ({case['mode']})")
        started, report, outcome, error_kind = time.time(), None, "report", ""
        try:
            report = run_research(case["topic"], case["mode"], depth, "Brief", case.get("time_focus", "Any time"),
                                  use_cache=False, progress=lambda stage, msg: print(f"    {stage}: {msg}"))
        except AppError as error:
            outcome, error_kind = "error", error.kind
            print(f"    -> {error.title}")
            if error.kind == "daily_limit":
                print("Daily token budget used up. Run the same command tomorrow to continue.")
                break
        card = report["scorecard"] if report else {"metrics": []}
        row = {"run_at": datetime.now().isoformat(timespec="seconds"), "id": case["id"], "mode": case["mode"],
               "topic": case["topic"], "depth": depth, "outcome": outcome, "error_kind": error_kind,
               "expected": case["expected"], "behaviour_ok": _behaviour_ok(case, outcome, error_kind, report),
               "sources": len(report["sources"]) if report else 0,
               "cited": sum(1 for s in report["sources"] if s.get("cited")) if report else 0,
               "findings": len(report["key_findings"]) if report else 0,
               "could_not_verify": len(report["could_not_verify"]) if report else 0,
               "citation_fixes": len(report["flags"]) if report else 0,
               "citation_validity": _value(card, "citation_validity"), "coverage": _value(card, "coverage"),
               "tier12_share": _value(card, "tier1_share"), "full_text": _value(card, "full_text"),
               "completeness": _value(card, "completeness"), "separation": _value(card, "separation"),
               "seconds": round(time.time() - started),
               "tokens_estimate": report["metadata"].get("tokens_estimate", "") if report else ""}
        new_file = not csv_path.exists()
        with csv_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDS)
            if new_file:
                writer.writeheader()
            writer.writerow(row)
        print(f"    -> behaviour ok: {row['behaviour_ok']} · {row['seconds']} s")
        if number < len(cases):
            time.sleep(delay_seconds)      # respect Groq's per-minute limit and search engines
    _write_markdown(csv_path)
    print(f"Results: {csv_path} and {csv_path.with_suffix('.md')}")
    return csv_path


def _write_markdown(csv_path: Path) -> None:
    with csv_path.open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    columns = ["id", "mode", "outcome", "behaviour_ok", "cited", "findings", "could_not_verify",
               "citation_validity", "coverage", "tier12_share", "full_text", "completeness", "seconds"]
    lines = [f"# Benchmark results ({csv_path.stem})", "",
             f"Behaviour OK: {sum(r['behaviour_ok'] == 'True' for r in rows)}/{len(rows)}", "",
             "| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(r.get(c, "") for c in columns) + " |" for r in rows]
    csv_path.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Research Desk offline benchmark")
    parser.add_argument("--benchmark", action="store_true", help="run eval_set.json")
    parser.add_argument("--depth", default="Quick", choices=["Quick", "Standard", "Deep"])
    parser.add_argument("--limit", type=int, default=None, help="only the first N cases")
    parser.add_argument("--delay", type=int, default=70, help="seconds to wait between cases")
    args = parser.parse_args()
    if args.benchmark:
        run_benchmark(depth=args.depth, limit=args.limit, delay_seconds=args.delay)
    else:
        parser.print_help()
