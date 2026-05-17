"""
One-time script: pre-fetch poster images for all demo films and save them
to static/poster_cache/ using the same key scheme as api_poster().
Run from the project root:  python prefetch_posters.py
"""

import hashlib
import time
from pathlib import Path
import requests

TMDB_API_KEY = "c9e7e3075b9ea8dae513713df3d0a139"
TMDB_BASE    = "https://api.themoviedb.org/3"
CACHE_DIR    = Path(__file__).parent / "static" / "poster_cache"
DATA_FILE    = Path(__file__).parent / "BillyVS_films.txt"

CACHE_DIR.mkdir(parents=True, exist_ok=True)

# ── Read titles + years from the demo data file ──────────────────────────────
ALL_SECTION_KEYS = [
    "CHRONILOGICAL LIST", "RANKED LIST OF FILMS SEEN", "DATES", "RUN TIMES",
    "DIRECTORS", "ACTORS", "GENRES", "COMPOSERS", "YEAR", "VIEW COUNT"
]

def read_demo_films(path):
    with open(path, encoding="utf-8") as f:
        lines = f.readlines()

    # Find ALL section header positions so we can bound each section correctly
    sec_starts = {}
    for i, line in enumerate(lines):
        for k in ALL_SECTION_KEYS:
            if k in line:
                sec_starts[k] = i

    def read_section(key):
        start = sec_starts[key] + 1
        # end = nearest next section header
        next_starts = [v for k, v in sec_starts.items() if v > sec_starts[key]]
        end = min(next_starts) if next_starts else len(lines)
        return [lines[i].strip() for i in range(start, end) if lines[i].strip()]

    titles = read_section("CHRONILOGICAL LIST")
    years  = read_section("YEAR")
    while len(years) < len(titles):
        years.append("")
    return list(zip(titles, years))

films = read_demo_films(DATA_FILE)
print(f"Found {len(films)} films in demo data")

# ── Fetch and save ────────────────────────────────────────────────────────────
already   = 0
fetched   = 0
not_found = 0
errors    = 0

for i, (title, year) in enumerate(films):
    key   = f"{title}|{year}"
    fname = hashlib.md5(key.encode()).hexdigest() + ".jpg"
    dest  = CACHE_DIR / fname

    if dest.exists() and dest.stat().st_size > 0:
        already += 1
        continue

    try:
        params = {"api_key": TMDB_API_KEY, "query": title}
        if year:
            params["year"] = year
        results = requests.get(
            f"{TMDB_BASE}/search/movie", params=params, timeout=8
        ).json().get("results", [])

        # If no result with year, try without
        if not results and year:
            results = requests.get(
                f"{TMDB_BASE}/search/movie",
                params={"api_key": TMDB_API_KEY, "query": title},
                timeout=8
            ).json().get("results", [])

        if results and results[0].get("poster_path"):
            img_url = f"https://image.tmdb.org/t/p/w185{results[0]['poster_path']}"
            img = requests.get(img_url, timeout=10)
            if img.status_code == 200:
                dest.write_bytes(img.content)
                fetched += 1
                print(f"[{i+1}/{len(films)}] OK  {title} ({year})")
            else:
                not_found += 1
                print(f"[{i+1}/{len(films)}] IMG {img.status_code}: {title}")
        else:
            not_found += 1
            print(f"[{i+1}/{len(films)}] --- not found: {title} ({year})")

        # Stay well within TMDB rate limit (40 req/10s)
        time.sleep(0.28)

    except Exception as e:
        errors += 1
        print(f"[{i+1}/{len(films)}] ERROR {title}: {e}")
        time.sleep(1)

print(f"\nDone. fetched={fetched}  already_cached={already}  not_found={not_found}  errors={errors}")
print(f"Cache dir: {CACHE_DIR}  ({sum(1 for f in CACHE_DIR.glob('*.jpg'))} files)")
