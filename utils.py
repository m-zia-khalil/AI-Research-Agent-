"""Small shared helpers used by every other file.

- Secrets: get_secret() reads Streamlit secrets first, then environment variables.
- Errors: AppError carries a friendly message; friendly_error() turns any
  exception into one, so users never see a raw traceback.
- Budgets: rough token counting, text trimming and a per-minute token limiter
  that keeps us inside Groq's free-tier limits.
- Citations: check every [n] the model wrote against the source registry.
- JSON: pull a JSON object out of model text and repair small mistakes.
- Caching: a tiny time-limited cache that works with and without Streamlit.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from collections import deque
from datetime import date
from typing import Any, Callable

from dotenv import load_dotenv

# Read the local .env file (if there is one) into environment variables.
# On Streamlit Cloud there is no .env file, so this simply does nothing there.
load_dotenv()

# Keep CrewAI quiet: no usage telemetry, and no interactive "view your traces?"
# question in the terminal, which would pause the app. setdefault() only fills
# these in when you have not set them yourself.
os.environ.setdefault("CREWAI_DISABLE_TELEMETRY", "true")
os.environ.setdefault("CREWAI_TRACING_ENABLED", "false")

# ---------------------------------------------------------------------------
# Logging: details go to the terminal / Streamlit Cloud logs, never to users.
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger, e.g. get_logger(__name__)."""
    return logging.getLogger(name)


log = get_logger(__name__)


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------
def get_secret(name: str, default: str = "") -> str:
    """Return a secret by name, or `default` if it is not set anywhere.

    Looks in two places, in this order:
    1. Streamlit secrets (.streamlit/secrets.toml locally, the Secrets box on Cloud)
    2. Environment variables (including those loaded from .env)
    """
    try:
        import streamlit as st

        if name in st.secrets:
            value = str(st.secrets[name]).strip()
            if value:
                return value
    except Exception:
        # No secrets.toml file, or not running inside Streamlit. Fall through.
        pass
    return os.environ.get(name, default).strip()


def mask_secret(value: str) -> str:
    """Show only the start and end of a secret, e.g. 'gsk_ab…9xYz'."""
    if len(value) <= 10:
        return "•" * len(value)
    return f"{value[:6]}…{value[-4:]}"


def check_groq_key() -> tuple[bool, str]:
    """Check that a Groq key is present and looks plausible (no network call)."""
    key = get_secret("GROQ_API_KEY")
    if not key:
        return False, (
            "No GROQ_API_KEY found. Copy .env.example to .env and paste your key "
            "from https://console.groq.com/keys."
        )
    if key == "your-groq-key-here":
        return False, "GROQ_API_KEY is still the placeholder. Replace it with your real key in .env."
    if not key.startswith("gsk_"):
        # Only a hint: Groq keys have started with "gsk_" so far, but the real
        # test is an API call.
        return True, (
            f"GROQ_API_KEY found ({mask_secret(key)}), but it doesn't start with 'gsk_' "
            "like Groq keys usually do. Check you copied the whole key."
        )
    return True, f"GROQ_API_KEY found ({mask_secret(key)})."


