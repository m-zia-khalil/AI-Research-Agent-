"""Live test 1: Groq through CrewAI, alone. Uses about 1-2K tokens.

    python tests/test_llm.py

Checks: the key works, the model string is right, reasoning_effort is accepted,
JSON comes back in a shape our Pydantic schema accepts, and how many tokens a
small call really uses.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import schema  # noqa: E402
from research_agent import MODEL, call_agent, make_llm  # noqa: E402
from utils import AppError, check_groq_key  # noqa: E402

ok, message = check_groq_key()
print(("[ok]  " if ok else "[FAIL] ") + message)
if not ok:
    sys.exit(1)

print(f"\n1. Plain call to {MODEL} …")
started = time.time()
try:
    reply = make_llm(200).call("Reply with exactly: Groq is working.")
    print(f"[ok]  {reply!r} ({time.time() - started:.1f} s)")
except Exception as error:
    print(f"[FAIL] {type(error).__name__}: {error}")
    print("       See docs/GUIDE.md → Troubleshooting → 'Groq authentication' / 'Invalid model'.")
    sys.exit(1)

print("\n2. Agent + task returning JSON validated by Pydantic …")
try:
    plan = call_agent('Plan 3 web search queries about: "how do heat pumps work"', schema.QUERY_PLAN_SHAPE,
                      schema.QueryPlan, 600)
    print(f"[ok]  queries: {plan.queries}")
except AppError as error:
    print(f"[FAIL] {error.title} {error.action}\n       detail: {error.detail}")
    sys.exit(1)

from utils import LIMITER  # noqa: E402
print(f"\nTokens recorded so far: {LIMITER.used_today}")
print("All good. If step 2 printed queries, structured output works through our JSON check.")
