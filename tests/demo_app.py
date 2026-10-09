"""Run the full app offline with the fake model and fake web: no key, no internet, no tokens.

    streamlit run tests/demo_app.py

Useful for checking the interface and exports without spending your Groq quota.
Every report it shows is made-up test content.
"""

import os
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ.setdefault("GROQ_API_KEY", "gsk_offline_demo_key_not_real")

import fakes  # noqa: E402
import research_agent  # noqa: E402
import search_tools  # noqa: E402

fakes.install(research_agent, search_tools)
runpy.run_path(str(ROOT / "app.py"), run_name="__main__")
