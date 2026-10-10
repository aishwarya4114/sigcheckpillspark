"""Tests never call live sponsors: blank the keys before anything reads .env (python-dotenv won't override)."""
import os

for var in ("NICRRON_API_KEY", "PRISMTRACE_API_KEY", "PRISMTRACE_PROJECT_ID"):
    os.environ[var] = ""
