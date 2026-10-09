"""Everything about collecting sources. Pure Python, no AI here.

Pipeline (called by research_agent.run_research):
  web_search / news search / research APIs
    -> normalise URLs and drop duplicates
    -> classify credibility tier (editable domain lists + simple heuristics)
    -> rank
    -> read the full text of the best pages (timeouts, size caps, graceful skips)
    -> drop near-duplicate pages
    -> number everything in a source registry

Each source in the registry is a plain dict:
  id, title, url, domain, tier (1/2/3), tier_label, tier_reason, date,
  source_type (web/news/wikipedia/arxiv/openalex/crossref/pubmed),
  read_status ("full text" / "abstract" / "snippet"), snippet, text (excerpt
  used by the agent), preprint (bool), query (which query found it)
"""

from __future__ import annotations

import io
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from typing import Callable
from urllib.parse import parse_qsl, quote, urlencode, urlparse, urlunparse

import httpx

from utils import AppError, cache_get, cache_set, estimate_tokens, get_logger, get_secret, trim_to_tokens

log = get_logger(__name__)

USER_AGENT = "ResearchDesk/1.0 (educational research app; https://github.com/)"
SEARCH_CACHE_SECONDS = 60 * 60          # identical searches within an hour reuse results
PAGE_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
API_TIMEOUT = httpx.Timeout(12.0, connect=5.0)
MAX_PAGE_BYTES = 2_000_000              # skip pages larger than ~2 MB
MAX_PDF_BYTES = 5_000_000

Progress = Callable[[str], None] | None


# ---------------------------------------------------------------------------
# Credibility tiers. These lists are EDITABLE: add domains you trust or distrust.
# Tiers are a heuristic about the *kind* of source, not a guarantee of truth.
# ---------------------------------------------------------------------------
TIER1_SUFFIXES = (".gov", ".mil", ".int", ".gov.uk", ".gov.au", ".gc.ca", ".europa.eu", ".gouv.fr",
                  ".bund.de", ".govt.nz", ".gov.in", ".go.jp")
TIER1_DOMAINS = {
    # international bodies, statistics, standards
    "un.org", "who.int", "oecd.org", "worldbank.org", "imf.org", "wto.org", "ipcc.ch", "iso.org", "w3.org",
    "ietf.org", "rfc-editor.org", "unicode.org", "ecma-international.org", "whatwg.org",
    # peer-reviewed publishers and indexes
    "nature.com", "science.org", "cell.com", "thelancet.com", "nejm.org", "bmj.com", "jamanetwork.com",
    "plos.org", "pnas.org", "springer.com", "link.springer.com", "wiley.com", "onlinelibrary.wiley.com",
    "sciencedirect.com", "tandfonline.com", "sagepub.com", "journals.sagepub.com", "academic.oup.com",
    "cambridge.org", "jstor.org", "ieee.org", "ieeexplore.ieee.org", "acm.org", "dl.acm.org",
    "frontiersin.org", "mdpi.com", "cochranelibrary.com", "ncbi.nlm.nih.gov", "pubmed.ncbi.nlm.nih.gov",
    "doi.org",
    # official documentation
    "python.org", "docs.python.org", "developer.mozilla.org", "learn.microsoft.com", "developer.apple.com",
    "developer.android.com", "docs.github.com", "kubernetes.io", "rust-lang.org", "go.dev", "nodejs.org",
    "postgresql.org", "docs.streamlit.io", "docs.crewai.com", "console.groq.com", "pypi.org",
}
TIER1_PREFIXES = ("docs.", "developer.", "developers.", "learn.", "investor.", "investors.", "ir.")

