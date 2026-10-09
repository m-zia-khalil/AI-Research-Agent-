# Research Desk: an AI research agent

Research Desk is a Streamlit app that researches a topic on the web and writes a cited,
structured report. Python code does the searching and reading. A single CrewAI agent,
running on Groq's free `openai/gpt-oss-120b`, does the thinking and returns one report
object. Every citation is then checked against the sources that were actually read.

It also has a **My Document Workspace**, which turns your own report or notes into slides,
a PDF, a Word document or a summary.

> New to this? Read [`docs/GUIDE.md`](docs/GUIDE.md). It is the teaching guide: setup,
> how every part works, design decisions, testing, deployment and troubleshooting.

---

## Features

- **Six research modes**: General, Technical, Market, Current Events, Scientific and Academic. Each mode has its own search angles, preferred sources, research APIs and extra report sections.
- **Options**: depth (Quick, Standard or Deep), length (Brief, Standard or Comprehensive) and time focus (Any time, Past year or Recent news).
- **Query planning**: the agent turns your topic into focused search queries.
- **Free sources**: DuckDuckGo-backed web and news search through `ddgs`, plus Wikipedia, arXiv, OpenAlex, Crossref and PubMed.
- **Source registry with credibility tiers**:
  - Tier 1 is official, academic and peer-reviewed sources.
  - Tier 2 is established news and reference sources.
  - Tier 3 is everything else.
  - Content farms are excluded, and near-duplicate pages are merged.
- **A structured report** with:
  - an executive summary;
  - key findings that separate *evidence* from *interpretation*, each with a confidence level;
  - a detailed analysis;
  - perspectives compared side by side;
  - a **Could Not Verify** list;
  - limitations;
  - numbered sources.
- **Two-layer hallucination prevention**:
  - The agent's rules forbid it from inventing anything.
  - Code then removes citations that don't exist, links that weren't researched, and findings with no valid citation.
- **Follow-up questions**, answered from the stored sources (or a small extra search), which you can add to the report as addenda.
- **Exports**: Markdown, PDF, Word (DOCX) and PowerPoint (PPTX), each at three detail levels. For slides, the agent writes an outline and code draws it.
- **Evaluation**:
  - a scorecard on every run;
  - an optional faithfulness check;
  - an offline benchmark over 10 fixed test cases.
- **Reliability**:
  - retries with backoff;
  - a token limiter that respects Groq's free limits;
  - caching;
  - session history of 20 reports;
  - plain-language error messages, never a raw traceback.
- **A "Research Desk" editorial interface**: serif reading column, no emojis, light and dark themes, works on phones.

## How it works (one paragraph)

1. You enter a topic.
2. The agent plans search queries.
3. Python searches the web and the research APIs, reads the best pages, and builds a numbered source registry.
4. The agent receives trimmed excerpts and returns the report as JSON. Code validates it with Pydantic and checks every `[n]` citation and URL against the registry.
5. Anything that fails a check is removed or moved to Could Not Verify, and the app tells you what it fixed.

The agent never browses by itself, which keeps its token use predictable and its sources traceable.

## Tech stack

| Part | Choice |
|---|---|
| Language | Python 3.12 |
| Interface | Streamlit 1.65 |
| Agent | CrewAI 1.15 (one agent, one task per call) |
| Model | Groq `openai/gpt-oss-120b` via LiteLLM |
| Search | `ddgs` (web and news) and five free research APIs |
| Page reading | trafilatura (web pages), pypdf (PDFs) |
| Validation | Pydantic 2, json-repair |
| Exports | fpdf2, python-docx, python-pptx |

The app needs no database, no login, no Docker and no paid API.

## Project structure

```
ai-research-agent/
├── app.py               Streamlit entry point: pages, navigation, state
├── research_agent.py    The single CrewAI agent and every task it runs
├── search_tools.py      Web search, research APIs, page reading, source registry
├── schema.py            Pydantic models for every agent output
├── modes.py             The six research modes, depths, lengths and time options
├── documents.py         Reading PDF, DOCX, TXT and MD files for the workspace
├── exporters.py         Markdown, PDF, Word and PowerPoint exports
├── evaluation.py        Scorecard, faithfulness check, offline benchmark
├── eval_set.json        The 10 benchmark cases
├── ui_components.py     Interface pieces (forms, report view, panels)
├── utils.py             Secrets, errors, token limiter, citation checks, cache
├── requirements.txt
├── .env.example         Template for your local key
├── .streamlit/
│   ├── config.toml      Theme (light and dark)
│   └── secrets.toml.example
├── assets/
│   ├── style.css        Report styles
│   └── fonts/           Open-licence fonts used in PDF exports
├── docs/
│   ├── GUIDE.md         The full teaching guide
│   └── phase-0-report.md
└── tests/
    ├── check_setup.py   Checks your install (no internet)
    ├── test_offline.py  Full end-to-end run with a fake model (no internet)
    ├── test_exporters.py
    ├── test_llm.py      Live: Groq key and model
    ├── test_search.py   Live: search and APIs
    ├── test_agent.py    Live: one complete Quick research run
    ├── fakes.py         The fake model and fake web used by the offline tests
    └── demo_app.py      The full app with fakes: no key, no tokens
```

## Installation

You need Python 3.12 and a free Groq account.

