# Research Desk: the build guide

This guide explains how the whole app works and why it is built this way. It covers every phase from setup to deployment, and you can read it in order or jump to a section. The Phase 0 verification report, which lists the versions, limits and sources I checked, is in [phase-0-report.md](phase-0-report.md).

**What was tested and what wasn't.** Every part of the app was run end to end in my sandbox with a *fake* model and a *fake* web, and passed:

- research at all depths, follow-ups and the document workspace;
- all four exports;
- the scorecard;
- every screen in a real browser, in light and dark themes, at 375, 768 and 1440 px.

My sandbox cannot reach Groq, DuckDuckGo or the research APIs. So the **first live run happens on your computer**, in step 1.5 below. If anything differs from what this guide says, tell me what you see and I'll fix it.

---

## Contents

1. [Setup](#1-setup)
2. [The architecture in plain language](#2-the-architecture-in-plain-language)
3. [What every file does](#3-what-every-file-does)
4. [The report object (schema)](#4-the-report-object-schema)
5. [How a research run works, step by step](#5-how-a-research-run-works-step-by-step)
6. [Token budgets](#6-token-budgets)
7. [Credibility tiers and hallucination prevention](#7-credibility-tiers-and-hallucination-prevention)
8. [Research modes](#8-research-modes)
9. [Follow-up questions](#9-follow-up-questions)
10. [Exports](#10-exports)
11. [My Document Workspace](#11-my-document-workspace)
12. [Evaluation](#12-evaluation)
13. [UI/UX: the Research Desk design](#13-uiux-the-research-desk-design)
14. [Testing plan and quality checklist](#14-testing-plan-and-quality-checklist)
15. [GitHub](#15-github)
16. [Deploying to Streamlit Community Cloud](#16-deploying-to-streamlit-community-cloud)
17. [Troubleshooting](#17-troubleshooting)
18. [Phase checklists](#18-phase-checklists)

---

## 1. Setup

### 1.1 Python 3.12

CrewAI supports Python 3.10 to 3.13, and Streamlit Cloud defaults to 3.12. Use **3.12 on your computer and on Cloud**, so both run the same thing.

```bash
python3.12 --version        # macOS / Linux
py -3.12 --version          # Windows
```

If 3.12 isn't installed, get the latest 3.12.x from <https://www.python.org/downloads/>. On Windows, tick **"Add python.exe to PATH"** during install.

### 1.2 Virtual environment and libraries

A virtual environment is a private set of libraries for this one project. It is the `.venv` folder, and it never goes to GitHub.

```bash
cd ai-research-agent
python3.12 -m venv .venv               # Windows: py -3.12 -m venv .venv
source .venv/bin/activate              # Windows: .venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt        # about 1 GB, a few minutes
```

**Why these versions.** `requirements.txt` uses two kinds of rule:

- **`==` (exact pin)** for the libraries whose functions we call directly and which change often. CrewAI releases almost daily.
- **`>=…,<…` (range)** for shared libraries that CrewAI also depends on (httpx, pydantic, python-dotenv, json-repair). A range lets pip pick a version both of us accept.

The exact file was installed on a fresh Python 3.12 environment, and everything resolved together.

### 1.3 Your Groq key

1. Create a key at <https://console.groq.com/keys>.
2. Copy the template: `cp .env.example .env` (on Windows, `copy .env.example .env`).
3. Paste the key after `GROQ_API_KEY=`. Use no quotes and no spaces.

`.env` is listed in `.gitignore`, so Git never uploads it.

While you are in the console, open **Settings → Limits** and check the numbers for `openai/gpt-oss-120b`. If your per-minute or per-day tokens are higher than 8,000 and 200,000, raise `GROQ_TPM_LIMIT` and `GROQ_TPD_LIMIT` in `utils.py`.

### 1.4 Check the setup (no internet, no tokens)

```bash
python tests/check_setup.py      # every line should say [ok]
python tests/test_offline.py     # "All 42 checks passed"
python tests/test_exporters.py   # writes sample files to tests/output/
```

### 1.5 First live tests (run them in this order)

| Command | Tests | Tokens | Correct output |
|---|---|---|---|
| `python tests/test_llm.py` | Groq key, model string, `reasoning_effort`, JSON → Pydantic | about 1–2K | `[ok] 'Groq is working.'`, then a list of 3 queries |
| `python tests/test_search.py` | ddgs web and news search, the 5 research APIs, page reading | none | 5 results with tiers, a few `[ok] … chars` page reads. A `[skip]` on some pages is normal |
| `python tests/test_agent.py` | one complete Quick research run | about 6–9K | progress lines, a title, 3–4 findings with citations, cited sources, the scorecard |

Then start the app:

```bash
streamlit run app.py              # the real app
streamlit run tests/demo_app.py   # the same app with a fake model: no key, no tokens
```

---

## 2. The architecture in plain language

### Code collects, the agent thinks

Think of a newspaper with three roles:

- **The researcher (Python code)** goes to the library, photocopies pages, numbers every copy and files it.
- **The writer (the CrewAI agent)** reads only the numbered copies and writes the article, citing copy numbers.
- **The fact-checker (Python code again)** checks every citation number against the file before printing.

Here is why this is more reliable than letting the model do everything:

1. **The model never types a URL or a source name.** It writes `[3]`, and code turns that into a link from the registry it built itself. An invented link has nowhere to come from.
2. **Code behaves the same every time.** Searching, deduplicating, ranking and trimming are deterministic, so you can test them alone. A model doing those steps would vary between runs and spend tokens.
3. **Budgets are counted by code.** Groq's free tier allows 8,000 tokens per minute. A tool-using agent re-sends its whole growing conversation on every step and would blow through that limit. Our agent gets one small, pre-trimmed prompt per call.
4. **One structured object feeds everything.** The agent returns data, not formatted prose. That data includes claims, evidence, interpretation, confidence and citation numbers. The web page, PDF, Word file, slides, scorecard and follow-ups are all generated from that same object, so they can never disagree.
5. **Failures are honest.** If code finds no sources, the agent is never asked to write, so a report from memory is impossible.

### Flow

```
 You (Streamlit: app.py + ui_components.py)
   │  topic, mode, depth, length, time focus
   ▼
 run_research()  ───────────────────────────── research_agent.py
   │ 1 PLAN      [AGENT]  mode config → 3–8 search queries (JSON)
   │ 2 SEARCH    [CODE]   ddgs web/news + Wikipedia/arXiv/OpenAlex/Crossref/PubMed by mode
   │ 3 FILTER    [CODE]   normalise URLs, drop duplicates & excluded sites, assign tiers, rank
   │ 4 READ      [CODE]   trafilatura on the best pages (timeouts, size caps, skip failures)
   │ 5 REGISTER  [CODE]   number every source [1]..[n]: title, URL, tier, date, read status
   │ 6 BUDGET    [CODE]   pick the most relevant paragraphs so each call fits ~7K tokens
   │ 7 WRITE     [AGENT]  1–3 calls: findings → analysis (JSON checked by Pydantic)
   │ 8 CHECK     [CODE]   unknown [n] and URLs removed; uncited findings → Could Not Verify
   │ 9 SCORE     [CODE]   quality scorecard
   ▼
 report dict (cached 6 h, kept in session history)
   ├─► report view · contents · sources panel · scorecard · research details
   ├─► follow-up chat ── run_followup() [AGENT] (+ optional mini-search → registry)
   ├─► exports: Markdown · Word · PDF · PowerPoint (slides: agent outline → code draws)
   └─► optional faithfulness check [AGENT as judge]

 My Document Workspace: your file → documents.py → run_document_job() [AGENT]
   → optional fact-check (search → registry → cited) → same exporters
```

### One agent, several small tasks (and why)

The rules say: a single agent, no multiple agents or crews working together, no hierarchy. That is exactly what we have.

- **One agent.** `make_agent()` always builds the same agent: same role, same goal, same rules, no tools, no delegation. Only `max_tokens` changes between calls.
- **One task per call.** Each model call is a `Crew` with that one agent and one task, run sequentially.

Why split the work into several small tasks instead of one big one:

- **Size.** One big prompt would not fit in 8K tokens per minute.
- **Repair.** A failed small call can be retried cheaply.
- **Focus.** The model writes better when each call has one clear job.

---

## 3. What every file does

```
ai-research-agent/
├── app.py              Page layout and navigation only. Calls backend functions, stores results in session state.
├── ui_components.py    Small render functions: form, progress, report HTML, scorecard, sources, export, chat.
├── research_agent.py   The single agent, call_agent() (pacing, retries, JSON repair), run_research(),
│                       run_followup(), make_slide_outline(), run_document_job(), judge_faithfulness().
├── schema.py           Pydantic models for everything the agent returns (+ compact JSON examples).
├── modes.py            The 6 research modes, depths, lengths and time focus, as plain dictionaries.
├── search_tools.py     Search, research APIs, page reading, dedup, credibility tiers, source registry.
├── documents.py        Reading PDF/DOCX/TXT/MD and pasted text; scanned-PDF detection; chunking.
├── exporters.py        report_blocks() + Markdown, Word, PDF, PowerPoint.
├── evaluation.py       Scorecard, optional faithfulness check, offline benchmark (also a command-line script).
├── utils.py            Secrets, friendly errors, token budgets and limiter, JSON repair, citation checks, cache.
├── eval_set.json       Benchmark topics for every mode + fictional, hard-to-verify, vague and too-short topics.
├── requirements.txt    Exact/bounded library versions for Python 3.12.
├── .env.example        Template for your local secrets (copy to .env).
├── .gitignore          Files Git must never upload.
├── README.md           Project overview.
├── assets/style.css    The only custom CSS, commented, with risky selectors marked [risk].
├── assets/fonts/       Source Serif 4, IBM Plex Sans/Mono (OFL licence) for PDF export.
├── docs/               This guide and the Phase 0 report.
├── tests/              check_setup, offline end-to-end test with fakes, live tests, demo app.
└── .streamlit/         config.toml (theme) and secrets.toml.example.
```

Two changes from the planned structure:

- **I added `schema.py`.** The report shape is used by the agent, the UI, the exporters and the evaluation. In its own small file, the exporters and evaluation can use it without loading CrewAI.
- **I added `tests/`.** It holds the standalone test snippets so they don't clutter the app files.

`app.py` never searches or calls the model directly. It only calls `research_agent` functions and shows their results. That keeps the UI and the backend independent: you could put a different interface on the same backend.

---

## 4. The report object (schema)

The agent returns JSON. `schema.py` validates it, and `research_agent.assemble_report()` adds the parts code owns. The result is a plain dictionary, the **report dict**:

| Field | Written by | Meaning |
|---|---|---|
| `id`, `kind` | code | Unique id. `kind` is `"research"` or `"document"` |
| `metadata` | code | Topic, mode, depth, length, time focus, queries, search statistics, model, created time, duration, estimated tokens, `from_cache` |
| `title` | agent | Specific report title |
| `executive_summary` | agent | 3–5 sentences with `[n]` citations |
| `introduction_and_scope` | agent | What the report covers and what it doesn't |
| `key_findings[]` | agent | Each finding has these parts: |
| `key_findings[].claim` | agent | One-sentence finding |
| `key_findings[].evidence` | agent | What sources actually say, attributed and cited |
| `key_findings[].interpretation` | agent | The agent's own analysis, shown separately and labelled |
| `key_findings[].confidence` | agent | High / Medium / Low |
| `key_findings[].confidence_reason` | agent | Why, e.g. "3 independent Tier 1–2 sources agree" |
| `key_findings[].citations` | agent, checked by code | Registry numbers only. Invalid numbers are removed |
| `detailed_analysis[]` | agent | `{heading, body, citations}` sections |
| `perspectives[]` | agent | `{position, held_by, summary, evidence, evidence_strength, citations}` |
| `agreement`, `disagreement`, `weight_of_evidence` | agent | Where sources agree and differ, and which side the evidence favours (no false balance) |
| `extra_sections[]` | agent | Mode-specific sections, e.g. Version Notes, Timeline, Research Gaps |
| `could_not_verify[]` | agent + code | `{item, reason}`. Code adds findings that ended up with no valid citation |
| `limitations[]` | agent | Limits of this research. Exports also add three fixed honesty notes |
| `conclusion` | agent | Closing paragraph |
| `background_note` | agent (optional) | Only if you enable it. Shown as "Background knowledge (not researched)" |
| `addenda[]` | code | Follow-up answers you chose to add: `{question, answer, citations, searched}` |
| `sources[]` | **code only** | The registry: `id, title, url, domain, tier, tier_label, tier_reason, date, source_type, read_status, preprint, cited, text` |
| `warnings[]` | code | Plain-language notices shown above the report |
| `flags[]` | code | Every citation problem found and what was done about it |
| `scorecard` | code | Output of `evaluation.compute_scorecard()` |

Document-workspace reports use the same shape:

- `detailed_analysis[].origin` is `"document"` or `"added"`;
- fact-checked claims appear as `key_findings`, with a `verdict`.

---

## 5. How a research run works, step by step

### 5.1 The LLM (`research_agent.make_llm`)

```python
LLM(model="groq/openai/gpt-oss-120b", api_key=key, temperature=0.2,
    max_tokens=max_tokens, timeout=120, reasoning_effort="low")
```

- `groq/` tells CrewAI (through LiteLLM) to use Groq. The rest is Groq's model ID.
- `temperature=0.2` keeps answers factual and consistent.
- `reasoning_effort="low"` matters because gpt-oss "thinks" before answering, and those hidden tokens count as output. Low effort leaves room for the real answer.
- `api_key` comes from `get_secret()`: Streamlit secrets first (that's where Cloud keeps them), then `.env` (so plain scripts work).

### 5.2 The agent and one call (`make_agent`, `call_agent`)

`call_agent(description, shape, Model, max_tokens)` does every model call in the app. Here is what it does each time:

1. **Waits for room.** `LIMITER.wait_for(prompt + max_tokens)` sleeps if that many tokens would exceed the per-minute limit. The UI shows "Pausing 40 s to stay within Groq's free per-minute limit".
2. **Runs the call.** It builds a `Crew(agents=[agent], tasks=[task])` and runs `kickoff()`.
3. **Handles rate limits.** On a 429 it reads Groq's "try again in 12.3s", waits that long and retries, up to 3 times.
4. **Parses the answer.** `extract_json()` finds the JSON in the answer (even inside ```` ``` ```` fences) and repairs small mistakes. `Model.model_validate()` then checks types and allowed values.
5. **Retries once on bad JSON.** If parsing fails, it asks again once, including the error message. If that also fails, you get a friendly "the model's answer couldn't be read" error.

**Why JSON-in-the-prompt rather than CrewAI's `output_pydantic`.** Groq supports structured outputs for this model, but CrewAI's converter can make an *extra* model call when parsing fails, which costs unpredictable tokens. Our way costs exactly one call, or two on a retry. The model sees a short JSON example (`schema.*_SHAPE`), which is far cheaper in tokens than a full JSON Schema. `tests/test_llm.py` confirms it works with your key.

### 5.3 Search and reading (`search_tools.collect_sources`)

1. **Searching.** The planned queries run one at a time, with a short pause between them, which makes rate limiting on shared servers less likely.
   - **Recent news**, and **Current Affairs** mode, use `DDGS().news(..., timelimit="m")`.
   - Otherwise it uses `DDGS().text(..., timelimit=None or "y")`.
   - Each search retries with backoff (3 s, then 8 s) and is cached for an hour.
2. **Research APIs** for the mode run once, with the topic. Each fails soft: on any error it returns `[]` and the run continues with web results.
3. **URL normalisation** removes tracking parameters, `www.` and trailing slashes, so the same page found by two queries counts once (and gets a ranking bonus).
4. **Tiers and exclusion** (see section 7).
5. **Ranking** combines:
   - tier (Tier 1 = 3 points, Tier 2 = 2, Tier 3 = 0.8);
   - preferred domains for the mode (+1.5);
   - search position;
   - being found by several queries;
   - recency, in modes where it matters.
6. **Reading.** `fetch_page()` downloads the best pages, 4 at a time:
   - 10-second timeout and a 2 MB cap (5 MB for PDFs);
   - HTML goes through trafilatura, which returns the main text, title and date;
   - PDFs go through pypdf;
   - blocked, paywalled (HTTP 401/402/403/429/451), tiny or non-HTML pages fall back to their search snippet and are recorded as "snippet".
7. **Near-duplicates.** Pages sharing most 5-word phrases (Jaccard similarity of at least 0.6) are dropped.
8. **Excerpts.** `best_excerpt()` keeps each page's opening paragraph, plus the paragraphs with the most topic words, within the per-page budget.
9. **The registry** numbers every remaining source `[1]..[n]` in ranking order.

### 5.4 Writing (`write_report`)

| Depth | Writing calls |
|---|---|
| Quick | 1 call writes the whole report |
| Standard | Findings call, then analysis call |
| Deep | Findings over odd-ranked sources, findings over even-ranked sources, then analysis |

The analysis call sees the findings already written, plus excerpts, so it builds on them instead of repeating them.

### 5.5 Checking and assembling (`assemble_report`)

1. **Fill in citation lists.** `[n]` markers written in the text are added to each item's `citations` list.
2. **Run `verify_citations()`.** It removes every citation number not in the registry, and every URL not in the registry, which becomes "[link removed: not a researched source]". Each fix is recorded in `flags`.
3. **Move uncited findings.** Findings left with no valid citation go to **Could Not Verify**.
4. **Mark cited sources.** Sources are marked `cited`, so the Sources list can show cited ones first.

### 5.6 Caching and history

- **Cache.** Identical requests (same topic, mode and options) within 6 hours return a copy of the cached report instantly, using no tokens. The metadata strip shows "from cache".
- **History.** Earlier reports live in `st.session_state.history`. Clicking one reopens it without re-running anything. History lasts until you refresh or close the tab, because Streamlit keeps session state in memory and we use no database.

### 5.7 Progress you can trust

`run_research(progress=fn)` calls `fn(stage, message)` at each real step: planning, searching (one line per query), reading, writing (one line per call), waiting (for rate limits), checking, done. `ui.progress_reporter()` writes these into `st.status`. There are no fake percentages.

---

## 6. Token budgets

Groq's free tier for this model allows **8,000 tokens per minute and 200,000 per day**. Every call keeps *prompt + max_tokens* under about 7,000 (`SAFE_CALL_TOKENS`), and `_budget_for_sources()` sizes the excerpts to fit.

| | Quick | Standard | Deep |
|---|---|---|---|
| Queries / results per query | 3 / 5 | 5 / 6 | 8 / 8 |
| Pages read in full | 3 | 6 | 10 |
| Model calls | 2 | 3 | 4 |
| Largest call (measured with realistic pages) | about 2.3K prompt + up to 3K answer | about 3.4K + 3K | about 3.2K + 3K |
| Tokens per report (estimate) | about 6–9K | about 12–16K | about 16–22K |
| Time | about 1 min | about 2–3 min | about 4–6 min |

| Report length | Max answer tokens per writing call | Words |
|---|---|---|
| Brief | 2,200 | about 600–900 |
| Standard | 2,600 | about 1,200–1,800 |
| Comprehensive | 3,000 | about 2,000–3,500 |

Other features:

| Feature | Budget |
|---|---|
| Follow-up | Report summary + top 5 relevant excerpts + last 3 Q&As (shortened), with an answer of up to 1,300 tokens |
| Slides | Condensed report (about 3K), with an outline of up to 2,400 tokens |
| Documents | Chunks of about 2,800 tokens, each summarised; a 60,000-word cap |
| Faithfulness check | 3 claims with their cited excerpts, about 3–4K tokens |

The sidebar shows an estimate of today's use for the whole deployment. Everyone using the same app shares one key.

---

## 7. Credibility tiers and hallucination prevention

### Tiers (`search_tools.classify_tier`)

| Tier | Meaning | How it's decided |
|---|---|---|
| Tier 1 | Primary / official | `.gov`, `.int`, `.europa.eu` and similar; standards bodies; journal publishers; official-docs domains; `docs.`/`developer.`/`investor.` subdomains; PubMed; journal records from OpenAlex/Crossref |
| Tier 2 | Established secondary | `.edu`/`.ac.uk`; established news and wire services; Wikipedia and Britannica; recognised publications; GitHub; preprint servers (labelled *preprint, not peer-reviewed*) |
| Tier 3 | Other | Blogs, forums, unknown sites; listicle or clickbait-style titles |
| Excluded | Not used | Social feeds, pin boards and homework farms (`EXCLUDED_DOMAINS`), plus pages where one word is over 6% of the text (keyword stuffing) |

Every list is a plain Python set at the top of `search_tools.py`, so you can edit it. **Tiers describe the type of website, not whether a claim is true.** The UI and exports say so.

### Two layers against hallucination

**Layer 1: instructions.** `AGENT_BACKSTORY` holds the rules the agent sees on every call:

- cite only `[n]`;
- never write URLs;
- never invent sources, statistics, quotes or studies;
- never claim to have visited a website;
- keep evidence and interpretation separate;
- prefer claims from several sources;
- list what can't be supported as Could Not Verify;
- no assumptions presented as facts;
- no false balance;
- flag Tier 3 sources and preprints.

**Layer 2: code.** Code then enforces what instructions can't guarantee:

- Every citation number is checked against the registry; invalid ones are removed and listed.
- Every URL in the agent's text is checked; unknown URLs are removed.
- Findings left without a valid citation are moved to Could Not Verify.
- The Sources list is printed from the registry, never from model text.
- **If search fails or finds nothing, no report is written.** You get an honest error instead.
- The optional background note sits in its own section, labelled *not researched*.

**What code can't prove.** Code can prove a citation *exists*. It cannot prove the source *supports* the claim. The optional faithfulness check (section 12) samples claims and asks a strict judge to compare them with the cited text.

---

## 8. Research modes

Each mode is a dictionary in `modes.py` with these keys:

- `query_angles`
- `preferred_domains` (a ranking boost)
- `apis` (research databases to query)
- `use_news` and `recency`
- `writing_style`
- `extra_sections`
- `evaluation_focus`
- example topics

| Mode | Extra section | APIs | Special handling |
|---|---|---|---|
| General Learning | none | Wikipedia | Explains terms and gives examples |
| Technical Documentation | Version Notes | none | Prefers official docs; recency matters; flags outdated info |
| Market Analysis | Data Caveats | none | Numbers only with source and date; free-data warning |
| Current Affairs | Timeline | none | News search, dates, developing-story notice |
| Scientific Research | Study Quality Notes | OpenAlex, arXiv, PubMed | Preprints labelled; findings kept separate from media coverage |
| Academic Research | Research Gaps | OpenAlex, Crossref, Wikipedia | Literature-review style |

**Time focus:**

- **Any time:** no date filter.
- **Past year:** `timelimit="y"`.
- **Recent news:** news search with `timelimit="m"`.

`ddgs` combines several search engines, and some may ignore the filter. That's why each source's own date is shown and used by the Recency metric.

**Perspectives.** Every mode asks for the main positions, who holds each, its evidence and its strength (Strong / Moderate / Weak). The report then gives where sources agree, where they disagree, and the **weight of evidence**, which says plainly when one side is better supported. They render as a table plus three labelled paragraphs.

---

## 9. Follow-up questions

`run_followup(report, question, history, allow_search)` works in two steps:

1. **Answer from stored sources.** It asks the agent using the report summary, the 5 stored sources most relevant to the question, and short summaries of the last 3 questions and answers, never the full chat.
2. **Search only if needed.** If the agent says the sources can't answer (`needs_search`) and you allowed searching, `search_more()` runs one or two small searches. It reads up to 2 new pages, adds them to the report's registry with new numbers, and asks again. The answer then says it used an extra search, and the new sources appear in the Sources list marked "added by follow-up".

Answers go through the same citation check. **Add to report** stores the answer as an addendum, which then appears on screen and in every export.

---

## 10. Exports

Every format is built from `exporters.report_blocks()`, the same list of blocks the screen uses, so the sections and their order always match.

| Format | What you get | Limits |
|---|---|---|
| Markdown | Headings, findings with labelled evidence and interpretation, a table, clickable citations and sources | Plain text |
| Word (.docx) | Title page; a table of contents field; real Heading 1–3 styles (navigation pane works); shaded evidence; ruled italic interpretation; coloured confidence labels; page numbers; clickable citations and sources | The contents list fills when Word updates fields (right-click it → Update Field, or press F9). Fonts are Georgia and Arial, which nearly every computer has |
| PDF | Title page; contents with page numbers and links; bookmarks; header and footer; evidence and interpretation styling; confidence labels; a perspectives table; clickable sources | Embeds Source Serif 4 and IBM Plex. No images or charts. Characters missing from those fonts (e.g. Chinese) may not render |
| PowerPoint | Title, agenda, one slide per finding or section, perspectives, limitations, conclusion, sources (from the registry). At most 5 short bullets per slide; detail and citations go in the speaker notes | Uses Georgia and Arial, because python-pptx can't embed fonts. No images or charts |

**Length choices:**

- **Documents:** Summary, Standard, or Full + research details. The last one adds queries, citation checks and the scorecard.
- **Slides:** 5–16 slides, including title, agenda and sources.

The slide outline is the only export that uses the model: one call, cached per report and slide count.

---

## 11. My Document Workspace

1. **Read the input** (`documents.read_document` / `read_pasted`):
   - PDFs use pypdf; DOCX uses python-docx, including headings and tables; TXT and MD are decoded safely.
   - **Scanned PDFs** average under 60 characters of text per page. They are detected and explained, because reading them needs OCR, which isn't included.
   - Password-protected PDFs and very short documents get clear messages.
   - Documents over 60,000 words are refused, with a request to trim.
2. **Chunk and summarise faithfully.** Text is split at paragraph boundaries into chunks of about 2,800 tokens. Each chunk is summarised with the instruction "add nothing", and gives up to 3 checkable claims.
3. **Shape the output** for what you asked: slides, PDF, Word or summary, at your length, purpose and audience. Anything the agent adds goes in a section marked **"Added by the assistant · not from your document"**.
4. **Enhance with web research (optional).** Up to 3 claims (5 for long documents) are each searched and checked by one judge call:
   - *Supported* or *Contradicted* claims appear as cited findings;
   - *Not verified* claims go to Could Not Verify.

The result uses the same report shape, so the same preview and exporters work.

---

## 12. Evaluation

### Part 1: the per-run scorecard (`compute_scorecard`, free, every run)

| Metric | How it's calculated | Good looks like |
|---|---|---|
| Citation validity | valid citations ÷ (valid + invalid ones removed) | 98–100% |
| Citation coverage | findings with at least one valid citation ÷ all findings (including those moved to Could Not Verify) | 100% |
| Sources and diversity | cited sources; distinct sites ÷ cited sources | 5+ cited, at least 0.7 distinct |
| Credibility mix | share of cited sources in Tier 1 / 2 / 3 | Tier 1+2 at least 70% |
| Full text read | share of cited sources read in full / abstract / snippet | 50%+ full text |
| Recency (technical, market, current) | cited sources dated within 730 / 730 / 90 days ÷ cited sources (undated count as not recent) | 60%+ |
| Section completeness | expected sections (including the mode's extras) present and non-empty | All |
| Evidence vs interpretation | findings with both parts present and different | Every finding |
| Could not verify · fixes | counts only, no grade | Informational |

Metrics that matter most for the selected mode are marked "mode focus". **They are never added into one score**: a single number would hide which part is weak (for example, perfect citations but only Tier 3 sources), and any weighting would be arbitrary.

### Part 2: the faithfulness check (optional, off by default)

The button under the scorecard runs it. It costs about 3–4K tokens:

- It samples up to 3 findings that cite a full-text source.
- It sends each claim with the cited excerpts to the agent acting as a strict judge.
- It reports Supported / Partially supported / Not supported, with reasons.

The judge is the same model, so treat it as a second look, not proof.

### Part 3: the offline benchmark

```bash
python evaluation.py --benchmark              # all cases in eval_set.json, Quick depth
python evaluation.py --benchmark --limit 3    # first 3 only
python evaluation.py --benchmark --depth Standard --delay 90
```

How it behaves:

- It runs each case with `use_cache=False` and waits 70 s between cases, for Groq's per-minute limit and for the search engines.
- It appends a row to `eval_results/results-<date>-<depth>.csv` after every case, and writes a Markdown table next to it.
- It is **resumable**: if the daily budget runs out, it stops cleanly, and the same command continues tomorrow.
- `behaviour_ok` checks each case's expectation:
  - `report`: a report with findings.
  - `no_confident_report`: a fictional topic must fail honestly, or have no High-confidence findings plus Could Not Verify items.
  - `hedged_report`: a report with Could Not Verify items.
  - `topic_error`: a friendly refusal for a too-short topic.

The 10 cases use about 70–100K tokens at Quick depth, so compare versions by re-running after a change.

---

## 13. UI/UX: the Research Desk design

### Direction

"The Research Desk" is an editorial, paper-and-ink interface where **the report is the hero**. It is calm, precise, typographic and trustworthy. Four principles:

- content first;
- trust is visible: citations, tiers, confidence and limitations are always one glance away;
- quiet confidence: no hype;
- honest states: real progress, real errors, real limits.

### Design system

| Token | Light | Dark (night reading) | Use |
|---|---|---|---|
| Paper | `#FBFAF7` | `#151513` | Page background |
| Panel | `#F2EFE9` | `#1F1E1B` | Inputs, evidence blocks |
| Ink | `#1A1A1A` | `#E9E6DF` | Text |
| Muted ink | `#5C5A55` | `#A39F96` | Metadata, help text |
| Hairline | `#E2DDD3` | `#33312C` | Rules and borders, used instead of shadows |
| Accent (ink-green) | `#1F4D3A` | `#8CC3A6` | The one accent: primary button, links, citations, interpretation rule |
| High confidence | `#2F6B3F` on `#E3EFE6` | `#93CFA6` on `#1D2A21` | Always with the word "High" |
| Medium / warning | `#7A5200` on `#F5ECD9` | `#E2B867` on `#2D2617` | Always with a word |
| Low / error | `#9B2C2C` on `#F6E2E0` | `#EE9F97` on `#33201E` | Always with a word |

**Contrast** (WCAG AA needs 4.5:1 for body text). Values were calculated with the WCAG luminance formula:

- **Light:** ink on paper 16.7, muted ink 6.6, accent 9.2, confidence tags 5.4–6.0, button text on accent 9.6.
- **Dark:** ink 14.7, muted 6.9, accent 9.1, confidence tags 7.4–8.3.

Every pair passes. Colour is never the only signal: every tag carries its word.

**Typography** (all from Google Fonts, all verified to load):

- **Source Serif 4** for the report and headings;
- **IBM Plex Sans** for the interface;
- **IBM Plex Mono** for metadata, citations and URLs.

The reading column is capped at about 70 characters per line, with a 17 px body and 1.65 line height. That gives five type sizes: title 2.1 rem, section 1.45 rem, subsection 1.125 rem, body 1.0625 rem, labels 0.72–0.85 rem.

**Shape and space:**

- 4 px radius (3 px on tags);
- hairline borders, no shadows;
- spacing in steps of 0.25 rem;
- a left-aligned grid.

**Icons:** Material icons only where they help scanning (navigation, export, download, scorecard). No emojis anywhere.

### Copy deck

| Place | Copy |
|---|---|
| Wordmark | Research Desk |
| One honest sentence | "Enter a topic and an agent searches the web, reads the most credible pages it can reach, and writes a report where every claim points to a numbered source. It can be wrong: tiers are heuristic, free search is limited, and anything it couldn't verify is listed as such." |
| Topic label / placeholder | Topic or question / "e.g. How do heat pumps work in cold climates?" |
| Primary button | Research this topic |
| Too-short topic | "Please write a slightly longer topic. At least a few words helps the agent plan good searches…" |
| Progress stages | Planning … / Searching (2/5): query / Reading 6 pages … / Writing key findings (1/2) … / Pausing 40 s to stay within Groq's free per-minute limit … / Checking every citation against the source registry … |
| Warning block title | Read with care |
| Evidence label | Evidence · what sources say |
| Interpretation label | Interpretation · analysis, not stated by sources |
| Confidence | Confidence: High / Medium / Low · reason |
| Scorecard | Quality checks: 5 good · 2 fair · 1 need attention |
| Tier note | Tiers are heuristic: they describe the type of site, not whether a claim is true. |
| Follow-up | Ask a follow-up question / "Answers use this report's sources first…" / Add to report |
| Export | Export / Prepare PDF file / Download PDF |
| History empty | Reports you create appear here. History lasts until you close or refresh this tab. |
| Workspace intro | "Bring your own report or notes and turn them into slides, a PDF, a Word document or a summary. Your content stays faithful; anything the assistant adds is labelled, and facts checked on the web are cited." |
| Errors | Each error in `utils.ERROR_MESSAGES` has a title (what happened) and an action (what to do) |

### Wireframes

```
DESKTOP (1440)                                         PHONE (375)
┌──────────┬──────────────────────────────────────┐   ┌─────────────────────┐
│ Research │ Research Desk                        │   │ » (sidebar toggle)  │
│ My Docs  │ one honest sentence                  │   │ Title               │
│──────────│ [mode pills……………………………]           │   │ meta · meta · meta  │
│ History  │ ┌ topic ─────────────────────────┐   │   │ [New research]      │
│  topic 1 │ └────────────────────────────────┘   │   │ ▌Read with care     │
│  topic 2 │ examples (pills)                     │   │ ▸ Contents          │
│ [Clear]  │ Depth   Length   Time focus          │   │ ▸ Quality checks    │
│──────────│ ▸ More options                       │   │ Executive Summary   │
│ tokens ▬ │ [Research this topic]                │   │ …reading column…    │
└──────────┴──────────────────────────────────────┘   └─────────────────────┘
RESULTS (1440)
┌──────────┬───────────────────────────┬─────────────┐
│ sidebar  │ Title            [New]    │             │
│          │ mono metadata strip       │             │
│          │ ▌Read with care …         │ CONTENTS    │
│          │ ▸ Quality checks          │ 1 Summary   │
│          │   Run faithfulness check  │ 2 Findings… │
│          │ Executive Summary         │ CITED       │
│          │ Key Findings              │ [1] title   │
│          │   ░ evidence ░            │     domain  │
│          │   ┃ interpretation        │ [2] …       │
│          │   CONFIDENCE: HIGH · [1]  │             │
│          │ … Perspectives table …    │             │
│          │ ┃ Could Not Verify        │             │
│          │ ┃ Limitations             │             │
│          │ Sources                   │             │
│          │ ▸ Research details        │             │
│          │ Export ─ Follow-up chat   │             │
└──────────┴───────────────────────────┴─────────────┘
```

### Layout and responsiveness

**Why wide layout.** The report needs a comfortable reading column, plus a side column for contents and sources, so a citation can be checked without scrolling away. The reading text itself is still capped at about 70 characters, so wide never means long lines.

| Width | What to check | What I saw |
|---|---|---|
| 1440 | Reading column + side column; sidebar open; no line longer than about 75 characters | ✓ |
| 768 | Side column still beside the report; segmented controls wrap instead of hiding options; no horizontal page scroll | ✓ scrollWidth = 768 |
| 375 | Single column; side column hidden; Contents becomes a collapsible block at the top; buttons at least 44 px tall; tables scroll inside their own box, never the page | ✓ scrollWidth = 375 |

### Streamlit rules followed

- Theming lives in `config.toml` (fonts, colours, radius, borders, both themes). `style.css` only styles the report HTML and a few details.
- No auto-generated class names are used. The few `data-testid` and `st-key-*` selectors are marked `[risk]` in `style.css`. They may need updating after a Streamlit upgrade.
- No external JavaScript and no paid components.
- **No accidental re-runs.** Research runs only on the button press. The export panel, follow-up chat and faithfulness check are `@st.fragment`s, so using them doesn't rerun the page. Downloads use `on_click="ignore"`. History clicks only change which stored report is shown.

### Design critique (honest)

- **Does anything still look templated?** The sidebar navigation and the chat avatars still look like Streamlit, because they're its components. The segmented controls look clean but generic. The report itself, the main thing you look at, is fully custom and reads like a document, not a dashboard.
- **Comfortable for 5+ minutes?** Yes, in my judgement: a serif body at 17 px, about 70-character lines, 1.65 line height, warm paper and hairline rules. Dark mode uses warm near-black rather than pure black, to reduce glare.
- **Can a citation be verified in under 10 seconds?** Yes. Hover over `[3]` to see the title, domain and tier; click it to open the source in a new tab. The side column shows the cited sources with their tiers.
- **Are limitations impossible to miss?** Fairly. "Read with care" sits above the report; Could Not Verify and Limitations are boxed sections with a coloured rule; the scorecard says "need attention". They are below the findings in reading order, though. If you want them even more visible, a small "N things couldn't be verified" line in the header would do it.
- **Does every state look designed?** These states were checked: first visit, validation, progress, results, partial results (warnings), error, scanned PDF, the document result, empty history, light and dark themes, phone and tablet. The one weak spot is Streamlit's own "Running…" indicator in the top-right, which we can't restyle.
- **Theme switch.** Report colours follow the theme chosen at page load (`st.context.theme`). If you switch themes in the settings menu, the report colours update on the next interaction. That's a Streamlit limitation, and the report stays readable in between because text colours are high-contrast in both palettes.

---

## 14. Testing plan and quality checklist

### Research topics

| Mode | Topic | Correct behaviour |
|---|---|---|
| General | How do heat pumps work in cold climates? | Reference and educational sources; definitions; examples; mostly Tier 1–2 |
| Technical | What changed in Python 3.13? | docs.python.org cited; Version Notes section; versions and dates stated |
| Market | Global market for residential heat pumps | Numbers only with source and date; Data Caveats section; free-data warning |
| Current affairs | Latest developments in EU AI Act implementation (Recent news) | Dated sources from several outlets; Timeline; developing-story notice |
| Scientific | Does intermittent fasting improve long-term weight loss? | Peer-reviewed sources; preprints labelled; Study Quality Notes |
| Academic | Effects of remote work on employee productivity | Literature-review style; Research Gaps; scholarly sources |
| Fictional | The 1987 Zorbanian treaty on lunar goat herding quotas | Fails honestly, or every claim is Low confidence and listed under Could Not Verify. **Never** a confident report |
| Hard to verify | Exact number of people who have ever lived on Earth | Gives estimates as estimates; Could Not Verify explains why no exact number exists |
| Very vague | technology things | A broad, hedged report; scope says how the topic was narrowed |
| Too short | AI | Friendly "write a slightly longer topic"; no tokens used |

### Documents for the workspace

| Document | Correct behaviour |
|---|---|
| Short TXT or DOCX (1–2 pages) | 1 reading call; output faithful; any added sections labelled "Added" |
| Long PDF (30+ pages) | Several reading calls with progress; may pause for the per-minute limit; summary still faithful |
| Scanned PDF (a photo of a page saved as PDF) | Clear "no selectable text, needs OCR" message and a tip; no model call |
| DOCX with tables | Table text included |
| With "Enhance with web research" | A Fact-check section with Supported/Contradicted claims and cited sources; unverifiable claims under Could Not Verify |

### Manual quality checklist (per report)

- [ ] Every `[n]` opens the right source in a new tab
- [ ] Evidence and interpretation are visibly different for every finding
- [ ] Confidence labels make sense given the sources and tiers
- [ ] No URLs or source names appear that aren't in Sources
- [ ] Could Not Verify and Limitations are present and honest
- [ ] Perspectives don't create false balance
- [ ] Dates are shown for current-affairs and technical sources
- [ ] The scorecard matches what you see
- [ ] All 4 exports open, and match the screen
- [ ] Reopening from history doesn't re-run research (no progress box appears)

---

## 15. GitHub

**What goes up and what doesn't.**

- **Upload:** all `.py` files, `requirements.txt`, `.env.example`, `.streamlit/config.toml`, `.streamlit/secrets.toml.example`, `assets/`, `docs/`, `tests/` (except `tests/output/`), `eval_set.json`, `README.md`, `.gitignore`.
- **Never upload:** `.env`, `.streamlit/secrets.toml`, `.venv/`.

### `.gitignore`, line by line

| Line | Why |
|---|---|
| `.env` | Your real key for local runs |
| `.streamlit/secrets.toml` | Your real key if you use Streamlit's secrets file locally |
| `.venv/`, `venv/` | About 1 GB of libraries; anyone can rebuild them from `requirements.txt` |
| `__pycache__/`, `*.pyc` | Python's automatic compiled copies |
| `exports/`, `eval_results/`, `*.log`, `tests/output/` | Generated files you can recreate |
| `.vscode/`, `.idea/`, `.DS_Store`, `Thumbs.db` | Editor and operating-system clutter |

### Steps

```bash
git init
git add .
git status                              # check: .env and secrets.toml must NOT be listed
git commit -m "Research Desk: first version"
# Create an empty repository on github.com (no README, no .gitignore), then:
git branch -M main
git remote add origin https://github.com/<your-username>/ai-research-agent.git
git push -u origin main
```

### Confirm no key was committed

```bash
git ls-files | grep -E "^\.env$|secrets\.toml$"     # should print nothing
git log -p | grep -n "gsk_"                          # should print nothing
```

GitHub's secret scanning may also warn you.

**If a key was ever pushed:**

1. Revoke it immediately at console.groq.com/keys, and create a new one.
2. Put the new key in `.env` and in Streamlit secrets.

Deleting the file in a later commit is **not enough**, because the key stays in the history and bots scan it within minutes.

---

## 16. Deploying to Streamlit Community Cloud

1. Push to GitHub (section 15).
2. Go to <https://share.streamlit.io> and sign in with GitHub.
3. Click **Create app → Deploy a public app from GitHub**.
4. Choose your repository, branch `main`, and main file `app.py`.
5. Open **Advanced settings**:
   - **Python version: 3.12**, to match your computer.
   - **Secrets:** paste
     ```toml
     GROQ_API_KEY = "gsk_your_real_key"
     CONTACT_EMAIL = "you@example.com"
     ```
     `CONTACT_EMAIL` is optional; it gives better Crossref and OpenAlex limits.
6. Click **Deploy**. The first install takes several minutes, because CrewAI is large.
7. **Logs:** open the app and click **Manage app** (bottom right). Every friendly error in the app has its full details here.
8. **Reboot or redeploy:** in Manage app, choose **Reboot app**. Pushing to GitHub redeploys automatically. Changing the Python version later means deleting and redeploying the app.

### Cloud limits and how the design handles them

| Limit | Handling |
|---|---|
| Memory about 690 MB–2.7 GB | The app imports at about 250 MB; history keeps 20 reports at most |
| App sleeps after 12 hours with no visitors | Visitors wake it with one click. Caches and history start empty after a sleep |
| Long runs | Deep is about 4–6 minutes. Closing the tab stops the run. Repeat requests come from the cache |
| Shared IP addresses, so search rate limits | Pauses between queries, retries with backoff, automatic fallback across search engines, a one-hour search cache, research APIs as a second source, and an honest error if everything fails |
| One Groq key for every visitor | The token limiter is shared by the whole app; the sidebar shows today's estimated use. Keep the app private, or share it carefully |

---

## 17. Troubleshooting

| Error or symptom | What it means | Why it happens | Fix |
|---|---|---|---|
| `ModuleNotFoundError: crewai` (or any library) | Library not installed in the active environment | `.venv` not activated, or install interrupted | Activate `.venv` (prompt shows `(.venv)`) and run `pip install -r requirements.txt` |
| `Could not find a version that satisfies crewai==1.15.26` | Wrong Python | Python 3.14+ or below 3.10 | Recreate `.venv` with 3.12 |
| `ImportError: cannot import name 'DDGS' from 'duckduckgo_search'` | Old package name | `duckduckgo-search` was renamed | We use `ddgs`: `pip install -r requirements.txt`, import `from ddgs import DDGS` |
| ImportError inside crewai after an upgrade | CrewAI changed its API | Upgrading past the pinned version | Go back to `crewai[litellm]==1.15.26`, or ask me to update the code |
| "Groq rejected the API key" / 401 | Bad key | Typo, extra space, revoked key | Copy the key again into `.env` / Secrets; restart the app |
| "The AI model isn't available" / 404 model_not_found | Model ID changed | Groq retires models over time | Check console.groq.com/docs/models; update `MODEL` in `research_agent.py` |
| "Groq's free per-minute limit was reached" | 8K tokens/minute exceeded | Several users, or follow-ups right after a Deep run | The app waits and retries automatically; if it still fails, wait a minute |
| "daily token limit is used up" | 200K tokens/day used | Many reports or a benchmark run | Wait for the reset; use Quick depth; cached reports still work |
| "Web search isn't responding" | Every search failed | Rate limiting, often on shared Cloud IPs | Wait 1–2 minutes and retry; try fewer queries (Quick) |
| "No usable sources were found" | Searches returned nothing usable | Very obscure or fictional topic, or a too-strict time filter | Rephrase; choose "Any time"; this is the honest outcome for fictional topics |
| Many "snippet" sources | Pages couldn't be read | Paywalls, bot blocking, JavaScript-only pages | Normal for some sites; Research details lists why each page failed |
| "The model's answer couldn't be read" | Invalid JSON, even after a retry | The answer was cut off (length) or malformed | Retry; choose Brief length; check the logs for the raw answer |
| "That file couldn't be read" | Unsupported or corrupt file | Wrong type, damaged, password-protected | Use PDF/DOCX/TXT/MD, or paste the text |
| "This PDF has (almost) no selectable text" | Scanned PDF | Pages are images | Run OCR (Google Drive, Acrobat), then upload the result |
| "The file couldn't be created" | An export failed | Unusual characters or a library problem | Try another format; check the logs |
| Word contents list is empty | TOC field not updated yet | Word fills fields on demand | Right-click → Update Field, or press F9 |
| Strange characters in the PDF | Character missing from the embedded font | e.g. CJK scripts | Use the Word or Markdown export for those languages |
| "The app isn't connected to Groq yet" on Cloud | Missing secret | Secrets not saved, or the wrong name | App settings → Secrets: `GROQ_API_KEY = "…"` (TOML with quotes); then reboot |
| Cloud build fails | Dependency conflict or wrong Python | Python version not 3.12, or an edited `requirements.txt` | Set 3.12 in Advanced settings (redeploy); restore `requirements.txt` |
| App very slow or restarting on Cloud | Memory or CPU throttling | Many simultaneous users | Use Quick depth; reboot the app |
| Styling looks off after a Streamlit upgrade | A `[risk]` selector changed | Streamlit renamed internals | Pin `streamlit==1.65.0` or update the `[risk]` rules in `style.css` |

---

## 18. Phase checklists

**Phase 1 (core research)**

- [ ] `check_setup.py` all `[ok]`
- [ ] `test_llm.py` prints queries
- [ ] `test_search.py` shows results and page reads
- [ ] `test_agent.py` prints a cited report
- [ ] `streamlit run app.py` produces a report

**Phase 2 (trust)**

- [ ] Tiers shown on every source
- [ ] Every finding has evidence, interpretation and confidence
- [ ] Could Not Verify present
- [ ] An invented citation is removed (the offline test proves it)

**Phase 3 (modes)**

- [ ] Each mode adds its extra section
- [ ] Current Affairs shows dates
- [ ] Scientific labels preprints
- [ ] Perspectives table and weight of evidence shown

**Phase 4 (follow-ups)**

- [ ] An answer cites stored sources
- [ ] An off-topic question triggers the extra search and says so
- [ ] Add to report shows the addendum

**Phase 5 (exports)**

- [ ] All 4 formats download and open
- [ ] Word navigation pane shows headings
- [ ] PDF links are clickable
- [ ] Slides have speaker notes

**Phase 6 (workspace)**

- [ ] Short and long documents work
- [ ] A scanned PDF gives the OCR message
- [ ] Added content is labelled

**Phase 7 (evaluation)**

- [ ] Scorecard appears
- [ ] Faithfulness check runs on demand
- [ ] `python evaluation.py --benchmark --limit 2` writes CSV + Markdown

**Phase 8 (UI)**

- [ ] Light and dark themes both look intentional
- [ ] 375 / 768 / 1440 px checks pass
- [ ] History reopens without re-running

**Phase 9 (deploy)**

- [ ] No key in git
- [ ] Cloud on Python 3.12 with secrets
- [ ] A Quick report works on the deployed app