TIER2_SUFFIXES = (".edu", ".ac.uk", ".ac.jp", ".edu.au", ".ac.nz", ".ac.in", ".ac.za")
TIER2_DOMAINS = {
    # established news and wire services
    "reuters.com", "apnews.com", "bbc.co.uk", "bbc.com", "npr.org", "theguardian.com", "nytimes.com",
    "washingtonpost.com", "wsj.com", "ft.com", "economist.com", "bloomberg.com", "aljazeera.com", "dw.com",
    "france24.com", "cnbc.com", "politico.com", "politico.eu", "axios.com", "theatlantic.com", "pbs.org",
    "lemonde.fr", "spiegel.de", "nikkei.com", "scmp.com", "abc.net.au", "cbc.ca", "time.com",
    # reference works and science journalism
    "wikipedia.org", "britannica.com", "plato.stanford.edu", "iep.utm.edu", "scientificamerican.com",
    "newscientist.com", "nationalgeographic.com", "smithsonianmag.com", "quantamagazine.org",
    "khanacademy.org", "mayoclinic.org", "clevelandclinic.org", "hopkinsmedicine.org",
    # established tech and industry publications, repositories, preprints
    "github.com", "arxiv.org", "biorxiv.org", "medrxiv.org", "ssrn.com", "openalex.org", "arstechnica.com",
    "theverge.com", "wired.com", "techcrunch.com", "lwn.net", "spectrum.ieee.org", "technologyreview.com",
    "mckinsey.com", "pewresearch.org", "statista.com", "brookings.edu", "rand.org", "chathamhouse.org",
}

# Excluded: sites we never use as research sources (social feeds, pin boards,
# known low-quality content farms). Edit freely.
EXCLUDED_DOMAINS = {
    "pinterest.com", "facebook.com", "instagram.com", "tiktok.com", "x.com", "twitter.com", "linkedin.com",
    "ehow.com", "answers.com", "coursehero.com", "chegg.com", "studocu.com", "scribd.com", "slideshare.net",
    "brainly.com", "brainly.in", "bartleby.com",
}
TIER3_HINT_DOMAINS = {"medium.com", "substack.com", "reddit.com", "quora.com", "stackexchange.com",
                      "stackoverflow.com", "blogspot.com", "wordpress.com", "youtube.com"}

TIER_LABELS = {1: "Tier 1 · primary/official", 2: "Tier 2 · established secondary", 3: "Tier 3 · other"}


def domain_of(url: str) -> str:
    """'https://www.bbc.co.uk/news/x' -> 'bbc.co.uk'."""
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def _matches(domain: str, names: set[str]) -> bool:
    """True if domain equals a listed name or is a subdomain of it."""
    return any(domain == name or domain.endswith("." + name) for name in names)


def classify_tier(url: str, title: str = "", text: str = "", source_type: str = "web") -> tuple[int | None, str]:
    """Return (tier, reason). tier None means 'excluded'.

    Order: excluded lists -> research APIs -> Tier 1 -> Tier 2 -> Tier 3 hints -> heuristics.
    """
    domain = domain_of(url)
    if _matches(domain, EXCLUDED_DOMAINS):
        return None, "excluded domain (social media, pin board or content farm)"
    if source_type in ("pubmed",):
        return 1, "indexed in PubMed (biomedical literature)"
    if source_type in ("openalex", "crossref"):
        # The API caller sets preprint/journal info in the title prefix; see below.
        return (2, "scholarly record (preprint or repository)") if "preprint" in title.lower() else (
            1, "scholarly record (journal or publisher)")
    if source_type == "arxiv" or _matches(domain, {"arxiv.org", "biorxiv.org", "medrxiv.org"}):
        return 2, "preprint server: not peer-reviewed"
    if domain.endswith(TIER1_SUFFIXES) or _matches(domain, TIER1_DOMAINS):
        return 1, "government, international body, standards body, journal or official docs"
    if domain.startswith(TIER1_PREFIXES):
        return 1, "official documentation or investor-relations site (by subdomain)"
    if domain.endswith(TIER2_SUFFIXES) or _matches(domain, TIER2_DOMAINS):
        return 2, "university, established news, reference work or recognised publication"
    if _matches(domain, TIER3_HINT_DOMAINS):
        return 3, "blog, forum or user-generated platform"
    # Very simple spam heuristics on what we read.
    if text:
        words = re.findall(r"[a-z]{3,}", text.lower())
        if len(words) > 200:
            top = max((words.count(w) for w in set(words) if len(w) > 4), default=0)
            if top / len(words) > 0.06:
                return None, "excluded: looks keyword-stuffed (one word is over 6% of the text)"
    spam_title = re.search(r"\b(top \d+|best .* \d{4}|you won't believe|click here)\b", title.lower())
    if spam_title:
        return 3, "unknown site with listicle/clickbait-style title"
    return 3, "unknown site (not on any list)"


