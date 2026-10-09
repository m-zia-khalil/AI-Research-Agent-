"""Fake model and fake web, so the whole pipeline can be tested offline with no tokens.

Used by tests/test_offline.py. The fake model looks at the task text and
returns a plausible JSON answer, including a few deliberate mistakes (an
invented citation [99] and an invented URL) so we can see the citation checks
remove them.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from crewai.llms.base_llm import BaseLLM  # noqa: E402

PROMPTS: list[str] = []


def _answer_for(task: str) -> dict:
    if "web search queries" in task:
        return {"queries": ["heat pump basics", "heat pump cold climate efficiency", "heat pump criticism",
                            "heat pump data", "heat pump latest", "heat pump cost", "x", "y"]}
    if "key findings from these sources" in task:
        return {"title": "How heat pumps cope with cold climates", "key_findings": [
            {"claim": "Modern cold-climate heat pumps keep working well below freezing.",
             "evidence": "Source [1] reports operation down to -25 °C; [2] gives similar figures.",
             "interpretation": "This suggests climate is less of a barrier than often assumed.",
             "confidence": "High", "confidence_reason": "two Tier 1-2 sources agree", "citations": [1, 2]},
            {"claim": "Efficiency drops as temperatures fall.",
             "evidence": "[3] describes lower COP at low temperatures. See https://invented.example.com/fake.",
             "interpretation": "Backup heating may still be needed in the coldest regions.",
             "confidence": "medium", "confidence_reason": "one source", "citations": ["3", 99]},
            {"claim": "An uncited claim the checker should move.", "evidence": "No source.", "interpretation": "None.",
             "confidence": "Low", "confidence_reason": "none", "citations": []}],
            "could_not_verify": [{"item": "Exact lifetime cost savings", "reason": "sources disagree"}]}
    if "Now write the summary" in task or "Write a research report" in task:
        data = {"executive_summary": "Heat pumps work in cold climates [1], with lower efficiency [3] [42].",
                "introduction_and_scope": "Covers air-source heat pumps for homes.",
                "detailed_analysis": [{"heading": "How they work", "body": "They move heat [1].", "citations": [1]}],
                "perspectives": [{"position": "Suitable everywhere", "held_by": "manufacturers", "summary": "Works at -25 °C",
                                  "evidence": "[1]", "evidence_strength": "Strong", "citations": [1]},
                                 {"position": "Needs backup", "held_by": "some engineers", "summary": "Efficiency drops",
                                  "evidence": "[3]", "evidence_strength": "moderate", "citations": [3]}],
                "agreement": "All agree efficiency falls with temperature [1][3].", "disagreement": "Costs [2].",
                "weight_of_evidence": "Evidence favours suitability with caveats.",
                "extra_sections": [{"heading": "Version Notes", "body": "n/a", "citations": []}],
                "could_not_verify": [{"item": "Grid impact", "reason": "not covered"}],
                "limitations": ["Few independent field studies."], "conclusion": "Heat pumps are viable [1].",
                "background_note": ""}
        if "Write a research report" in task:
            data.update(_answer_for("key findings from these sources"))
        return data
    if "NEW QUESTION" in task:
        if "unrelated" in task and "needs_search" in task and "set needs_search" in task:
            return {"answer": "The sources don't cover this.", "citations": [], "needs_search": True,
                    "search_queries": ["unrelated follow-up"]}
        return {"answer": "Defrost cycles reduce output briefly [2].", "citations": [2], "needs_search": False,
                "search_queries": []}
    if "content slides" in task:
        return {"slides": [{"title": f"Slide {i}", "bullets": ["Short point one", "Short point two"],
                            "notes": "Detail with [1] and fake [77].", "citations": [1, 77]} for i in range(1, 9)]}
    if "Summarise this part" in task:
        return {"points": ["The document says heat pumps cut emissions by 50%."],
                "key_claims": ["Heat pumps cut household heating emissions by about 50%."]}
    if "Turn the user's material" in task:
        return {"title": "My heat pump notes", "summary": "A faithful summary.",
                "sections": [{"heading": "Main point", "body": "Emissions fall.", "origin": "document"},
                             {"heading": "Context", "body": "Added background.", "origin": "added"}],
                "conclusion": "Done."}
    if "Check each claim" in task:
        return {"checks": [{"claim": "Heat pumps cut household heating emissions by about 50%.",
                            "verdict": "Supported", "evidence": "[1] reports similar cuts.", "citations": [1]}]}
    if "strict fact-checking judge" in task:
        return {"judgements": [{"finding": 1, "verdict": "Supported", "reason": "matches source"}]}
    return {}


class FakeLLM(BaseLLM):
    """Returns canned JSON. The first answer to each task is wrapped in chatter to test JSON extraction."""

    def call(self, messages, tools=None, callbacks=None, available_functions=None, from_task=None,
             from_agent=None, response_model=None):
        text = messages if isinstance(messages, str) else "\n".join(m["content"] for m in messages)
        PROMPTS.append(text)
        return "Here you go:\n```json\n" + json.dumps(_answer_for(text)) + "\n```"

    def supports_function_calling(self) -> bool:
        return False

    def get_context_window_size(self) -> int:
        return 131072


PAGE = ("Heat pumps move heat from outside air into a home. " * 8 + "\n\n" +
        "Cold-climate models keep operating at minus 25 degrees Celsius according to field tests. " * 6 + "\n\n" +
        "Efficiency, measured as coefficient of performance, declines at low temperatures. " * 6)


def fake_web_search(query, max_results=6, timelimit=None, news=False):
    domains = ["energy.gov", "iea.org", "en.wikipedia.org", "someblog.example", "bbc.co.uk", "pinterest.com",
               "nature.com", "random-site.example"]
    slug = re.sub(r"\W+", "-", query)[:20]
    return [{"title": f"Result {i} for {query}", "url": f"https://{d}/{slug}/{i}", "snippet": f"Snippet about {query} {i}.",
             "date": "2025-03-01" if news else "", "source_type": "news" if news else "web"}
            for i, d in enumerate(domains[:max_results])]


def fake_fetch_page(url):
    if "someblog" in url:
        return {"ok": False, "text": "", "title": "", "date": "", "error": "blocked or paywalled (HTTP 403)"}
    return {"ok": True, "text": PAGE + f"\n\nUnique detail for {url}.", "title": f"Page at {url}", "date": "2025-01-15",
            "error": ""}


def install(research_agent_module, search_tools_module) -> None:
    """Swap the real model and web for the fakes."""
    research_agent_module.LLM_FACTORY = lambda max_tokens: FakeLLM(model="fake-model")
    search_tools_module.web_search = fake_web_search
    search_tools_module.fetch_page = fake_fetch_page
    research_agent_module.web_search = fake_web_search
    research_agent_module.fetch_page = fake_fetch_page
    for name in search_tools_module.API_FUNCTIONS:
        search_tools_module.API_FUNCTIONS[name] = lambda query, limit=3: []
    import utils
    utils.LIMITER.tpm = 10**9          # no waiting in offline tests
    research_agent_module.time.sleep = lambda s: None
    search_tools_module.time.sleep = lambda s: None