```bash
cd ai-research-agent
python3.12 -m venv .venv               # Windows: py -3.12 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## Environment setup

1. Create a free key at <https://console.groq.com/keys>.
2. Copy the template: `cp .env.example .env` (Windows: `copy .env.example .env`).
3. Put your key after `GROQ_API_KEY=`.

Optional settings:

- **`CONTACT_EMAIL`** puts you in Crossref's and OpenAlex's "polite pool", which has better limits.
- **`OPENALEX_API_KEY`** is a free OpenAlex key. The app works without it.

`.env` and `.streamlit/secrets.toml` are in `.gitignore`, so your key is never uploaded.
No key is written anywhere in the code.

## Running

Check the install first. These commands use no internet and no tokens:

```bash
python tests/check_setup.py
python tests/test_offline.py
```

Next, run the live checks, in this order:

```bash
python tests/test_llm.py
python tests/test_search.py
python tests/test_agent.py
```

Then start the app:

```bash
streamlit run app.py
```

To try the interface without a key, run `streamlit run tests/demo_app.py`. It uses a fake model and fake web pages.

## Using the app

**Research:**

1. Pick a mode.
2. Type a topic, or click an example.
3. Choose the depth, length and time focus.
4. Press **Research this topic**.

The progress panel shows each real stage. Once the report is ready:

- **Read it.** Citation numbers link to their sources, and the side column lists the contents and sources.
- **Check its quality.** The scorecard shows how the report did, and you can run the faithfulness check.
- **Download it** as Markdown, PDF, Word or PowerPoint.
- **Ask follow-up questions** and add the answers to the report.

**My Documents:**

1. Upload a PDF, DOCX, TXT or MD file, or paste text.
2. Choose slides, a PDF, a Word document or a summary.
3. Set the length, purpose and audience.
4. Optionally turn on web fact-checking.

Your content is kept faithful. Anything the assistant adds is labelled "added". Scanned
PDFs (images with no text) are detected and explained.

**History** in the sidebar holds up to 20 reports for the current browser session.

## How hallucination prevention works

- **Layer 1: rules.** The agent's instructions say:
  - use only the numbered sources it is given;
  - cite every factual claim as `[n]`;
  - never invent sources, URLs, statistics or quotes;
  - put anything uncertain under Could Not Verify.
- **Layer 2: code.** After every answer, `verify_citations` in `utils.py`:
  - removes `[n]` numbers that aren't in the registry;
  - replaces links that weren't researched with "[link removed: not a researched source]";
  - moves findings with no valid citation to Could Not Verify;
  - records every fix, which the report shows as "Read with care".

## How evaluation works

- **Scorecard (every run, free):**
  - citation validity and coverage;
  - source diversity;
  - credibility mix;
  - share of sources read in full text;
  - recency, for time-sensitive modes;
  - section completeness;
  - evidence vs interpretation separation.

  The metrics are deliberately **not** combined into one score, because one number would hide the weak part.
- **Faithfulness check (optional, about 3–4K tokens):** the agent acts as a strict judge. It checks up to 3 sampled claims against the source text they cite.
- **Offline benchmark:** `python evaluation.py --benchmark` runs the 10 cases in `eval_set.json`. They include a fictional topic, which must fail honestly, and a too-short topic. It writes resumable CSV and Markdown results to `eval_results/`.

## GitHub and deployment

Both are explained step by step in the guide:

- [GitHub](docs/GUIDE.md#15-github): create the repository, check that no secret is staged, and push.
- [Streamlit Community Cloud](docs/GUIDE.md#16-deploying-to-streamlit-community-cloud): connect the repository, choose Python 3.12, and paste your key into **App settings → Secrets**.

## Limitations (honest)

- **Groq's free tier is the main constraint.**
  - The limits are 8,000 tokens per minute and 200,000 per day.
  - A Standard report uses roughly 12–16K tokens, so expect short waits between calls and around 12–16 Standard reports a day.
  - Your account's limits may differ, so check Settings → Limits in the Groq console.
- **Free search can be rate-limited or blocked.** When that happens, the app retries and then explains the problem. Some sites block automated reading, and those sources are used as snippets only.
- **History is per session.** Without a database, it is lost when the browser tab is closed.
- **The faithfulness judge is the same model** that wrote the report. Treat it as a second look, not proof.
- **Not tested live here.** The live Groq and search calls could not be run while this project was built (the build environment had no outbound access to them). The offline tests, the exports and the full interface were tested with a fake model. Run the three live tests above before relying on the app.
- **Scanned PDFs need OCR first.** The app doesn't include OCR.

## Future improvements

- Saving history to a file the user downloads and re-uploads.
- OCR for scanned PDFs, for example with Tesseract.
- More research APIs, for example Semantic Scholar or government data portals.
- A configurable token budget in the interface.
- Benchmark trend charts across versions.

## Troubleshooting

See [docs/GUIDE.md, section 17](docs/GUIDE.md#17-troubleshooting). It covers missing keys, rate
limits, blocked searches, scanned PDFs, export errors and Streamlit Cloud problems.

## Licence notes

The fonts in `assets/fonts/` (Source Serif 4 and IBM Plex) are under the SIL Open Font
Licence. See `assets/fonts/OFL-NOTICE.txt`.
