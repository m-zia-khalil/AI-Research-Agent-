# Phase 0: Verification and Feasibility Report

*AI Research Agent. Checked on 9 October 2026 against official docs and PyPI.*

How to read this report:

- **Verified** means I read it in current official docs, or installed the package and inspected it.
- **Docs only** means the docs say it, but I could not run it live. My sandbox blocks outbound calls to Groq, DuckDuckGo, Wikipedia, arXiv, OpenAlex, Crossref and PubMed, and I have no Groq key. The first live test happens on your machine in Phase 1, step 2.
- **Not verified** means I could not confirm it. I say so instead of guessing.

---

## Contents

1. [The one finding that shapes everything](#1-the-one-finding-that-shapes-everything)
2. [Part B: verification results](#2-part-b-verification-results)
3. [Token and content budgets](#3-token-and-content-budgets)
4. [Part C: feasibility of every feature](#4-part-c-feasibility-of-every-feature)
5. [Biggest risks and mitigations](#5-biggest-risks-and-mitigations)
6. [Replacements and corrections to the request](#6-replacements-and-corrections-to-the-request)
7. [Architecture in plain language](#7-architecture-in-plain-language)
8. [Final phase plan](#8-final-phase-plan)
9. [Decisions I need from you](#9-decisions-i-need-from-you)

---

## 1. The one finding that shapes everything

On Groq's free plan, `openai/gpt-oss-120b` allows **8,000 tokens per minute** and **200,000 tokens per day**.

The model itself can read 131K tokens. The free plan, though, lets us send only about 8K tokens per minute. In practice, **each single model call must stay under roughly 7,000 tokens, counting the prompt and the answer together.** A long report therefore cannot be written in one call. Bigger jobs are split into a few small calls with short waits between them.

This is why the design below:

- has Python do all the collecting and trimming, and sends the model only short, budgeted excerpts;
- gives the agent **no tools**, because a tool-using agent re-sends its whole growing conversation on every step and would break 8K quickly;
- writes Standard and Deep reports in 2–3 calls, spaced to respect the per-minute limit;
- caches results, so repeating a topic costs no tokens.

At about 8–22K tokens per report, 200K tokens a day gives you roughly **9 Deep, 13 Standard or 25 Quick reports per day**, shared with follow-ups, exports and evaluation. These are estimates; Phase 1 will measure real numbers.

---

## 2. Part B: verification results

### B1. Is `openai/gpt-oss-120b` on Groq, and what string does CrewAI need?

| Item | Finding | Status |
|---|---|---|
| Model on Groq | Yes. Model ID `openai/gpt-oss-120b`, 120B-parameter mixture-of-experts open-weight model from OpenAI | Verified ([Groq model page](https://console.groq.com/docs/model/openai/gpt-oss-120b)) |
| Deprecated? | No. Groq's deprecation page recommends it as the *replacement* for several retired models (for example Qwen 3 32B, retired July 2026) | Verified ([Groq deprecations](https://console.groq.com/docs/deprecations)) |
| CrewAI model string | **`groq/openai/gpt-oss-120b`**: the `groq/` provider prefix plus Groq's model ID | Verified ([CrewAI LLM docs](https://docs.crewai.com/en/concepts/llms)), and LiteLLM's model table recognises this exact key |
| How CrewAI talks to Groq | Through LiteLLM, not a native SDK. Install `crewai[litellm]` | Verified (CrewAI docs; installed and constructed `LLM("groq/openai/gpt-oss-120b")` without error) |
| API key variable | `GROQ_API_KEY` | Verified (CrewAI docs) |
| A live call works | Not tested. No key, and my sandbox blocks Groq | **Phase 1, step 2 tests this first** |

### B2. Context window, output and free-tier limits

| Item | Value | Status |
|---|---|---|
| Context window | 131,072 tokens | Verified (Groq model page; LiteLLM agrees) |
| Max output | 65,536 tokens | Verified (same) |
| Speed | About 500 tokens/second | Verified (Groq model page) |
| Requests per minute | 30 | Verified ([Groq rate limits](https://console.groq.com/docs/rate-limits)) |
| Requests per day | 1,000 | Verified (same) |
| **Tokens per minute** | **8,000** | Verified (same) |
| **Tokens per day** | **200,000** | Verified (same) |
| On limit | HTTP 429 with a `retry-after` header (seconds). `x-ratelimit-*` headers on every response | Verified (same) |
| Reasoning tokens | gpt-oss is a reasoning model. Its hidden "thinking" counts as output tokens. LiteLLM lists `reasoning_effort` as supported for this model, and we will set it to `low` | Docs only. Phase 1 confirms CrewAI passes it through |

Two caveats:

- Groq's page labels these numbers as the base limits, and says your own organisation's exact limits are on your console's limits page. Please open [console.groq.com/settings/limits](https://console.groq.com/settings/limits) once you have a key and tell me if they differ.
- I could not confirm from the docs whether Groq counts your `max_tokens` setting against the per-minute limit *before* answering. The budgets below keep **prompt + max_tokens under ~7,000**, which is safe either way.

### B3. CrewAI version, Python and syntax

| Item | Finding | Status |
|---|---|---|
| Current version | **crewai 1.15.26** (released 8 Oct 2026). crewai-tools is the same version | Verified (PyPI) |
| Python | `>=3.10, <3.14`. **Python 3.13 works; 3.14 does not** | Verified (PyPI and [install docs](https://docs.crewai.com/en/installation)) |
| Install | `pip install "crewai[litellm]==1.15.26"`. The docs prefer `uv`; plain `pip` in a virtual environment also works and is simpler for us | Verified (installed with pip) |
| Agent | `Agent(role=, goal=, backstory=, llm=, tools=[], allow_delegation=False, max_iter=, max_execution_time=, respect_context_window=, verbose=)`. All these fields exist in 1.15.26 | Verified (inspected installed package) |
| Task | `Task(description=, expected_output=, agent=, output_pydantic=Model)`. Also `output_json`, `guardrail` / `guardrails`, `guardrail_max_retries` (default 3) | Verified ([Tasks docs](https://docs.crewai.com/en/concepts/tasks) and package) |
| Crew | `Crew(agents=[agent], tasks=[...], process=Process.sequential, max_rpm=)`, run with `crew.kickoff(inputs={...})` | Verified (package) |
| Results | `result.pydantic`, `result.json_dict`, `result.raw`, `result.to_dict()`; per task: `task.output` | Verified (Tasks docs) |
| Custom tools | `from crewai.tools import tool, BaseTool` | Verified (package). **We won't need tools; see below** |
| LLM | `LLM(model="groq/openai/gpt-oss-120b", temperature=, max_tokens=, timeout=, ...)`. Extra settings are passed through to LiteLLM | Verified (constructed it) |

**Structured output with this Groq model**

- Groq supports JSON-schema structured output for `openai/gpt-oss-120b`, in both strict and best-effort mode. Verified: [Groq structured outputs](https://console.groq.com/docs/structured-outputs).
- Groq's own restriction is that **structured outputs cannot be combined with streaming or tool use**. Our agent has no tools, so this does not affect us.
- In strict mode, every field must be required and objects must forbid extra keys. Optional fields become "value or null".
- CrewAI's `output_pydantic` should work, but I have not seen it run against Groq. CrewAI's docs also say to test structured output per provider.

**Fallback.** We will test `output_pydantic` in Phase 1. Whether or not it works, our code will also:

1. take the raw text;
2. pull out the JSON (`json-repair` comes with CrewAI and fixes small mistakes such as trailing commas);
3. validate it with our Pydantic schema;
4. retry once with the validation error if it still fails.

This guarantees a valid report object, or an honest error.

### B4. DuckDuckGo search package

| Item | Finding | Status |
|---|---|---|
| Old package | `duckduckgo-search` (last release 8.1.1, July 2025). Its PyPI page says: *"This package (`duckduckgo_search`) has been renamed to `ddgs`!"* | Verified ([PyPI](https://pypi.org/project/duckduckgo-search/)) |
| Current package | **`ddgs` 9.16.0** (Aug 2026), MIT, Python ≥3.10 | Verified ([PyPI](https://pypi.org/project/ddgs/)) |
| Import | `from ddgs import DDGS` | Verified (installed) |
| Text search | `DDGS().text(query, region="us-en", safesearch="moderate", timelimit=None\|"d"\|"w"\|"m"\|"y", max_results=10, backend="auto")`. Returns `title`, `href`, `body` | Verified (PyPI and package source) |
| News search | `DDGS().news(query, timelimit="d"\|"w"\|"m", ...)`. Returns `date`, `title`, `body`, `url`, `source` | Verified (same) |
| Errors | `from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException` | Verified (installed) |
| Important change | `ddgs` is now a *metasearch* library. With `backend="auto"` it may answer from Bing, Brave, DuckDuckGo, Google, Mojeek, Yahoo, Wikipedia and others, not only DuckDuckGo. This makes it more robust, and it still needs no key | Verified (README) |
| Live search works | Not tested (sandbox blocked) | Phase 1, step 3 |

### B5. Free research APIs

| Source | Free? | Key? | Limits | Use in modes | Status |
|---|---|---|---|---|---|
| **Wikipedia** (MediaWiki API) | Yes | No; send a descriptive User-Agent | Could not open Wikimedia's rate-limit page | General Learning | Free and keyless is well established. **Limits not verified**; we will be gentle (one call at a time) |
| **arXiv API** | Yes | No | **1 request every 3 seconds, one connection at a time.** Metadata is CC0 | Scientific, Academic (labelled *preprint, not peer-reviewed*) | Verified ([arXiv terms](https://info.arxiv.org/help/api/tou.html)) |
| **OpenAlex** | Yes for basic use | Optional free key (10× daily budget). Updated Aug 2026: now a daily credit budget per request | Max 100 req/s; daily budget numbers are on a pricing page I did not confirm | Academic, Scientific | Partly verified ([OpenAlex auth](https://help.openalex.org/api/authentication/)). **Exact free daily budget not verified** |
| **Crossref** | Yes | No | Public pool: rate 5, concurrency 1. "Polite" pool (add your email as `mailto`): rate 10, concurrency 3 | Academic (DOI metadata, reference list) | Verified ([Crossref access](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/)) |
| **PubMed E-utilities** | Yes | Optional free key | 3 req/s without a key, 10 with one | Scientific (biomedical) | Verified ([NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25497/)) |

**Recommendation for a beginner:**

- **Start with:** web search (`ddgs`), Wikipedia, arXiv and OpenAlex. Each is one plain HTTP call that returns JSON or XML.
- **Add in Phase 3:** Crossref and PubMed, as small optional extras.
- **Market Analysis:** I found no free, stable, keyless market-data API to verify. That mode uses web search, preferring official domains such as company investor pages and government statistics sites.

### B6. Libraries for reading and exporting

| Job | Choice | Version | Why | Status |
|---|---|---|---|---|
| Page text extraction | **trafilatura** | 2.3.1 (Oct 2026), Apache-2.0 | Extracts main article text, title and **publication date**. Pure Python, using lxml wheels | Verified (PyPI; installed) |
| HTTP | **httpx** | 0.28.1 | Timeouts and redirects. (CrewAI already installs it) | Verified |
| PDF reading | **pypdf** | 6.19.0 (Sep 2026), BSD | Pure Python | Verified (installed) |
| DOCX reading and writing | **python-docx** | 1.2.0 (Jun 2025), MIT | Real Word heading styles, so the navigation pane works | Verified (installed) |
| PDF generation | **fpdf2** | 2.8.9 (Sep 2026), LGPL-3.0 | Pure Python. Supports a table of contents, internal and external links, page numbers, and embedded TTF fonts | Verified (installed). TOC and fonts are tested in Phase 5 |
| PPTX generation | **python-pptx** | 1.0.2 (Aug 2024), MIT | Builds slides and speaker notes. It is the only mature pure-Python option. **Its last release was over 2 years ago**, but it installs and imports fine on Python 3.13 | Verified (installed) |
| Markdown | Plain Python string building | — | No library needed | — |

Rejected options:

- **PyMuPDF**: AGPL licence. Too restrictive for a public repo you may reuse.
- **markdown-pdf**: AGPL.
- **WeasyPrint**: needs system libraries, which Streamlit Cloud would only install through an extra `packages.txt`.
- **reportlab**: works, but is harder for beginners than fpdf2.

### B7. Streamlit and Community Cloud

| Item | Finding | Status |
|---|---|---|
| Version | **streamlit 1.65.0** (2 Oct 2026), Python ≥3.10 | Verified (PyPI) |
| Widgets | `st.segmented_control`, `st.pills`, `st.status`, `@st.dialog`, `st.chat_input`, `st.file_uploader`, `st.download_button`, `@st.fragment`, `st.navigation` / `st.Page`, `st.cache_data` all exist in 1.65.0 | Verified (installed and checked) |
| Theming in `config.toml` | `[theme]` plus **`[theme.light]` and `[theme.dark]`**, so both themes can be designed. Options include `primaryColor`, `backgroundColor`, `secondaryBackgroundColor`, `textColor`, `borderColor`, `linkColor`, `baseRadius`, `buttonRadius`, `font`, `headingFont`, `codeFont`, `baseFontSize`, `fontFaces`, `showWidgetBorder` and per-colour overrides | Verified (`streamlit config show` on 1.65.0) |
| Google Fonts in theme | `font = "IBM Plex Sans:https://fonts.googleapis.com/css2?family=IBM+Plex+Sans&display=swap"` format is officially supported | Verified (config help text) |
| Fonts available | Source Serif 4, Newsreader, Literata, IBM Plex Sans and IBM Plex Mono all load from Google Fonts | Verified (HTTP 200) |
| Cloud Python | Any Python version that still receives security updates, chosen in **Advanced settings** at deploy. **Default is 3.12** | Verified ([deploy docs](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy)) |
| Cloud resources | About 0.078–2 CPU cores, **690 MB–2.7 GB memory**, up to 50 GB storage ("as of February 2024, may change") | Verified ([manage your app](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app)) |
| Sleeping | Apps with no traffic for 12 hours go to sleep; a visitor can wake them | Verified (same) |
| Secrets | Paste `secrets.toml` content (TOML) into the Secrets box, for example `GROQ_API_KEY = "gsk_..."` | Verified (deploy docs) |
| Our memory use | Importing CrewAI, Streamlit, ddgs, trafilatura and the exporters peaks at about **234 MB**. Installed size is about 1.2 GB, because CrewAI also installs vector-database packages we won't use. This fits Cloud's limits | Measured in my sandbox |

**Python version decision: 3.12, both locally and on Cloud.** Here is why:

- It is Streamlit Cloud's default.
- It is inside CrewAI's supported range.
- It is the most widely tested version for every library above.
- Python 3.14 is excluded, because CrewAI does not support it.

---

## 3. Token and content budgets

Every model call must stay under **about 7,000 tokens, prompt plus answer**.

Two rules of thumb:

- 1 token is about 0.75 English words.
- CrewAI adds about 400–600 tokens of its own framing to each call (Phase 1 measures this exactly).

| | Quick | Standard | Deep |
|---|---|---|---|
| Planned queries | 3 | 5 | 8 |
| Search results per query | 5 | 6 | 8 |
| Pages read in full | 3 | 6 | 10 |
| Text kept per page | about 600 tokens (best paragraphs) | about 600 | about 600 |
| Writing calls | 1 | 2 (findings, then analysis) | 3 (findings A, findings B, then analysis) |
| Model calls including planning | 2 | 3 | 4 |
| Tokens per report (estimate) | about 8K | about 15K | about 22K |
| Waits for the per-minute limit | none | about 1 | about 2 |
| **Estimated time** | **about 1 min** | **about 2–3 min** | **about 4–6 min** |

How report length relates to depth:

- Length (Brief / Standard / Comprehensive) mainly changes how many words each writing call may produce.
- Comprehensive reports top out at roughly **2,500–3,500 words**. That is an honest ceiling under 8K tokens per minute, not 10,000-word reports.

Other features:

| Feature | Budget per call |
|---|---|
| Follow-up answer | Report summary (about 800 tokens) + best source excerpts (about 2,500) + last 3 Q&A summaries (about 600). Answer up to about 1,200 tokens |
| Slide outline | Report object (about 2,500) in; outline up to about 2,000 out |
| Document workspace | Your document is cut into chunks of about 3,500 tokens. Each chunk gets summarised, then the summaries get combined. For example, a 30-page document is about 15K tokens and takes about 5 calls (about 5–6 minutes). **Cap: about 60,000 words**; above that, the app asks you to trim |
| Faithfulness check (optional) | 3 sampled claims × (claim + source excerpt) in one call, about 4K |

---

## 4. Part C: feasibility of every feature

Each feature below gets a verdict: ✅ feasible, ⚠️ feasible with limits, or ❌ not feasible. Nothing was found to be impossible.

| Feature | Verdict | Reason / limit |
|---|---|---|
| **C1** Code collects, agent thinks, one report object | ✅ | It is the design that makes 8K TPM workable (section 7) |
| **C2** Six modes as plain dicts | ✅ | Pure configuration: preferred domains, query angles, recency, extra sections, evaluation emphasis |
| **C3** Depth, length, time focus, time estimates | ⚠️ | Depth and length are fully supported. "Past year" maps to `ddgs` `timelimit="y"`, and "Recent news" uses the news search with `timelimit="m"` or `"w"`. Some fallback engines may ignore the time filter, so we also show each page's own date. Time estimates are approximate |
| **C4** Agent plans 3–8 queries | ✅ | One small call with a structured list output |
| **C5** Search, dedupe, tiers, full-text reading | ⚠️ | Dedupe by URL and near-duplicate text is simple Python. Tiers come from editable domain lists plus heuristics, and the UI says they are a heuristic. **Spotting AI-generated SEO pages is only partially possible**: we catch obvious content farms with a blocklist and simple signals such as thin text and keyword stuffing, not all of them. Paywalled or bot-blocked pages are skipped and fall back to the snippet, and the registry records "snippet only" |
| **C6** Source registry, agent cites numbers only | ✅ | Built by code; the Sources list is printed by code |
| **C7** Claim / evidence / interpretation / confidence / citations | ✅ | Fields in the report schema. Code checks that every finding has them |
| **C8** Perspectives compared, no false balance | ✅ | Schema section and a table. Avoiding false balance is a prompt rule, and the "weight of evidence" field makes the model say which side the evidence favours |
| **C9** Two-layer hallucination prevention | ✅ | Code checks every citation number and every URL against the registry. If there are no sources, no report is written. "Could Not Verify" is a required section. **Limit:** code can prove a citation *exists*, not that it *supports* the claim. That is what the optional faithfulness check in C14 is for |
| **C10** Report structure plus mode sections | ✅ | Schema has fixed sections plus a list of mode-specific extra sections |
| **C11** Follow-ups with optional new search and an "add to report" action | ✅ | Small call per question. Memory is kept as short summaries |
| **C12** Markdown | ✅ | Plain Python |
| **C12** PDF | ⚠️ | fpdf2 supports title page, table of contents, page numbers, clickable links and styled evidence boxes. Fonts are embedded by bundling the free (OFL) TTF files in `assets/fonts/`. No images or charts |
| **C12** Word | ⚠️ | Real Heading 1/2/3 styles, so the navigation pane works. **The table of contents is a Word field: Word fills it when you open the file and accept "update fields", or press F9.** python-docx cannot calculate page numbers itself |
| **C12** PowerPoint | ⚠️ | Agent writes a slide outline and code draws it, with speaker notes. Styling uses fonts and colours, but slides only look right if the viewer has those fonts installed (PowerPoint does not embed fonts from python-pptx). We fall back to safe fonts. No images or charts. python-pptx is old but working |
| **C12** User picks slide count or length | ✅ | Passed into the outline call |
| **C13** Document workspace (PDF/DOCX/TXT/MD/paste → slides/PDF/Word/summary) | ⚠️ | Works for text-based files. Long documents are slow because of the per-minute limit (see budgets). There is a size cap. **Scanned PDFs cannot be read**: we detect almost-no-text pages and show a clear message. Adding OCR is out of scope, since it needs a system package |
| **C13** "Enhance with web research" | ⚠️ | Fact-checks a few key claims (3–6 by size), not every sentence, because of token limits |
| **C14** Part 1, per-run scorecard | ✅ | Pure Python, no tokens |
| **C14** Part 2, faithfulness judge (off by default) | ⚠️ | Samples 3 claims and costs about 4K tokens. Works only for sources whose text we read in full |
| **C14** Part 3, offline benchmark | ⚠️ | Works, but about 12 topics already uses about 150–200K tokens, a full day's free budget. The script runs with delays, saves after each topic, and can resume the next day |
| **C15** Retries, timeouts, budgets, caching | ✅ | `st.cache_data(ttl=...)` on search and on the final report, keyed by topic + mode + options |
| **C15** Session history without a database | ⚠️ | Works within a browser session. **Refreshing the page or a Cloud restart clears it**, because Streamlit session state lives in memory. A simpler extra could let you download and re-upload history as a file (see decisions) |
| **C16** Friendly error messages | ✅ | One small error helper maps each known failure to a message. Tracebacks go to logs only |
| **D** Research Desk UI, light and dark themes | ⚠️ | Most styling comes from `config.toml` (fonts, colours, radius, borders, both themes). Report typography needs a small CSS file. A few Streamlit internals have no stable selectors; I will mark each risky selector in the CSS file |
| **D4** Real staged progress | ✅ | `run_research()` takes a `progress` callback that the backend calls at each real step (planning → searching → reading → writing → checking); the UI shows it in `st.status`. No fake percentages |
| **D4** Citation markers linked to sources | ⚠️ | Markers like [3] link straight to the source URL in a new tab. Linking to the sources panel inside the page is less reliable in Streamlit, so we use the direct link |

---

## 5. Biggest risks and mitigations

| # | Risk | What the design does |
|---|---|---|
| 1 | **Groq 8K tokens/minute.** One oversized call fails outright | Python trims everything to budgets *before* calling. Long jobs are split into small calls. On a 429 we wait for Groq's `retry-after` seconds, then retry |
| 2 | **Groq 200K tokens/day** | Reports are cached. A token meter in the sidebar estimates today's use. The faithfulness check is off by default. When the daily limit is hit, the app says so clearly ("Daily free limit reached. It resets within 24 hours.") |
| 3 | **Search blocking on Streamlit Cloud's shared IPs.** Many apps share the same addresses, so search engines rate-limit them | `ddgs` `backend="auto"` falls back across several engines. Retries back off and searches are spaced out. Search results are cached. Wikipedia, arXiv and OpenAlex give non-search sources for most modes. If everything fails, we show an honest failure and **no report from memory** |
| 4 | **Reasoning tokens eat the output budget** | Use `reasoning_effort="low"` (confirmed in Phase 1). Keep `max_tokens` with headroom. If the answer is cut off, the JSON repair and retry path catches it |
| 5 | **Structured output fails or is malformed** | `output_pydantic` first. Fallback: extract, repair, validate with Pydantic, retry once with the error message, then show a friendly error |
| 6 | **Pages blocked, paywalled, slow or huge** | 10-second timeouts and a 2 MB download cap. Non-HTML content is skipped. Failed pages keep their snippet and are marked "snippet only" |
| 7 | **CrewAI changes fast** (a release almost every day) | Pin `crewai[litellm]==1.15.26` exactly and upgrade on purpose |
| 8 | **Long runs on Cloud.** Closing the tab stops the run; the app sleeps after 12 hours | Deep runs stay around 4–6 minutes. Results are cached, so a re-run of the same topic is instant |
| 9 | **Heuristic tiers mistaken for truth** | The UI and exports label tiers as "heuristic credibility", and the domain lists are editable |

---

## 6. Replacements and corrections to the request

| You asked for | Status | Recommendation |
|---|---|---|
| `duckduckgo-search` / "DuckDuckGo search" | **Renamed** to `ddgs` | Use `ddgs` 9.16. Same idea (free, no key), and now with automatic fallback engines. The import is `from ddgs import DDGS` |
| Model `openai/gpt-oss-120b` | Valid. CrewAI needs the prefix | `groq/openai/gpt-oss-120b`. No change of model |
| "Groq only" | Fine | CrewAI reaches Groq through LiteLLM. This is **not** LangChain and not the OpenAI API; it is CrewAI's adapter library |
| crewai-tools | Not needed | Python does the collecting and the agent has no tools, so we skip it. It saves install size and avoids tool-loop token growth |
| LangChain | Not needed | Not used |
| "Single agent, multiple tasks allowed" | Using it | One Agent object, created once. Each job (research, follow-up, slides, document) runs that same agent through a Crew with `Process.sequential` and 1–3 small tasks. Splitting into tasks is what keeps each call under 8K tokens. This is still one agent; no agents talk to each other |
| Python version | — | 3.12 locally and on Cloud |
| OpenAlex "no key" | Changed in 2026 to a daily credit budget | Works without a key for light use. A free key is optional (`OPENALEX_API_KEY`) |
| Session history | Possible, with a limit | Lost on refresh (see decisions) |

---

## 7. Architecture in plain language

### Why "code collects, agent thinks"

Think of a newspaper:

- The **researcher** (Python code) goes to the library, photocopies pages, numbers each copy, and files them.
- The **writer** (the CrewAI agent) only reads the numbered copies and writes the article, citing copy numbers.
- The **fact-checker** (Python code again) checks every citation number against the file before printing.

This is more reliable than letting the model do everything, for five reasons:

1. **Code can't hallucinate a URL.** The model never types a URL or a source title. It only writes numbers like `[3]`, and code turns those into links from the registry it built itself.
2. **Code is predictable and testable.** Searching, deduplicating and trimming behave the same every time and can be tested in isolation. A model doing those steps would vary and burn tokens.
3. **Budgets are enforced by code.** Under 8K tokens a minute, someone has to count. Code counts exactly; a model with tools would re-send its whole history on every step.
4. **One structured object feeds everything.** The model returns data (claims, evidence, confidence, citation numbers), not formatted prose. The web view, PDF, Word, slides, scorecard and follow-ups all render from that same object, so they can never disagree.
5. **Failures are honest.** If code found no sources, the model is never asked to write, so there is no "report from memory".

### Flow diagram

```
 USER (Streamlit UI: app.py + ui_components.py)
   │  topic, mode, depth, length, time focus
   ▼
 run_research()  ─────────────── research_agent.py
   │
   │ 1. PLAN          [AGENT, call 1, ~1.5K tokens]
   │    mode config (modes.py) ──► agent ──► 3–8 search queries (structured)
   │
   │ 2. SEARCH        [CODE] search_tools.py
   │    ddgs text/news (+ Wikipedia / arXiv / OpenAlex by mode)
   │    retries + backoff + timeouts + cache
   │
   │ 3. FILTER        [CODE] dedupe URLs & near-duplicates
   │    credibility tiers (domain lists + heuristics), drop excluded
   │
   │ 4. READ          [CODE] trafilatura on top-ranked pages
   │    timeouts, size cap, skip failures → snippet fallback
   │
   │ 5. REGISTER      [CODE] Source Registry
   │    #1 title | url | domain | tier | date | full-text or snippet
   │
   │ 6. BUDGET        [CODE] pick best excerpts per source to fit ~7K/call
   │
   │ 7. WRITE         [AGENT, calls 2..4]
   │    excerpts labelled [1]..[n] ──► agent ──► ReportObject (Pydantic)
   │    (JSON repair + validation + 1 retry if needed)
   │
   │ 8. CHECK         [CODE] utils.py
   │    citation numbers exist? stray URLs? flag/remove → warnings
   │    Sources list built from registry (never from model text)
   │
   │ 9. SCORE         [CODE] evaluation.py → quality scorecard
   ▼
 ReportObject + Registry + Scorecard + Metadata   (cached, saved in session history)
   │
   ├──► UI report view (reading column, sources panel, scorecard)
   ├──► Follow-up chat ── run_followup() [AGENT, small call; optional mini-search → registry]
   ├──► Exports ── exporters.py: Markdown │ PDF │ Word │ PowerPoint
   │                 (slides: agent writes outline, code renders)
   └──► Optional faithfulness check [AGENT as judge, off by default]

 MY DOCUMENT WORKSPACE (separate page)
   upload/paste ─► documents.py (read, detect scanned PDF, chunk)
                ─► run_document_job() [AGENT: summarize chunks → combine → shape output]
                ─► optional fact-check: claims ─► SEARCH/READ/REGISTER ─► cited
                ─► same exporters ─► download
```

### The report object, in brief

The full schema with every field explained is Phase 1, step 5. In outline:

```
Report
├─ title, topic, mode, generated_at, options      (metadata, set by code)
├─ executive_summary
├─ introduction_and_scope
├─ key_findings[]  → claim, evidence, interpretation,
│                    confidence (High/Medium/Low), confidence_reason, citations[int]
├─ detailed_analysis[]  → heading, body, citations[int]
├─ perspectives[]       → position, who_holds_it, summary, evidence, citations[int]
├─ agreement_and_disagreement, weight_of_evidence
├─ could_not_verify[]   → item, why
├─ limitations[]
├─ conclusion
├─ extra_sections[]     → mode-specific (Version Notes, Timeline, Research Gaps…)
└─ (Sources, warnings and scorecard are attached by CODE, not written by the agent)
```

### File structure

I would keep your structure as written. It is already small and clear. Two small additions:

- `assets/fonts/`: free OFL font files (Source Serif 4, IBM Plex Sans, IBM Plex Mono) for PDF export.
- `tests/`: the small standalone test snippets (`test_llm.py`, `test_search.py`, and so on). This keeps them out of the app code.

Everything else matches Part E.

---

## 8. Final phase plan

Each phase ends with a working app and a short checklist. Each "next" moves one step forward.

**Phase 1: Setup and core research**

1. Project folder, Python 3.12 virtual environment, `requirements.txt` (pinned), empty files, `.env` / secrets, `.gitignore`
2. Test Groq alone: model string, a small call, `reasoning_effort`, measure tokens, test structured output
3. Search and page reading alone (`ddgs`, trafilatura), with timeouts and retries
4. Source registry and citation verification (pure code, testable without the model)
5. Report schema (Pydantic), with every field explained
6. The single agent and its tasks; JSON fallback and validation
7. `run_research()` with budgets, call splitting, rate-limit waits, caching, metadata and a progress callback
8. Minimal temporary UI to test end to end

**Phase 2:** Credibility tiers, evidence vs interpretation, confidence, Could Not Verify, optional background note

**Phase 3:** Six research modes, plus Wikipedia, arXiv, OpenAlex (then Crossref and PubMed); perspective comparison

**Phase 4:** Follow-up questions, mini-search, add-to-report addendum

**Phase 5:** Exports: Markdown → Word → PDF → PowerPoint, plus length and slide-count controls

**Phase 6:** My Document Workspace: reading, scanned-PDF detection, chunking, outputs, optional fact-check

**Phase 7:** Evaluation: scorecard, optional faithfulness judge, `eval_set.json` and the resumable benchmark script

**Phase 8:** Full Research Desk UI: design system, contrast checks, copy deck, wireframes, `config.toml` light and dark themes, `style.css`, every screen and state, responsive and accessibility pass, honest design critique

**Phase 9:** Local test pass, GitHub (I'll ask before creating any repo or pushing), Streamlit Cloud deploy and secrets, deployed testing, README and troubleshooting guide

---

## 9. Decisions I need from you

None of these block Phase 1. I'm using the default shown unless you say otherwise.

1. **Session history after a refresh.** Default: memory only, as specified, with a clear note in the UI. Optional extra in Phase 4: a "Download history" / "Load history" button that saves to a JSON file. This adds no database.
2. **Your Groq limits.** When you have a key, check [console.groq.com/settings/limits](https://console.groq.com/settings/limits). If your TPM is higher than 8K, I can raise the budgets.
3. **Optional free keys.** Default: none. OpenAlex and PubMed keys are free and raise limits, and they can be added later through the same secrets helper.

---

*Sources checked:* [Groq gpt-oss-120b](https://console.groq.com/docs/model/openai/gpt-oss-120b) · [Groq rate limits](https://console.groq.com/docs/rate-limits) · [Groq structured outputs](https://console.groq.com/docs/structured-outputs) · [Groq deprecations](https://console.groq.com/docs/deprecations) · [CrewAI LLMs](https://docs.crewai.com/en/concepts/llms) · [CrewAI Tasks](https://docs.crewai.com/en/concepts/tasks) · [CrewAI install](https://docs.crewai.com/en/installation) · [ddgs on PyPI](https://pypi.org/project/ddgs/) · [ddgs GitHub](https://github.com/deedy5/ddgs) · [duckduckgo-search on PyPI](https://pypi.org/project/duckduckgo-search/) · [arXiv API terms](https://info.arxiv.org/help/api/tou.html) · [OpenAlex authentication](https://help.openalex.org/api/authentication/) · [Crossref access](https://www.crossref.org/documentation/retrieve-metadata/rest-api/access-and-authentication/) · [NCBI E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25497/) · [Streamlit Cloud deploy](https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy) · [Streamlit Cloud manage app](https://docs.streamlit.io/deploy/streamlit-community-cloud/manage-your-app) · PyPI JSON for every package version listed.