# ---------------------------------------------------------------------------
# Friendly errors
# ---------------------------------------------------------------------------
# Every known problem has a short title and a "what to do" line.
ERROR_MESSAGES: dict[str, tuple[str, str]] = {
    "missing_key": (
        "No Groq API key is set.",
        "Add GROQ_API_KEY to your .env file (local) or to the app's Secrets (Streamlit Cloud).",
    ),
    "auth": (
        "Groq rejected the API key.",
        "Check the key at console.groq.com/keys. Copy it again with no spaces, then restart the app.",
    ),
    "model": (
        "The AI model isn't available.",
        "Groq may have renamed or retired openai/gpt-oss-120b. Check console.groq.com/docs/models.",
    ),
    "rate_limit": (
        "Groq's free per-minute limit was reached.",
        "Wait a minute and try again. Quick depth uses the fewest tokens.",
    ),
    "daily_limit": (
        "Groq's free daily token limit is used up.",
        "It resets within 24 hours. Cached reports and exports still work.",
    ),
    "too_large": (
        "The request was too large for the free tier.",
        "Try a lower depth or a shorter document.",
    ),
    "search_failed": (
        "Web search isn't responding right now.",
        "Search engines sometimes rate-limit shared servers. Wait a minute and try again.",
    ),
    "no_sources": (
        "No usable sources were found, so no report was written.",
        "Try a more specific topic, a different mode, or 'Any time'. The app never writes a report from memory.",
    ),
    "network": (
        "A network request timed out.",
        "Check your internet connection and try again.",
    ),
    "topic": (
        "The topic is too short.",
        "Write at least a few words, e.g. 'How do heat pumps work in cold climates?'",
    ),
    "bad_output": (
        "The model's answer couldn't be read, even after a retry.",
        "Try again. If it keeps happening, try Quick depth or Brief length.",
    ),
    "file": (
        "That file couldn't be read.",
        "Use a PDF, DOCX, TXT or MD file, or paste the text instead.",
    ),
    "scanned_pdf": (
        "This PDF has (almost) no selectable text. It's probably a scanned image.",
        "Reading scans needs OCR, which this app doesn't include. Run OCR first (e.g. in Adobe Acrobat or Google Drive) or paste the text.",
    ),
    "export": (
        "The file couldn't be created.",
        "Try another format. The details are in the app logs.",
    ),
    "unknown": (
        "Something unexpected went wrong.",
        "Try again. The details are in the app logs.",
    ),
}


class AppError(Exception):
    """An error with a known cause and a friendly message for the user."""

    def __init__(self, kind: str, detail: str = "") -> None:
        self.kind = kind if kind in ERROR_MESSAGES else "unknown"
        self.title, self.action = ERROR_MESSAGES[self.kind]
        self.detail = detail
        super().__init__(f"{self.kind}: {detail or self.title}")


def friendly_error(error: Exception) -> AppError:
    """Turn any exception into an AppError. The full error goes to the logs."""
    if isinstance(error, AppError):
        return error
    text = f"{type(error).__name__}: {error}"
    log.exception("Unexpected error: %s", text)
    lowered = text.lower()
    if "authentication" in lowered or "invalid api key" in lowered or "401" in lowered:
        return AppError("auth", text)
    if "tokens per day" in lowered or "tpd" in lowered:
        return AppError("daily_limit", text)
    if "ratelimit" in lowered or "rate limit" in lowered or "429" in lowered:
        return AppError("rate_limit", text)
    if "request too large" in lowered or "413" in lowered or "context" in lowered and "exceed" in lowered:
        return AppError("too_large", text)
    if "notfound" in lowered or "model_not_found" in lowered or "does not exist" in lowered:
        return AppError("model", text)
    if "timeout" in lowered or "timed out" in lowered or "connect" in lowered:
        return AppError("network", text)
    return AppError("unknown", text)


# ---------------------------------------------------------------------------
# Token budgets
# ---------------------------------------------------------------------------
# Groq free tier for openai/gpt-oss-120b (verified Oct 2026, see docs/phase-0-report.md).
# If your console shows higher limits, raise these two numbers.
GROQ_TPM_LIMIT = 8_000      # tokens per minute
GROQ_TPD_LIMIT = 200_000    # tokens per day
SAFE_CALL_TOKENS = 7_000    # keep prompt + max_tokens of one call under this


def estimate_tokens(text: str) -> int:
    """Rough token count. ~3.5 characters per token errs on the safe (high) side."""
    return int(len(text) / 3.5) + 1