# ---------------------------------------------------------------------------
# URLs and duplicates
# ---------------------------------------------------------------------------
def normalize_url(url: str) -> str:
    """Make URLs comparable: drop tracking params, fragments, 'www.' and trailing '/'."""
    try:
        parts = urlparse(url.strip())
        query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(("utm_", "fbclid", "gclid", "ref"))]
        host = parts.netloc.lower()
        host = host[4:] if host.startswith("www.") else host
        path = parts.path.rstrip("/") or "/"
        return urlunparse((parts.scheme.lower() or "https", host, path, "", urlencode(query), ""))
    except ValueError:
        return url


def _shingles(text: str, size: int = 5) -> set[str]:
    words = re.findall(r"\w+", text.lower())
    return {" ".join(words[i : i + size]) for i in range(max(0, len(words) - size + 1))}


def is_near_duplicate(text_a: str, text_b: str, threshold: float = 0.6) -> bool:
    """True if two texts share most of their 5-word phrases (Jaccard similarity)."""
    a, b = _shingles(text_a[:6000]), _shingles(text_b[:6000])
    if not a or not b:
        return False
    return len(a & b) / len(a | b) >= threshold


# ---------------------------------------------------------------------------
# Web search (ddgs)
# ---------------------------------------------------------------------------
def web_search(query: str, max_results: int = 6, timelimit: str | None = None, news: bool = False) -> list[dict]:
    """Search the web with ddgs. Returns [{title, url, snippet, date, source_type}].

    Retries with backoff when the search engine rate-limits or times out, and
    caches results for an hour. Raises AppError('search_failed') if all tries fail.
    """
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException, RatelimitException, TimeoutException

    key = f"search|{query}|{max_results}|{timelimit}|{news}"
    cached = cache_get(key, SEARCH_CACHE_SECONDS)
    if cached is not None:
        return cached

    last_error: Exception | None = None
    for attempt, pause in enumerate([0, 3, 8], start=1):
        if pause:
            time.sleep(pause)
        try:
            client = DDGS(timeout=10)
            if news:
                raw = client.news(query, max_results=max_results, timelimit=timelimit or "m")
            else:
                raw = client.text(query, max_results=max_results, timelimit=timelimit)
            results = []
            for item in raw or []:
                url = item.get("href") or item.get("url") or ""
                if not url.startswith("http"):
                    continue
                results.append({
                    "title": (item.get("title") or "").strip(),
                    "url": url,
                    "snippet": (item.get("body") or "").strip(),
                    "date": (item.get("date") or "")[:10],
                    "source_type": "news" if news else "web",
                })
            cache_set(key, results)
            return results
        except (RatelimitException, TimeoutException, DDGSException) as error:
            last_error = error
            log.warning("Search attempt %d failed for %r: %s", attempt, query, error)
        except Exception as error:  # network problems inside the library
            last_error = error
            log.warning("Search attempt %d crashed for %r: %s", attempt, query, error)
    raise AppError("search_failed", str(last_error))


# ---------------------------------------------------------------------------
# Free research APIs. Each returns the same result shape as web_search, plus
# "abstract" when the API gives one. Every function fails soft: on any error it
# logs and returns [] so research continues with web results.
# ---------------------------------------------------------------------------
def _get(url: str, params: dict | None = None, headers: dict | None = None) -> httpx.Response:
    with httpx.Client(timeout=API_TIMEOUT, headers={"User-Agent": USER_AGENT, **(headers or {})},
                      follow_redirects=True) as client:
        response = client.get(url, params=params)
        response.raise_for_status()
        return response


def wikipedia_search(query: str, limit: int = 2) -> list[dict]:
    """Search English Wikipedia. Pages are read later like any web page."""
    try:
        data = _get("https://en.wikipedia.org/w/api.php", {
            "action": "query", "list": "search", "srsearch": query, "srlimit": limit, "format": "json"}).json()
        results = []
        for hit in data.get("query", {}).get("search", []):
            title = hit["title"]
            snippet = re.sub(r"<[^>]+>", "", hit.get("snippet", ""))
            results.append({"title": f"{title} (Wikipedia)",
                            "url": "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_")),
                            "snippet": snippet, "date": (hit.get("timestamp") or "")[:10],
                            "source_type": "wikipedia"})
        return results
    except Exception as error:
        log.warning("Wikipedia search failed: %s", error)
        return []


_ARXIV_LOCK = threading.Lock()
_ARXIV_LAST = [0.0]


