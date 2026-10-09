"""Phase 1, step 1: check your setup. No internet calls, no tokens used.

Run from the project folder:
    python tests/check_setup.py
"""

import importlib
import sys
from pathlib import Path

# Let this script import utils.py from the project folder above tests/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ok = True

# 1. Python version: we use 3.12 locally and on Streamlit Cloud.
version = sys.version_info
if (version.major, version.minor) == (3, 12):
    print(f"[ok]   Python {version.major}.{version.minor}.{version.micro}")
else:
    print(f"[warn] Python {version.major}.{version.minor} found; this project expects 3.12")

# 2. Every library imports, and its version matches requirements.txt.
#    (import name, pip name)
packages = [
    ("crewai", "crewai"),
    ("litellm", "litellm"),
    ("streamlit", "streamlit"),
    ("ddgs", "ddgs"),
    ("trafilatura", "trafilatura"),
    ("pypdf", "pypdf"),
    ("docx", "python-docx"),
    ("fpdf", "fpdf2"),
    ("pptx", "python-pptx"),
    ("httpx", "httpx"),
    ("pydantic", "pydantic"),
    ("dotenv", "python-dotenv"),
    ("json_repair", "json-repair"),
]
from importlib.metadata import version as pkg_version  # noqa: E402

for import_name, pip_name in packages:
    try:
        importlib.import_module(import_name)
        print(f"[ok]   {pip_name} {pkg_version(pip_name)}")
    except Exception as error:  # show the reason, keep checking the rest
        ok = False
        print(f"[FAIL] {pip_name}: {error}")

# 3. The Groq key is present (not tested against Groq yet; that's step 2).
from utils import check_groq_key  # noqa: E402

key_ok, message = check_groq_key()
print(f"[{'ok' if key_ok else 'FAIL'}]{'   ' if key_ok else ' '}{message}")
ok = ok and key_ok

print("\nSetup looks good. Next: step 2, test Groq." if ok else "\nFix the FAIL lines above, then run this again.")
sys.exit(0 if ok else 1)