def trim_to_tokens(text: str, max_tokens: int) -> str:
    """Cut text to roughly `max_tokens`, ending at a sentence or word boundary."""
    max_chars = int(max_tokens * 3.5)
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    end = max(cut.rfind(". "), cut.rfind("\n"))
    if end > max_chars * 0.6:
        cut = cut[: end + 1]
    return cut.rstrip() + " …"


class TokenLimiter:
    """Keeps our calls under Groq's tokens-per-minute and tokens-per-day limits.

    Before each model call we ask `wait_for(tokens)`. If sending that many tokens
    now would exceed the per-minute limit, it sleeps until enough of the last
    minute's usage has "expired". One limiter is shared by the whole app process,
    because everyone using this deployment shares the same Groq key.
    """

    def __init__(self, tpm: int = GROQ_TPM_LIMIT, tpd: int = GROQ_TPD_LIMIT) -> None:
        self.tpm = tpm
        self.tpd = tpd
        self.window: deque[tuple[float, int]] = deque()  # (time, tokens) in last 60 s
        self.day = date.today()
        self.used_today = 0
        self.lock = threading.Lock()

    def _expire(self, now: float) -> None:
        while self.window and now - self.window[0][0] >= 60:
            self.window.popleft()
        if date.today() != self.day:
            self.day, self.used_today = date.today(), 0

    def seconds_to_wait(self, tokens: int) -> float:
        """How long to wait before `tokens` more fit in the per-minute window."""
        with self.lock:
            now = time.time()
            self._expire(now)
            used = sum(t for _, t in self.window)
            if used + tokens <= self.tpm:
                return 0.0
            # Find when enough old usage leaves the 60-second window.
            freed = 0
            for stamp, spent in self.window:
                freed += spent
                if used - freed + tokens <= self.tpm:
                    return max(0.0, 60 - (now - stamp)) + 0.5
            return 60.5

    def wait_for(self, tokens: int, on_wait: Callable[[float], None] | None = None) -> None:
        """Sleep until `tokens` can be sent. Raises AppError if the day's budget is gone."""
        if self.used_today + tokens > self.tpd:
            raise AppError("daily_limit", f"used {self.used_today} of {self.tpd} today")
        delay = self.seconds_to_wait(tokens)
        if delay > 0:
            log.info("Waiting %.0fs for the per-minute token limit", delay)
            if on_wait:
                on_wait(delay)
            time.sleep(delay)

    def record(self, tokens: int) -> None:
        """Remember tokens actually used by a finished call."""
        with self.lock:
            self.window.append((time.time(), tokens))
            self.used_today += tokens


LIMITER = TokenLimiter()