def arxiv_search(query: str, limit: int = 3) -> list[dict]:
    """Search arXiv. Respects arXiv's rule of one request every 3 seconds."""
    try:
        with _ARXIV_LOCK:
            wait = 3.1 - (time.time() - _ARXIV_LAST[0])
            if wait > 0:
                time.sleep(wait)
            _ARXIV_LAST[0] = time.time()
            text = _get("https://export.arxiv.org/api/query", {
                "search_query": f"all:{query}", "max_results": limit, "sortBy": "relevance"}).text
        ns = {"a": "http://www.w3.org/2005/Atom"}
        results = []
        for entry in ET.fromstring(text).findall("a:entry", ns):
            title = " ".join((entry.findtext("a:title", "", ns)).split())
            abstract = " ".join((entry.findtext("a:summary", "", ns)).split())
            url = entry.findtext("a:id", "", ns).replace("http://", "https://")
            results.append({"title": f"{title} (arXiv preprint)", "url": url, "snippet": abstract[:300],
                            "abstract": abstract, "date": entry.findtext("a:published", "", ns)[:10],
                            "source_type": "arxiv"})
        return results
    except Exception as error:
        log.warning("arXiv search failed: %s", error)
        return []


def _openalex_abstract(inverted: dict | None) -> str:
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the text."""
    if not inverted:
        return ""
    positions = [(pos, word) for word, places in inverted.items() for pos in places]
    return " ".join(word for _, word in sorted(positions))


def openalex_search(query: str, limit: int = 4) -> list[dict]:
    """Search OpenAlex scholarly works. Works without a key; a free key raises limits."""
    params = {"search": query, "per_page": limit,
              "select": "display_name,doi,publication_date,primary_location,abstract_inverted_index,type,id"}
    if get_secret("CONTACT_EMAIL"):
        params["mailto"] = get_secret("CONTACT_EMAIL")
    if get_secret("OPENALEX_API_KEY"):
        params["api_key"] = get_secret("OPENALEX_API_KEY")
    try:
        data = _get("https://api.openalex.org/works", params).json()
        results = []
        for work in data.get("results", []):
            location = work.get("primary_location") or {}
            venue = (location.get("source") or {})
            is_preprint = venue.get("type") == "repository" or work.get("type") == "preprint"
            url = work.get("doi") or location.get("landing_page_url") or work.get("id")
            abstract = _openalex_abstract(work.get("abstract_inverted_index"))
            label = "preprint" if is_preprint else (venue.get("display_name") or "scholarly work")
            results.append({"title": f"{work.get('display_name', '').strip()} ({label})", "url": url,
                            "snippet": abstract[:300], "abstract": abstract,
                            "date": work.get("publication_date") or "", "source_type": "openalex"})
        return [r for r in results if r["url"]]
    except Exception as error:
        log.warning("OpenAlex search failed: %s", error)
        return []


def crossref_search(query: str, limit: int = 3) -> list[dict]:
    """Search Crossref (DOI metadata). Adding CONTACT_EMAIL joins the faster 'polite' pool."""
    params = {"query": query, "rows": limit,
              "select": "DOI,title,URL,issued,container-title,abstract,type"}
    if get_secret("CONTACT_EMAIL"):
        params["mailto"] = get_secret("CONTACT_EMAIL")
    try:
        data = _get("https://api.crossref.org/works", params).json()
        results = []
        for item in data.get("message", {}).get("items", []):
            title = (item.get("title") or [""])[0]
            venue = (item.get("container-title") or [""])[0]
            parts = (item.get("issued", {}).get("date-parts") or [[None]])[0]
            date = "-".join(str(p).zfill(2) for p in parts if p) if parts and parts[0] else ""
            abstract = re.sub(r"<[^>]+>", " ", item.get("abstract") or "")
            label = "preprint" if item.get("type") == "posted-content" else (venue or item.get("type", "record"))
            results.append({"title": f"{title} ({label})", "url": item.get("URL") or f"https://doi.org/{item.get('DOI')}",
                            "snippet": " ".join(abstract.split())[:300], "abstract": " ".join(abstract.split()),
                            "date": date, "source_type": "crossref"})
        return [r for r in results if r["title"].strip(" ()")]
    except Exception as error:
        log.warning("Crossref search failed: %s", error)
        return []


def pubmed_search(query: str, limit: int = 3) -> list[dict]:
    """Search PubMed (biomedical). Two calls: find IDs, then fetch titles and abstracts."""
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    common = {"tool": "ResearchDesk", "db": "pubmed"}
    if get_secret("CONTACT_EMAIL"):
        common["email"] = get_secret("CONTACT_EMAIL")
    try:
        ids = _get(base + "esearch.fcgi", {**common, "term": query, "retmax": limit, "retmode": "json"}
                   ).json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []
        time.sleep(0.4)  # stay under 3 requests per second
        root = ET.fromstring(_get(base + "efetch.fcgi", {**common, "id": ",".join(ids), "retmode": "xml"}).text)
        results = []
        for article in root.findall(".//PubmedArticle"):
            pmid = article.findtext(".//PMID", "")
            title = "".join(article.find(".//ArticleTitle").itertext()) if article.find(".//ArticleTitle") is not None else ""
            abstract = " ".join("".join(p.itertext()) for p in article.findall(".//AbstractText"))
            year = article.findtext(".//PubDate/Year", "") or article.findtext(".//PubDate/MedlineDate", "")[:4]
            journal = article.findtext(".//Journal/Title", "")
            results.append({"title": f"{title} ({journal})", "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
                            "snippet": abstract[:300], "abstract": abstract, "date": year, "source_type": "pubmed"})
        return results
    except Exception as error:
        log.warning("PubMed search failed: %s", error)
        return []


API_FUNCTIONS = {"wikipedia": wikipedia_search, "arxiv": arxiv_search, "openalex": openalex_search,
                 "crossref": crossref_search, "pubmed": pubmed_search}


# ---------------------------------------------------------------------------
# Reading pages
# ---------------------------------------------------------------------------
def fetch_page(url: str) -> dict:
    """Download a page and extract its main text, title and date.

    Returns {ok, text, title, date, error}. Never raises: blocked, paywalled,
    too large, non-HTML or failed pages come back with ok=False and a reason.
    """
    import trafilatura

    cached = cache_get(f"page|{url}", SEARCH_CACHE_SECONDS)
    if cached is not None:
        return cached
    result = {"ok": False, "text": "", "title": "", "date": "", "error": ""}
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; ResearchDesk/1.0; educational)",
                   "Accept": "text/html,application/xhtml+xml,application/pdf;q=0.9,*/*;q=0.5"}
        with httpx.Client(timeout=PAGE_TIMEOUT, follow_redirects=True, headers=headers) as client:
            with client.stream("GET", url) as response:
                if response.status_code in (401, 402, 403, 429, 451):
                    result["error"] = f"blocked or paywalled (HTTP {response.status_code})"
                    return result
                response.raise_for_status()
                kind = response.headers.get("content-type", "").lower()
                limit = MAX_PDF_BYTES if "pdf" in kind else MAX_PAGE_BYTES
                body = b""
                for chunk in response.iter_bytes():
                    body += chunk
                    if len(body) > limit:
                        result["error"] = "page too large"
                        return result
        if "pdf" in kind:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(body))
            text = "\n".join((page.extract_text() or "") for page in reader.pages[:12])
            result.update(text=text, title=(reader.metadata.title if reader.metadata else "") or "")
        elif "html" in kind or "xml" in kind or not kind:
            doc = trafilatura.bare_extraction(body.decode(response.encoding or "utf-8", errors="replace"),
                                              url=url, with_metadata=True, include_comments=False,
                                              include_tables=True, favor_precision=True)
            if doc is not None:
                result.update(text=doc.text or "", title=doc.title or "", date=(doc.date or "")[:10])
        else:
            result["error"] = f"unsupported content type ({kind.split(';')[0]})"
            return result
        text = result["text"].strip()
        if len(text) < 400:
            result["error"] = "too little readable text (paywall, script-only page or login wall)"
            return result
        result["text"], result["ok"] = text, True
    except httpx.TimeoutException:
        result["error"] = "timed out"
    except Exception as error:
        result["error"] = f"could not read ({type(error).__name__})"
        log.info("Page read failed for %s: %s", url, error)
    if result["ok"]:
        cache_set(f"page|{url}", result)
    return result


def best_excerpt(text: str, terms: list[str], max_tokens: int) -> str:
    """Pick the paragraphs most related to the topic, within a token budget.

    Always keeps the opening paragraph (it usually says what the page is about),
    then adds the paragraphs with the most topic words, in their original order.
    """
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if len(p.strip()) > 40]
    if not paragraphs:
        return trim_to_tokens(text, max_tokens)
    words = {w for t in terms for w in re.findall(r"[a-z0-9]{4,}", t.lower())}
    scored = sorted(range(len(paragraphs)),
                    key=lambda i: -sum(paragraphs[i].lower().count(w) for w in words))
    chosen, used = {0}, estimate_tokens(paragraphs[0])
    for index in scored:
        cost = estimate_tokens(paragraphs[index])
        if index not in chosen and used + cost <= max_tokens:
            chosen.add(index)
            used += cost
    return trim_to_tokens("\n".join(paragraphs[i] for i in sorted(chosen)), max_tokens)


# ---------------------------------------------------------------------------
# Ranking and the full collection pipeline
# ---------------------------------------------------------------------------
def _score(source: dict, mode: dict, rank_in_results: int, hits: int) -> float:
    """Higher is better: tier, preferred domains, search rank, query overlap, recency."""
    score = {1: 3.0, 2: 2.0, 3: 0.8}[source["tier"]]
    domain = source["domain"]
    if any(domain.endswith(p) if p.startswith(".") else (p in domain) for p in mode["preferred_domains"]):
        score += 1.5
    score += max(0.0, 1.0 - rank_in_results * 0.12)      # earlier search results first
    score += 0.4 * (hits - 1)                               # found by several queries
    if mode["recency"] != "any" and source.get("date", "")[:4].isdigit():
        age_years = max(0, time.gmtime().tm_year - int(source["date"][:4]))
        score += max(0.0, 1.0 - 0.4 * age_years)
    return score


def collect_sources(topic: str, queries: list[str], mode: dict, depth: dict, time_focus: dict,
                    progress: Progress = None) -> tuple[list[dict], dict]:
    """Run searches, rank, read pages and return (registry, stats).

    The registry contains every usable source that searches returned, numbered
    1..n in ranking order. Sources whose text the agent will see have a "text".
    """
    say = progress or (lambda message: None)
    stats = {"queries_run": 0, "search_errors": 0, "results_returned": 0, "excluded": [],
             "duplicates_removed": 0, "pages_attempted": 0, "pages_read": 0, "page_failures": []}
    found: dict[str, dict] = {}            # normalised URL -> source
    order: list[str] = []
    hits: dict[str, int] = {}
    positions: dict[str, int] = {}

    def add(result: dict, position: int, query: str) -> None:
        key = normalize_url(result["url"])
        hits[key] = hits.get(key, 0) + 1
        if key in found:
            stats["duplicates_removed"] += 1
            return
        tier, reason = classify_tier(result["url"], result["title"], "", result["source_type"])
        if tier is None:
            stats["excluded"].append({"url": result["url"], "reason": reason})
            return
        found[key] = {"title": result["title"] or domain_of(result["url"]), "url": result["url"],
                      "domain": domain_of(result["url"]), "tier": tier, "tier_reason": reason,
                      "date": result.get("date", ""), "source_type": result["source_type"],
                      "snippet": result.get("snippet", ""), "abstract": result.get("abstract", ""),
                      "preprint": "preprint" in result["title"].lower() or result["source_type"] == "arxiv",
                      "query": query, "text": "", "read_status": "snippet"}
        positions[key] = position
        order.append(key)

    # 1. Web and news searches, one query at a time with a short pause between
    #    them, which makes rate limiting on shared servers less likely.
    use_news = time_focus["news"] or mode["recency"] == "recent_only"
    for number, query in enumerate(queries, start=1):
        say(f"Searching ({number}/{len(queries)}): {query}")
        stats["queries_run"] += 1
        try:
            if use_news:
                results = web_search(query, depth["results_per_query"], time_focus["timelimit"] or "m", news=True)
                if mode["recency"] != "recent_only" or len(results) < 3:
                    results += web_search(query, max(3, depth["results_per_query"] // 2), "y" if mode["recency"] == "recent_only" else time_focus["timelimit"])
            else:
                results = web_search(query, depth["results_per_query"], time_focus["timelimit"])
            stats["results_returned"] += len(results)
            for position, result in enumerate(results):
                add(result, position, query)
        except AppError as error:
            stats["search_errors"] += 1
            log.warning("Query failed: %s (%s)", query, error.detail)
        time.sleep(0.8)

    # 2. Mode-specific research APIs (scholarly databases, Wikipedia).
    for api in mode.get("apis", []):
        say(f"Checking {api.title()} …")
        api_results = API_FUNCTIONS[api](topic)
        stats["results_returned"] += len(api_results)
        for position, result in enumerate(api_results):
            add(result, position, f"{api}: {topic}")

    if not found:
        if stats["search_errors"] == stats["queries_run"] and stats["queries_run"] > 0:
            raise AppError("search_failed", "every search query failed")
        raise AppError("no_sources", "searches returned no usable results")

    # 3. Rank everything we found.
    sources = [found[k] for k in order]
    for key, source in zip(order, sources):
        source["_score"] = _score(source, mode, positions[key], hits[key])
    sources.sort(key=lambda s: -s["_score"])

    # 4. Read the full text of the best web pages, in parallel. API results with
    #    abstracts use the abstract (reading publisher pages is often blocked).
    terms = [topic] + queries
    for source in sources:
        if source["abstract"]:
            source["text"] = best_excerpt(source["abstract"], terms, depth["excerpt_tokens"])
            source["read_status"] = "abstract"
    to_read = [s for s in sources if not s["abstract"]][: depth["pages_to_read"]]
    say(f"Reading {len(to_read)} pages …")
    stats["pages_attempted"] = len(to_read)
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = list(pool.map(lambda s: fetch_page(s["url"]), to_read))
    read_texts: list[str] = []
    for source, page in zip(to_read, pages):
        if not page["ok"]:
            stats["page_failures"].append({"url": source["url"], "reason": page["error"]})
            continue
        if any(is_near_duplicate(page["text"], other) for other in read_texts):
            source["_duplicate"] = True
            stats["duplicates_removed"] += 1
            continue
        tier, reason = classify_tier(source["url"], page["title"] or source["title"], page["text"], source["source_type"])
        if tier is None:
            source["_duplicate"] = True        # drop it, and record why
            stats["excluded"].append({"url": source["url"], "reason": reason})
            continue
        read_texts.append(page["text"])
        source.update(text=best_excerpt(page["text"], terms, depth["excerpt_tokens"]), read_status="full text",
                      tier=tier, tier_reason=reason, date=source["date"] or page["date"])
        if page["title"] and len(page["title"]) > 5:
            source["title"] = page["title"]
        stats["pages_read"] += 1
    say(f"Read {stats['pages_read']} of {len(to_read)} pages; {len(stats['page_failures'])} could not be read.")

    # 5. Number the registry. Snippet-only sources keep their snippet as text.
    registry = []
    for source in sources:
        if source.pop("_duplicate", False):
            continue
        source.pop("_score", None)
        source.pop("abstract", None)
        if not source["text"]:
            source["text"] = source["snippet"]
        source["id"] = len(registry) + 1
        source["tier_label"] = TIER_LABELS[source["tier"]] + (" · preprint, not peer-reviewed" if source["preprint"] else "")
        registry.append(source)
    if not registry:
        raise AppError("no_sources", "all results were excluded or duplicates")
    return registry, stats


def search_more(question: str, registry: list[dict], max_pages: int = 2) -> list[dict]:
    """Small extra search for follow-up questions. Adds new sources to the registry.

    Returns only the newly added sources (already numbered after the existing ones).
    """
    known = {normalize_url(s["url"]) for s in registry}
    results = [r for r in web_search(question, 5) if normalize_url(r["url"]) not in known]
    added = []
    for result in results:
        tier, reason = classify_tier(result["url"], result["title"])
        if tier is None:
            continue
        page = fetch_page(result["url"]) if len(added) < max_pages else {"ok": False}
        source = {"id": len(registry) + 1, "title": page.get("title") or result["title"], "url": result["url"],
                  "domain": domain_of(result["url"]), "tier": tier, "tier_reason": reason,
                  "tier_label": TIER_LABELS[tier], "date": page.get("date") or result.get("date", ""),
                  "source_type": "web", "snippet": result["snippet"], "preprint": False,
                  "query": f"follow-up: {question}",
                  "text": best_excerpt(page["text"], [question], 500) if page.get("ok") else result["snippet"],
                  "read_status": "full text" if page.get("ok") else "snippet", "added_by_followup": True}
        registry.append(source)
        added.append(source)
        if len(added) >= 4:
            break
    return added
