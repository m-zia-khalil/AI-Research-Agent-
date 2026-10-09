"""Live test 2: web search, research APIs and page reading, alone. No Groq tokens.

    python tests/test_search.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from search_tools import (API_FUNCTIONS, best_excerpt, classify_tier, fetch_page,  # noqa: E402
                          web_search)
from utils import AppError  # noqa: E402

query = "how do heat pumps work in cold climates"
print("1. Web search (ddgs) …")
try:
    results = web_search(query, 5)
    for r in results:
        tier, reason = classify_tier(r["url"], r["title"])
        print(f"   Tier {tier}: {r['title'][:60]}  {r['url'][:70]}")
    print(f"[ok]  {len(results)} results")
except AppError as error:
    print(f"[FAIL] {error.title} {error.action}")
    results = []

print("\n2. News search (last month) …")
try:
    news = web_search("heat pump sales", 3, "m", news=True)
    print(f"[ok]  {len(news)} news results; first date: {news[0]['date'] if news else 'n/a'}")
except AppError as error:
    print(f"[warn] {error.title}")

print("\n3. Research APIs (each fails soft and returns [] on error) …")
for name, function in API_FUNCTIONS.items():
    found = function("heat pump efficiency")
    print(f"   {name:9s} {len(found)} result(s)" + (f": {found[0]['title'][:60]}" if found else ""))

print("\n4. Page reading …")
for r in results[:3]:
    page = fetch_page(r["url"])
    if page["ok"]:
        print(f"[ok]  {len(page['text']):,} chars, date {page['date'] or 'unknown'}: {r['url'][:60]}")
        print("      excerpt:", best_excerpt(page["text"], [query], 80)[:200].replace("\n", " "), "…")
    else:
        print(f"[skip] {page['error']}: {r['url'][:60]}")
