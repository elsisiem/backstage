"""Runtime configuration for Backstage. Everything comes from the environment (.env in repo root)."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

DATA = ROOT / "data"
CACHE = DATA / "cache"
TIMELINES = DATA / "timeline"
for _p in (CACHE, TIMELINES):
    _p.mkdir(parents=True, exist_ok=True)

FALKORDB_HOST = os.getenv("FALKORDB_HOST", "127.0.0.1")
FALKORDB_PORT = int(os.getenv("FALKORDB_PORT", "6379"))
FALKORDB_PASSWORD = os.getenv("FALKORDB_PASSWORD") or None

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
OPENAI_EMBED_MODEL = os.getenv("OPENAI_EMBED_MODEL", "text-embedding-3-small")

BRIGHTDATA_API_KEY = os.getenv("BRIGHTDATA_API_KEY")
BRIGHTDATA_UNLOCKER_ZONE = os.getenv("BRIGHTDATA_UNLOCKER_ZONE", "web_unlocker1")
BRIGHTDATA_SERP_ZONE = os.getenv("BRIGHTDATA_SERP_ZONE", "serp_api1")

EXA_API_KEY = os.getenv("EXA_API_KEY")

# Conferences Backstage knows about. RainFocus event codes map to the GitHub Universe catalog API.
CONFERENCES = {
    "universe25": {
        "id": "universe25",
        "name": "GitHub Universe 2025",
        "short": "Universe 2025",
        "venue": "Fort Mason Center, San Francisco",
        "tz": "America/Los_Angeles",
        "days": ["2025-10-28", "2025-10-29"],
        "mode": "replay",
        "hashtags": ["#GitHubUniverse", "GitHub Universe"],
        "rainfocus_event": "universe25",
    },
    "universe26": {
        "id": "universe26",
        "name": "GitHub Universe 2026",
        "short": "Universe 2026",
        "venue": "Fort Mason Center, San Francisco",
        "tz": "America/Los_Angeles",
        "days": ["2026-10-28", "2026-10-29"],
        "mode": "live",
        "hashtags": ["#GitHubUniverse", "GitHub Universe"],
        "rainfocus_event": "universe26",
    },
}