# ---------------------------------------------------------------------------
# JSON from model output
# ---------------------------------------------------------------------------
def extract_json(text: str) -> Any:
    """Find the JSON object in model output and parse it, repairing small mistakes.

    Handles ```json fences, text before/after the object, trailing commas and
    similar slips. Raises ValueError if nothing usable is found.
    """
    from json_repair import repair_json

    if not text or not text.strip():
        raise ValueError("empty model output")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    candidate = fenced.group(1) if fenced else text
    start, end = candidate.find("{"), candidate.rfind("}")
    if start != -1 and end > start:
        candidate = candidate[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        repaired = repair_json(candidate, return_objects=True)
        if isinstance(repaired, (dict, list)) and repaired:
            return repaired
        raise ValueError("could not parse JSON from model output")


# ---------------------------------------------------------------------------
# Citation checking (hallucination prevention, layer 2)
# ---------------------------------------------------------------------------
URL_PATTERN = re.compile(r"https?://[^\s\)\]\}>\"']+")
CITE_PATTERN = re.compile(r"\[(\d+(?:\s*[,–-]\s*\d+)*)\]")


def _clean_text_field(text: str, valid: set[int], known_urls: set[str], where: str, flags: list[dict]) -> str:
    """Remove unknown [n] markers and unknown URLs from one piece of text."""
    if not text:
        return text

    def fix_marker(match: re.Match) -> str:
        numbers = [int(n) for n in re.findall(r"\d+", match.group(1))]
        kept = [n for n in numbers if n in valid]
        for n in numbers:
            if n not in valid:
                flags.append({"where": where, "problem": f"citation [{n}] does not match any source", "action": "removed"})
        return "[" + ", ".join(str(n) for n in kept) + "]" if kept else ""

    text = CITE_PATTERN.sub(fix_marker, text)

    def fix_url(match: re.Match) -> str:
        url = match.group(0).rstrip(".,;")
        if url in known_urls or url.rstrip("/") in known_urls:
            return match.group(0)
        flags.append({"where": where, "problem": f"URL not in source registry: {url}", "action": "removed"})
        return "[link removed: not a researched source]"

    return URL_PATTERN.sub(fix_url, text)


def verify_citations(report: dict, sources: list[dict]) -> list[dict]:
    """Check every citation and URL in the report against the source registry.

    Changes the report in place:
    - citation numbers that don't exist in the registry are removed;
    - URLs in the text that we never collected are removed;
    Returns a list of everything flagged, so the UI can show it.
    """
    valid = {s["id"] for s in sources}
    known_urls = {s["url"] for s in sources} | {s["url"].rstrip("/") for s in sources}
    flags: list[dict] = []

    def check_list(items: list[dict], section: str, text_keys: list[str]) -> None:
        for index, item in enumerate(items or [], start=1):
            where = f"{section} {index}"
            cited = item.get("citations", []) or []
            bad = [n for n in cited if n not in valid]
            for n in bad:
                flags.append({"where": where, "problem": f"citation [{n}] does not match any source", "action": "removed"})
            item["citations"] = sorted({n for n in cited if n in valid})
            for key in text_keys:
                if isinstance(item.get(key), str):
                    item[key] = _clean_text_field(item[key], valid, known_urls, where, flags)

    check_list(report.get("key_findings", []), "Key finding", ["claim", "evidence", "interpretation", "confidence_reason"])
    check_list(report.get("detailed_analysis", []), "Analysis section", ["heading", "body"])
    check_list(report.get("perspectives", []), "Perspective", ["position", "held_by", "summary", "evidence"])
    check_list(report.get("extra_sections", []), "Section", ["heading", "body"])
    check_list(report.get("addenda", []), "Addendum", ["answer"])
    for key in ["executive_summary", "introduction_and_scope", "agreement", "disagreement",
                "weight_of_evidence", "conclusion", "background_note"]:
        if isinstance(report.get(key), str):
            report[key] = _clean_text_field(report[key], valid, known_urls, key.replace("_", " ").title(), flags)
    report["limitations"] = [
        _clean_text_field(x, valid, known_urls, "Limitations", flags) for x in report.get("limitations", [])
    ]
    for item in report.get("could_not_verify", []):
        item["item"] = _clean_text_field(item.get("item", ""), valid, known_urls, "Could not verify", flags)
    return flags


def cited_numbers(text: str) -> list[int]:
    """All citation numbers written as [n] or [n, m] in a piece of text."""
    numbers: list[int] = []
    for group in CITE_PATTERN.findall(text or ""):
        numbers += [int(n) for n in re.findall(r"\d+", group)]
    return numbers


# ---------------------------------------------------------------------------
# A tiny time-limited cache (works in scripts and in Streamlit)
# ---------------------------------------------------------------------------
_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()


def cache_get(key: str, ttl_seconds: float) -> Any | None:
    """Return a cached value younger than ttl_seconds, else None."""
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit and time.time() - hit[0] < ttl_seconds:
            return hit[1]
        return None


def cache_set(key: str, value: Any) -> None:
    """Store a value in the cache (keeps at most 200 entries)."""
    with _CACHE_LOCK:
        if len(_CACHE) > 200:
            oldest = min(_CACHE, key=lambda k: _CACHE[k][0])
            _CACHE.pop(oldest, None)
        _CACHE[key] = (time.time(), value)
