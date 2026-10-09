"""Research modes and options, as plain dictionaries.

A mode changes *how* the same agent researches and writes: which angles the
queries cover, which sites get a ranking boost, how much recency matters,
which extra report sections are added, and what the scorecard emphasises.
To add or tweak a mode, edit a dictionary here. No other code changes needed.
"""

from __future__ import annotations

MODES: dict[str, dict] = {
    "general": {
        "label": "General Learning",
        "tagline": "Clear explanations, definitions and examples from reference and educational sources.",
        "query_angles": ["definition and basics", "how it works", "concrete examples",
                         "common misconceptions", "latest developments", "criticism or limits"],
        "preferred_domains": ["wikipedia.org", "britannica.com", ".edu", ".ac.uk", "khanacademy.org",
                              "nationalgeographic.com", "nasa.gov", "nih.gov", "who.int"],
        "apis": ["wikipedia"],
        "use_news": False,
        "recency": "any",                 # any | prefer_recent | recent_only
        "writing_style": "Explain for a curious non-expert. Define terms before using them. Use one concrete example per key idea.",
        "extra_sections": [],
        "evaluation_focus": ["credibility", "coverage"],
        "examples": ["How do heat pumps work in cold climates?", "What is CRISPR and how is it used?",
                     "Why is the sky blue?"],
    },
    "technical": {
        "label": "Technical Documentation",
        "tagline": "Official docs, specifications, changelogs and repositories. Versions and dates matter.",
        "query_angles": ["official documentation", "latest version and changelog", "breaking changes or deprecations",
                         "configuration and usage", "known issues", "comparison with alternatives"],
        "preferred_domains": ["docs.", "developer.", "github.com", "readthedocs.io", "python.org", "pypi.org",
                              "w3.org", "ietf.org", "mozilla.org", "learn.microsoft.com", "cloud.google.com"],
        "apis": [],
        "use_news": False,
        "recency": "prefer_recent",
        "writing_style": "Be precise. Always state version numbers and dates when sources give them. Flag anything that may be outdated.",
        "extra_sections": [{"heading": "Version Notes",
                            "instruction": "Versions, release dates and deprecations mentioned in sources; flag outdated info."}],
        "evaluation_focus": ["recency", "tier1_share"],
        "examples": ["What changed in Python 3.13?", "How does HTTP/3 differ from HTTP/2?",
                     "Streamlit st.fragment: what is it for?"],
    },
    "market": {
        "label": "Market Analysis",
        "tagline": "Company sites, filings, official statistics and business news. Free data is limited.",
        "query_angles": ["market size and growth estimates", "major companies and market share", "official statistics",
                         "recent business news", "risks and headwinds", "regulation"],
        "preferred_domains": ["sec.gov", "census.gov", "bls.gov", "eurostat.ec.europa.eu", "ons.gov.uk", "oecd.org",
                              "worldbank.org", "imf.org", "reuters.com", "ft.com", "bloomberg.com", "wsj.com"],
        "apis": [],
        "use_news": True,
        "recency": "prefer_recent",
        "writing_style": "Give numbers only when a source states them, with the source's date and whether it is an estimate. State plainly that free data is limited and may be outdated.",
        "extra_sections": [{"heading": "Data Caveats",
                            "instruction": "Which numbers are estimates, how old they are, and which sources disagree."}],
        "evaluation_focus": ["recency", "tier1_share"],
        "examples": ["Electric scooter sharing market in Europe", "The global market for heat pumps",
                     "Who are the main players in cloud GPU rental?"],
    },
    "current": {
        "label": "Current Affairs",
        "tagline": "Recent news from multiple outlets, with dates. Developing stories are marked as such.",
        "query_angles": ["latest news", "background and timeline", "official statements",
                         "different outlets' coverage", "what happens next"],
        "preferred_domains": ["reuters.com", "apnews.com", "bbc.co.uk", "bbc.com", "npr.org", "theguardian.com",
                              "nytimes.com", "washingtonpost.com", "aljazeera.com", "dw.com", ".gov"],
        "apis": [],
        "use_news": True,
        "recency": "recent_only",
        "writing_style": "Always give dates. Say which outlet reported what. Mark facts that are still developing or disputed.",
        "extra_sections": [{"heading": "Timeline",
                            "instruction": "Dated events in order, each with [n]. Mark the story as developing if it is."}],
        "evaluation_focus": ["recency", "diversity"],
        "examples": ["Latest developments in EU AI regulation", "What happened at the most recent UN climate summit?",
                     "Current state of global semiconductor export controls"],
    },
    "scientific": {
        "label": "Scientific Research",
        "tagline": "Peer-reviewed papers, preprints (labelled) and institutions. Findings separated from media coverage.",
        "query_angles": ["systematic review or meta-analysis", "key studies and findings", "study limitations",
                         "scientific consensus", "conflicting evidence", "media coverage vs. findings"],
        "preferred_domains": ["nature.com", "science.org", "cell.com", "thelancet.com", "nejm.org", "bmj.com",
                              "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "plos.org", "nih.gov", "who.int",
                              "cochranelibrary.com", "arxiv.org", "biorxiv.org", "medrxiv.org"],
        "apis": ["openalex", "arxiv", "pubmed"],
        "use_news": False,
        "recency": "any",
        "writing_style": "Separate what a study found from how media describe it. Note sample sizes and study types when sources give them. Label preprints as not peer-reviewed.",
        "extra_sections": [{"heading": "Study Quality Notes",
                            "instruction": "Study types, sample sizes, preprint vs peer-reviewed, and limitations stated in sources."}],
        "evaluation_focus": ["tier1_share", "full_text"],
        "examples": ["Does intermittent fasting improve long-term weight loss?",
                     "Microplastics in human blood: what do studies show?", "Effectiveness of mRNA flu vaccines"],
    },
    "academic": {
        "label": "Academic Research",
        "tagline": "Scholarly sources in a literature-review style, with research gaps and a reference list.",
        "query_angles": ["foundational work", "recent literature review", "main theoretical positions",
                         "methods used", "open questions and research gaps", "critiques"],
        "preferred_domains": [".edu", ".ac.uk", "jstor.org", "springer.com", "wiley.com", "tandfonline.com",
                              "sciencedirect.com", "sagepub.com", "openalex.org", "doi.org", "arxiv.org", "plato.stanford.edu"],
        "apis": ["openalex", "crossref", "wikipedia"],
        "use_news": False,
        "recency": "any",
        "writing_style": "Write as a concise literature review: group sources by theme or position, compare methods, and identify research gaps.",
        "extra_sections": [{"heading": "Research Gaps",
                            "instruction": "Questions the sources say are open or understudied."}],
        "evaluation_focus": ["tier1_share", "diversity"],
        "examples": ["Effects of remote work on productivity: literature review",
                     "Theories of language acquisition", "Universal basic income pilot results"],
    },
}

