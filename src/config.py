"""Shared paths and settings for MuseIQ Lite."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SONGS_PATH = ROOT / "data" / "songs.csv"
NOTES_PATH = ROOT / "data" / "culture_notes.json"
LOG_DIR = ROOT / "logs"
TRACE_DIR = LOG_DIR / "traces"
REPORT_DIR = ROOT / "reports"

MODEL = os.environ.get("MUSEIQ_MODEL", "claude-opus-5-5")
EFFORT = os.environ.get("MUSEIQ_EFFORT", "medium")

DEFAULT_K = 5
MAX_REQUEST_CHARS = 500
MAX_TURNS = 10            # hard stop on the agent loop
MAX_REVISIONS = 2         # guardrail-triggered rewrites before falling back
SPOTLIGHT_SHARE = 0.30    # at least 30% of picks from spotlight creators
