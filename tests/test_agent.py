"""Live test 3: a complete Quick research run (about 6-9K tokens, ~1 minute).

    python tests/test_agent.py "How do heat pumps work in cold climates?"
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_agent import run_research  # noqa: E402
from utils import AppError  # noqa: E402

topic = sys.argv[1] if len(sys.argv) > 1 else "How do heat pumps work in cold climates?"
try:
    report = run_research(topic, "general", "Quick", "Brief", use_cache=False,
                          progress=lambda stage, message: print(f"  {stage:9s} {message}"))
except AppError as error:
    print(f"\n{error.title}\n{error.action}\n(detail: {error.detail})")
    sys.exit(1)

print(f"\nTITLE: {report['title']}\n\nSUMMARY: {report['executive_summary']}\n")
for i, f in enumerate(report["key_findings"], 1):
    print(f"{i}. {f['claim']}  [{f['confidence']}] {f['citations']}")
print(f"\nCould not verify: {len(report['could_not_verify'])} · citation fixes: {len(report['flags'])}")
print("Sources cited:")
for s in report["sources"]:
    if s.get("cited"):
        print(f"  [{s['id']}] Tier {s['tier']} {s['read_status']:9s} {s['url']}")
for m in report["scorecard"]["metrics"]:
    print(f"  {m['name']}: {m['display']} ({m['status']})")
print(f"\nTokens used (estimate): {report['metadata']['tokens_estimate']}, time: {report['metadata']['duration_seconds']} s")
