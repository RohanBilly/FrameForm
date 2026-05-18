"""
Fetch posters for the top ~1000 most popular films from TMDB that are NOT
already in the demo data, and save them to static/poster_cache/.

Unlike prefetch_posters.py (which searches by title), this script calls the
/movie/popular endpoint so each result comes with a poster_path directly —
no search step needed, much faster.

Run from the project root:  python prefetch_popular_posters.py
"""

import hashlib
import sys
import time
from pathlib import Path
import requests

def safe_print(*args, **kwargs):
    try:
        print(*args, **kwargs)
    except UnicodeEncodeError:
        text = " ".join(str(a) for a in args)
        print(text.encode("ascii", "replace").decode("ascii"), **kwargs)

TMDB_API_KEY = "c9e7e3075b9ea8dae513713df3d0a139"
TMDB_BASE    = "https://api.themoviedb.org/3"
CACHE_DIR    = Path(__file__).parent / "static" / "poster_cache"
DATA_FILE    = Path(__file__).parent / "BillyVS_films.txt"
TARGET       = 1000   # how many non-demo posters to collect

CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Load demo titles so we can skip them ─────────────────────────────────────
ALL_SECTION_KEYS = [
    "CHRONILOGICAL LIST", "RANKED LIST OF FILMS SEEN", "DATES", "RUN TIMES",
    "DIRECTORS", "ACTORS", "GENRES", "COMPOSERS", "YEAR", "VIEW COUNT"
]

def read_demo_titles(path):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()
    sec_starts = {}
    for i, line in enumerate(lines):
        for k in ALL_SECTION_KEYS:
            if k in line:
                sec_starts[k] = i
    start = sec_starts["CHRONILOGICAL LIST"] + 1
    next_starts = [v for k, v in sec_starts.items() if v > sec_starts["CHRONILOGICAL LIST"]]
    end = min(next_starts) if next_starts else len(lines)
    return {lines[i].strip().lower() for i in range(start, end) if lines[i].strip()}

demo_titles = read_demo_titles(DATA_FILE)
safe_print(f"Loaded {len(demo_titles)} demo titles to skip")

# ── Walk /movie/popular pages until we have TARGET new posters ────────────────
collected = 0
skipped_demo = 0
already_cached = 0
errors = 0
page = 1

while collected < TARGET:
    try:
        resp = requests.get(
            f"{TMDB_BASE}/movie/popular",
            params={"api_key": TMDB_API_KEY, "language": "en-US", "page": page},
            timeout=10,
        )
        data = resp.json()
    except Exception as e:
        safe_print(f"[page {page}] ERROR fetching page: {e}")
        time.sleep(2)
        page += 1
        continue

    results = data.get("results", [])
    if not results:
        safe_print(f"[page {page}] No results -- stopping (total_pages={data.get('total_pages')})")
        break

    for film in results:
        if collected >= TARGET:
            break

        title = film.get("title", "").strip()
        release_date = film.get("release_date", "")
        year = release_date[:4] if release_date else ""
        poster_path = film.get("poster_path")

        if not title or not poster_path:
            continue

        # Skip films already in the demo data (case-insensitive title match)
        if title.lower() in demo_titles:
            skipped_demo += 1
            continue

        key   = f"{title}|{year}"
        fname = hashlib.md5(key.encode()).hexdigest() + ".jpg"
        dest  = CACHE_DIR / fname

        if dest.exists() and dest.stat().st_size > 0:
            already_cached += 1
            collected += 1
            continue

        try:
            img_url = f"https://image.tmdb.org/t/p/w185{poster_path}"
            img = requests.get(img_url, timeout=10)
            if img.status_code == 200:
                dest.write_bytes(img.content)
                collected += 1
                safe_print(f"[{collected}/{TARGET}] OK  {title} ({year})")
            else:
                safe_print(f"[page {page}] IMG {img.status_code}: {title}")
            time.sleep(0.28)
        except Exception as e:
            errors += 1
            safe_print(f"[page {page}] ERROR downloading {title}: {e}")
            time.sleep(1)

    total_pages = data.get("total_pages", page)
    safe_print(f"  -- page {page}/{total_pages}  collected={collected}  skipped_demo={skipped_demo}  already_cached={already_cached}")
    page += 1
    time.sleep(0.28)

safe_print(f"\nDone. collected={collected}  already_cached={already_cached}  skipped_demo={skipped_demo}  errors={errors}")
safe_print(f"Cache dir: {CACHE_DIR}  ({sum(1 for f in CACHE_DIR.glob('*.jpg'))} files)")
