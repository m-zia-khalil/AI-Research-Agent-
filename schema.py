"""The structured shapes the agent must produce, defined with Pydantic.

Why a separate file: the agent, the UI, the exporters, the evaluation and the
follow-ups all depend on these shapes. Keeping them in one small file (instead
of inside research_agent.py) means exporters.py and evaluation.py can use them
without loading CrewAI.

How it fits together:
- The agent returns JSON. Pydantic checks it against these models (types, allowed
  values, required fields) and fills in safe defaults for anything missing.
- Code then turns the checked object into a plain dictionary, the "report dict",
  and adds things the agent must never write itself: metadata, the Sources list,
  warnings and the scorecard. Everything else is generated from that one dict.

Each model also has a JSON_SHAPE string: a compact example the agent sees in
its instructions. A compact example costs far fewer tokens than a full JSON
Schema, which matters under the 8K tokens-per-minute limit.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Confidence = Literal["High", "Medium", "Low"]


class Lenient(BaseModel):
    """Base model: ignore unexpected extra keys instead of failing."""

    model_config = {"extra": "ignore"}

    @field_validator("*", mode="before")
    @classmethod
    def _none_to_default(cls, value, info):
        # Models sometimes write null where we expect "" or []. Treat as empty.
        if value is None:
            field = cls.model_fields[info.field_name]
            if field.default_factory is not None:
                return field.default_factory()
            return field.default
        return value


def _clean_citations(value) -> list[int]:
    """Accept [1, "2", "[3]"] and return [1, 2, 3]; drop anything else."""
    numbers: list[int] = []
    for item in value or []:
        text = str(item).strip("[] ")
        if text.isdigit():
            numbers.append(int(text))
    return numbers


# ---------------------------------------------------------------------------
# 1. Query plan (agent call 1)
# ---------------------------------------------------------------------------
class QueryPlan(Lenient):
    queries: list[str] = Field(default_factory=list)   # 3–8 short search queries

    @field_validator("queries")
    @classmethod
    def _tidy(cls, value: list[str]) -> list[str]:
        seen, tidy = set(), []
        for q in value:
            q = " ".join(str(q).split()).strip('"')
            if q and q.lower() not in seen:
                seen.add(q.lower())
                tidy.append(q[:150])
        return tidy


QUERY_PLAN_SHAPE = '{"queries": ["short search query", "..."]}'


# ---------------------------------------------------------------------------
# 2. Report parts (agent writing calls)
# ---------------------------------------------------------------------------
class KeyFinding(Lenient):
    claim: str = ""                      # one-sentence finding
    evidence: str = ""                   # what sources actually say, attributable, with [n]
    interpretation: str = ""             # the agent's own analysis, labelled as such
    confidence: Confidence = "Low"       # High / Medium / Low
    confidence_reason: str = ""          # why: number of sources, tiers, agreement
    citations: list[int] = Field(default_factory=list)

    _c = field_validator("citations", mode="before")(lambda cls, v: _clean_citations(v))

    @field_validator("confidence", mode="before")
    @classmethod
    def _conf(cls, value) -> str:
        text = str(value or "Low").strip().capitalize()
        return text if text in ("High", "Medium", "Low") else "Low"


class Section(Lenient):
    heading: str = ""
    body: str = ""
    citations: list[int] = Field(default_factory=list)

    _c = field_validator("citations", mode="before")(lambda cls, v: _clean_citations(v))


class Perspective(Lenient):
    position: str = ""                   # name of the position
    held_by: str = ""                    # who holds it (types of sources / groups)
    summary: str = ""                    # what it claims
    evidence: str = ""                   # evidence offered for it, with [n]
    evidence_strength: Literal["Strong", "Moderate", "Weak"] = "Moderate"
    citations: list[int] = Field(default_factory=list)

    _c = field_validator("citations", mode="before")(lambda cls, v: _clean_citations(v))

    @field_validator("evidence_strength", mode="before")
    @classmethod
    def _strength(cls, value) -> str:
        text = str(value or "Moderate").strip().capitalize()
        return text if text in ("Strong", "Moderate", "Weak") else "Moderate"


class Unverified(Lenient):
    item: str = ""                       # a question or claim we could not verify
    reason: str = ""                     # why (no sources, conflicting, only Tier 3...)


class FindingsPart(Lenient):
    """Output of a 'findings' call (Standard and Deep depth)."""

    title: str = ""
    key_findings: list[KeyFinding] = Field(default_factory=list)
    could_not_verify: list[Unverified] = Field(default_factory=list)


class AnalysisPart(Lenient):
    """Output of the 'analysis' call (Standard and Deep depth)."""

    executive_summary: str = ""
    introduction_and_scope: str = ""
    detailed_analysis: list[Section] = Field(default_factory=list)
    perspectives: list[Perspective] = Field(default_factory=list)
    agreement: str = ""                  # where sources agree
    disagreement: str = ""               # where sources disagree
    weight_of_evidence: str = ""         # which position the evidence favours, if any
    extra_sections: list[Section] = Field(default_factory=list)   # mode-specific
    could_not_verify: list[Unverified] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    conclusion: str = ""
    background_note: str = ""            # only if the user enabled it


class ReportDraft(FindingsPart, AnalysisPart):
    """Everything the agent writes, in one object (used by Quick depth)."""


FINDINGS_SHAPE = """{
 "title": "specific report title",
 "key_findings": [{"claim": "one sentence", "evidence": "what sources say, with [n]",
   "interpretation": "your analysis, not stated by sources", "confidence": "High|Medium|Low",
   "confidence_reason": "e.g. 3 independent Tier 1-2 sources agree", "citations": [1, 2]}],
 "could_not_verify": [{"item": "claim or question", "reason": "why"}]
}"""

ANALYSIS_SHAPE = """{
 "executive_summary": "3-5 sentences with [n]",
 "introduction_and_scope": "what this covers and what it does not",
 "detailed_analysis": [{"heading": "...", "body": "paragraphs with [n]", "citations": [1]}],
 "perspectives": [{"position": "...", "held_by": "...", "summary": "...", "evidence": "with [n]",
   "evidence_strength": "Strong|Moderate|Weak", "citations": [2]}],
 "agreement": "where sources agree, with [n]", "disagreement": "where they differ, with [n]",
 "weight_of_evidence": "which position the evidence favours, or that it is balanced",
 "extra_sections": [{"heading": "mode section name", "body": "...", "citations": [3]}],
 "could_not_verify": [{"item": "...", "reason": "..."}],
 "limitations": ["..."], "conclusion": "...", "background_note": ""
}"""

REPORT_SHAPE = FINDINGS_SHAPE[:-2] + "," + ANALYSIS_SHAPE[1:]


# ---------------------------------------------------------------------------
# 3. Follow-up answers
# ---------------------------------------------------------------------------
class FollowupAnswer(Lenient):
    answer: str = ""                     # answer text with [n] citations
    citations: list[int] = Field(default_factory=list)
    needs_search: bool = False           # true if the sources cannot answer it
    search_queries: list[str] = Field(default_factory=list)   # 1-2 queries if needs_search

    _c = field_validator("citations", mode="before")(lambda cls, v: _clean_citations(v))


FOLLOWUP_SHAPE = '{"answer": "text with [n]", "citations": [1], "needs_search": false, "search_queries": []}'


# ---------------------------------------------------------------------------
# 4. Slide outline (agent writes, exporters.py draws)
# ---------------------------------------------------------------------------
class Slide(Lenient):
    title: str = ""
    bullets: list[str] = Field(default_factory=list)   # max ~5 short bullets
    notes: str = ""                                     # speaker notes: detail + citations
    citations: list[int] = Field(default_factory=list)

    _c = field_validator("citations", mode="before")(lambda cls, v: _clean_citations(v))

    @field_validator("bullets")
    @classmethod
    def _short(cls, value: list[str]) -> list[str]:
        return [str(b).strip()[:160] for b in value if str(b).strip()][:5]


class SlideOutline(Lenient):
    slides: list[Slide] = Field(default_factory=list)


SLIDES_SHAPE = '{"slides": [{"title": "...", "bullets": ["max 12 words", "..."], "notes": "speaker notes with detail and [n]", "citations": [1]}]}'


# ---------------------------------------------------------------------------
# 5. Document workspace
# ---------------------------------------------------------------------------
class ChunkNotes(Lenient):
    points: list[str] = Field(default_factory=list)       # faithful summary points
    key_claims: list[str] = Field(default_factory=list)   # checkable factual claims


CHUNK_SHAPE = '{"points": ["faithful point from the text"], "key_claims": ["specific checkable claim"]}'


class DocSection(Lenient):
    heading: str = ""
    body: str = ""
    origin: Literal["document", "added"] = "document"   # added = written by the agent

    @field_validator("origin", mode="before")
    @classmethod
    def _origin(cls, value) -> str:
        return "added" if str(value).lower().startswith("add") else "document"


class DocumentDraft(Lenient):
    title: str = ""
    summary: str = ""
    sections: list[DocSection] = Field(default_factory=list)
    conclusion: str = ""


DOCUMENT_SHAPE = """{"title": "...", "summary": "faithful overview",
 "sections": [{"heading": "...", "body": "...", "origin": "document|added"}], "conclusion": "..."}"""


class ClaimCheck(Lenient):
    claim: str = ""
    verdict: Literal["Supported", "Contradicted", "Not verified"] = "Not verified"
    evidence: str = ""
    citations: list[int] = Field(default_factory=list)

    _c = field_validator("citations", mode="before")(lambda cls, v: _clean_citations(v))

    @field_validator("verdict", mode="before")
    @classmethod
    def _verdict(cls, value) -> str:
        text = str(value or "").lower()
        if text.startswith("support"):
            return "Supported"
        if text.startswith("contra"):
            return "Contradicted"
        return "Not verified"


class FactCheck(Lenient):
    checks: list[ClaimCheck] = Field(default_factory=list)


FACTCHECK_SHAPE = '{"checks": [{"claim": "...", "verdict": "Supported|Contradicted|Not verified", "evidence": "with [n]", "citations": [1]}]}'


# ---------------------------------------------------------------------------
# 6. Faithfulness judge (optional evaluation)
# ---------------------------------------------------------------------------
class Judgement(Lenient):
    finding: int = 0                     # key finding number (1-based)
    verdict: Literal["Supported", "Partially supported", "Not supported"] = "Not supported"
    reason: str = ""

    @field_validator("verdict", mode="before")
    @classmethod
    def _verdict(cls, value) -> str:
        text = str(value or "").lower()
        if text.startswith("partial"):
            return "Partially supported"
        if text.startswith("support"):
            return "Supported"
        return "Not supported"


class JudgeResult(Lenient):
    judgements: list[Judgement] = Field(default_factory=list)


JUDGE_SHAPE = '{"judgements": [{"finding": 1, "verdict": "Supported|Partially supported|Not supported", "reason": "short"}]}'