DEFAULT_MODE = "general"

# Depth controls how much we collect. Numbers were chosen to fit Groq's free
# 8K tokens-per-minute limit (see docs/phase-0-report.md, section 3).
DEPTHS: dict[str, dict] = {
    "Quick": {"queries": 3, "results_per_query": 5, "pages_to_read": 3, "excerpt_tokens": 450,
              "writing_calls": 1, "estimate": "about 1 minute"},
    "Standard": {"queries": 5, "results_per_query": 6, "pages_to_read": 6, "excerpt_tokens": 500,
                 "writing_calls": 2, "estimate": "about 2–3 minutes"},
    "Deep": {"queries": 8, "results_per_query": 8, "pages_to_read": 10, "excerpt_tokens": 500,
             "writing_calls": 3, "estimate": "about 4–6 minutes"},
}

# Length controls how much the agent writes (output tokens per writing call).
LENGTHS: dict[str, dict] = {
    "Brief": {"findings": "3-4", "analysis_sections": "1-2", "max_tokens": 2200,
              "words": "about 600–900 words"},
    "Standard": {"findings": "4-6", "analysis_sections": "2-3", "max_tokens": 2600,
                 "words": "about 1,200–1,800 words"},
    "Comprehensive": {"findings": "6-8", "analysis_sections": "3-5", "max_tokens": 3000,
                      "words": "about 2,000–3,500 words"},
}

# Time focus maps to the search library's time filters.
TIME_FOCUS: dict[str, dict] = {
    "Any time": {"timelimit": None, "news": False, "help": "No date filter."},
    "Past year": {"timelimit": "y", "news": False,
                  "help": "Web results from the last 12 months, where the search engine supports it."},
    "Recent news": {"timelimit": "m", "news": True, "help": "News articles from the last month."},
}


def get_mode(key: str) -> dict:
    """Return a mode's settings, falling back to General Learning."""
    return MODES.get(key, MODES[DEFAULT_MODE])
