from flask import Flask, render_template, request, jsonify, redirect, url_for, Response, stream_with_context
from pathlib import Path
from collections import Counter
import glob
import sys
import json
import csv
import io
import requests as http_req

sys.path.insert(0, str(Path(__file__).parent))
import FLM as flm

app = Flask(__name__)

_poster_cache = {}

def _find_data_file():
    here = Path(__file__).parent
    candidates = sorted(here.glob("*_films.txt"))
    if candidates:
        return str(candidates[0])
    for name in ["2020 Vision.txt", "2020 Vision MY DATA.txt"]:
        p = here / name
        if p.exists():
            return str(p)
    return flm.DATA_FILE

_data_loaded  = False
_is_preview   = False   # True when loaded via RSS (limited film count)
_pending_imdb_films = []  # staged after CSV parse, before TMDB enrichment

class _EmptyManager:
    films = []

manager = _EmptyManager()


class ScrapedFilm:
    def __init__(self, title, year, date_watched, rank_idx):
        self.title        = title
        self.year         = year
        self.date_watched = date_watched
        self.rank_idx     = rank_idx
        self.runtime      = ""
        self.release_date = ""
        self.directors    = []
        self.actors       = []
        self.genres       = []
        self.composer     = ""
        self.views        = 1


_LB_NS_LB   = "https://letterboxd.com"
_LB_NS_TMDB = "https://themoviedb.org"


def _lb_rss_entries(username):
    """Yield (title, year, date_watched, tmdb_id) from the Letterboxd RSS feed."""
    import xml.etree.ElementTree as ET
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    url = f"https://letterboxd.com/{username}/rss/"
    try:
        r = http_req.get(url, headers=headers, timeout=12)
    except Exception as e:
        raise RuntimeError(f"Network error: {e}")
    if r.status_code == 404:
        raise RuntimeError(f"User '{username}' not found on Letterboxd.")
    if r.status_code != 200:
        raise RuntimeError(f"Letterboxd returned HTTP {r.status_code}.")
    root = ET.fromstring(r.content)
    seen = set()
    for item in root.findall(".//item"):
        title = item.findtext(f"{{{_LB_NS_LB}}}filmTitle") or ""
        year  = item.findtext(f"{{{_LB_NS_LB}}}filmYear")  or ""
        date_iso = item.findtext(f"{{{_LB_NS_LB}}}watchedDate") or ""
        tmdb_id  = item.findtext(f"{{{_LB_NS_TMDB}}}movieId")   or ""
        rating_s = item.findtext(f"{{{_LB_NS_LB}}}memberRating") or ""
        rating   = float(rating_s) if rating_s else 0.0
        if not title:
            continue
        # Convert YYYY-MM-DD → DD/MM/YYYY
        if len(date_iso) == 10 and date_iso[4] == "-":
            y, m, d = date_iso.split("-")
            date_watched = f"{d}/{m}/{y}"
        else:
            date_watched = date_iso
        key = (title, year, date_watched)
        if key in seen:
            continue
        seen.add(key)
        yield title, year, date_watched, tmdb_id, rating


def _tmdb_enrich(film, tmdb_id="", imdb_id=""):
    """Fill in directors, actors, genres, runtime from TMDB."""
    try:
        if tmdb_id:
            mid = tmdb_id
        elif imdb_id:
            result = http_req.get(
                f"{flm.TMDB_BASE_URL}/find/{imdb_id}",
                params={"api_key": flm.TMDB_API_KEY, "external_source": "imdb_id"},
                timeout=5,
            ).json()
            movies = result.get("movie_results", [])
            if not movies:
                return
            mid = movies[0]["id"]
        else:
            params = {"api_key": flm.TMDB_API_KEY, "query": film.title}
            if film.year:
                params["year"] = film.year
            results = http_req.get(
                f"{flm.TMDB_BASE_URL}/search/movie", params=params, timeout=5
            ).json().get("results", [])
            if not results:
                return
            mid = results[0]["id"]
        details = http_req.get(
            f"{flm.TMDB_BASE_URL}/movie/{mid}",
            params={"api_key": flm.TMDB_API_KEY, "append_to_response": "credits"},
            timeout=5,
        ).json()
        film.runtime      = film.runtime      or str(details.get("runtime") or "")
        film.release_date = film.release_date or details.get("release_date", "")
        film.genres    = film.genres  or [g["name"] for g in details.get("genres", [])]
        crew = details.get("credits", {}).get("crew", [])
        film.directors = film.directors or [p["name"] for p in crew if p.get("job") == "Director"]
        cast = details.get("credits", {}).get("cast", [])
        film.actors = [p["name"] for p in cast[:8]]
    except Exception:
        pass


@app.context_processor
def _inject_globals():
    return {
        "data_loaded":   _data_loaded,
        "is_preview":    _is_preview,
        "preview_count": len(manager.films) if _is_preview else 0,
    }


@app.route("/api/reset")
def api_reset():
    global manager, _data_loaded, _is_preview, _pending_imdb_films
    manager = _EmptyManager()
    _data_loaded = False
    _is_preview = False
    _pending_imdb_films = []
    return jsonify({"reset": True})


@app.route("/api/load-demo")
def api_load_demo():
    global manager, _data_loaded, _is_preview
    flm.DATA_FILE = _find_data_file()
    manager = flm.FilmManager()
    _data_loaded = True
    _is_preview  = False
    return jsonify({"loaded": True, "count": len(manager.films)})


@app.route("/api/scrape-letterboxd")
def api_scrape_letterboxd():
    username = request.args.get("username", "").strip().lower()
    if not username:
        return jsonify({"error": "No username"}), 400

    def generate():
        global manager, _data_loaded, _is_preview

        def ev(msg_type, **kwargs):
            return f"data: {json.dumps({'type': msg_type, **kwargs})}\n\n"

        yield ev("status", message="Fetching film list from Letterboxd…")

        films = []
        try:
            entries = list(_lb_rss_entries(username))
        except Exception as e:
            yield ev("error", message=str(e))
            return

        if not entries:
            yield ev("error", message="No films found. Check the username and make sure your diary is public.")
            return

        # Sort by rating descending so rank_idx reflects star order
        entries.sort(key=lambda e: -e[4])
        yield ev("status", message=f"Found {len(entries)} films. Fetching details from TMDB…")

        for i, (title, year, date_watched, tmdb_id, _rating) in enumerate(entries):
            film = ScrapedFilm(title, year, date_watched, rank_idx=i)
            yield ev("progress", current=i + 1, total=len(entries), film=title)
            _tmdb_enrich(film, tmdb_id=tmdb_id)
            films.append(film)

        class _ScrapedManager:
            pass

        m = _ScrapedManager()
        m.films = films
        manager = m
        _data_loaded = True
        _is_preview  = True
        yield ev("done", count=len(films))

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


@app.route("/api/upload-letterboxd-csv", methods=["POST"])
def api_upload_letterboxd_csv():
    global manager, _data_loaded, _is_preview
    f = request.files.get("csv")
    if not f:
        return jsonify({"error": "No file"}), 400
    content = f.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    raw = []
    for row in reader:
        title = row.get("Name", "").strip()
        if not title:
            continue
        year = row.get("Year", "").strip()
        rating_s = row.get("Rating", "").strip()
        rating = float(rating_s) if rating_s else 0.0
        date_iso = (row.get("Watched Date") or row.get("Date") or "").strip()
        if len(date_iso) == 10 and date_iso[4] == "-":
            yr, mo, dy = date_iso.split("-")
            date_watched = f"{dy}/{mo}/{yr}"
        else:
            date_watched = date_iso
        raw.append((title, year, date_watched, rating))
    if not raw:
        return jsonify({"error": "No films found in CSV"}), 400
    raw.sort(key=lambda x: -x[3])
    films = [ScrapedFilm(t, y, d, rank_idx=i) for i, (t, y, d, _r) in enumerate(raw)]

    class _CsvManager:
        pass

    m = _CsvManager()
    m.films = films
    manager = m
    _data_loaded = True
    _is_preview  = False
    return jsonify({"loaded": True, "count": len(films)})


@app.route("/api/upload-imdb-csv", methods=["POST"])
def api_upload_imdb_csv():
    global _pending_imdb_films
    f = request.files.get("csv")
    if not f:
        return jsonify({"error": "No file"}), 400
    content = f.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(content))
    raw = []
    for row in reader:
        title = row.get("Title", "").strip()
        if not title:
            continue
        year = row.get("Year", "").strip()
        rating_s = row.get("Your Rating", "").strip()
        rating = float(rating_s) / 2.0 if rating_s else 0.0
        date_iso = (row.get("Date Rated") or row.get("Created") or "").strip()
        if len(date_iso) == 10 and date_iso[4] == "-":
            yr, mo, dy = date_iso.split("-")
            date_watched = f"{dy}/{mo}/{yr}"
        else:
            date_watched = date_iso
        runtime = row.get("Runtime (mins)", "").strip()
        directors_s = row.get("Directors", "").strip()
        directors = [d.strip() for d in directors_s.split(",") if d.strip()]
        genres_s = row.get("Genres", "").strip()
        genres = [g.strip() for g in genres_s.split(",") if g.strip()]
        imdb_id = row.get("Const", "").strip()
        raw.append((title, year, date_watched, rating, runtime, directors, genres, imdb_id))
    if not raw:
        return jsonify({"error": "No films found in CSV"}), 400
    raw.sort(key=lambda x: -x[3])
    _pending_imdb_films = raw
    return jsonify({"staged": True, "count": len(raw)})


@app.route("/api/enrich-imdb-films")
def api_enrich_imdb_films():
    def generate():
        global manager, _data_loaded, _is_preview, _pending_imdb_films

        def ev(msg_type, **kwargs):
            return f"data: {json.dumps({'type': msg_type, **kwargs})}\n\n"

        if not _pending_imdb_films:
            yield ev("error", message="No staged films found — please upload the CSV again.")
            return

        raw = _pending_imdb_films
        yield ev("status", message=f"Fetching cast & crew for {len(raw)} films from TMDB…")

        films = []
        for i, (t, y, d, _r, rt, dirs, gens, imdb_id) in enumerate(raw):
            film = ScrapedFilm(t, y, d, rank_idx=i)
            film.runtime = rt
            film.directors = dirs
            film.genres = gens
            yield ev("progress", current=i + 1, total=len(raw), film=t)
            _tmdb_enrich(film, imdb_id=imdb_id)
            films.append(film)

        class _ImdbManager:
            pass

        m = _ImdbManager()
        m.films = films
        manager = m
        _data_loaded = True
        _is_preview = False
        _pending_imdb_films = []
        yield ev("done", count=len(films))

    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"})


def _total_minutes():
    return sum(
        int(f.runtime) * max(int(f.views), 1)
        for f in manager.films
        if str(f.runtime).strip().isdigit()
    )


@app.route("/")
def index():
    return redirect(url_for("poster"))

@app.route("/home")
def home():
    ranked = sorted(manager.films, key=lambda f: f.rank_idx)
    recent = manager.films[-12:][::-1]
    mins = _total_minutes()

    directors, actors, genres = Counter(), Counter(), Counter()
    years_watched = Counter()
    for film in manager.films:
        for d in film.directors:
            if d.strip() and d.upper() != "N/A":
                directors[d] += 1
        for a in film.actors:
            if a.strip() and a.upper() != "N/A":
                actors[a] += 1
        for g in film.genres:
            if g.strip():
                genres[g] += 1
        if film.date_watched:
            y = film.date_watched.split("/")[-1]
            if y.isdigit():
                years_watched[y] += 1

    this_year = years_watched.get("2025", 0) or years_watched.get(
        str(max((int(k) for k in years_watched if k.isdigit()), default=0)), 0
    )

    return render_template("index.html", route="home",
        total=len(manager.films),
        hours=round(mins / 60),
        this_year=this_year,
        top5=ranked[:5],
        recent=recent,
        top_directors=directors.most_common(5),
        top_actors=actors.most_common(5),
        top_genres=genres.most_common(8),
    )


@app.route("/welcome")
def welcome():
    if not _data_loaded:
        return redirect(url_for("poster"))
    total = len(manager.films)
    top_n = 100 if total >= 100 else (50 if total >= 50 else 10 if total >= 10 else total)

    from datetime import date as _date
    current_year = _date.today().year
    years_in_data = {y for f in manager.films if (y := _watch_year(f)) is not None}
    past_years = sorted([y for y in years_in_data if y < current_year], reverse=True)
    last_year = past_years[0] if past_years else (max(years_in_data) if years_in_data else current_year - 1)
    last_year_count = sum(1 for f in manager.films if _watch_year(f) == last_year)

    return render_template("welcome.html",
        total=total, top_n=top_n,
        last_year=last_year, last_year_count=last_year_count,
    )


@app.route("/poster")
def poster():
    import re as _re
    years = set()
    for f in manager.films:
        if f.date_watched:
            m = _re.match(r'^(\d{4})', f.date_watched) or \
                _re.match(r'^\d{1,2}/\d{1,2}/(\d{4})$', f.date_watched)
            if m:
                years.add(int(m.group(1)))
    min_year = min(years) if years else 2000
    max_year = max(years) if years else 2025
    has_cast = any(
        (getattr(f, 'directors', None) and any(d.strip() and d.upper() != 'N/A' for d in f.directors)) or
        (getattr(f, 'actors',    None) and any(a.strip() and a.upper() != 'N/A' for a in f.actors))
        for f in manager.films
    )
    return render_template("poster.html", total=len(manager.films),
                           min_year=min_year, max_year=max_year, has_cast=has_cast)


def _watch_year(f):
    import re as _re
    if not f.date_watched:
        return None
    m = _re.match(r'^(\d{4})', f.date_watched) or \
        _re.match(r'^\d{1,2}/\d{1,2}/(\d{4})$', f.date_watched)
    return int(m.group(1)) if m else None


def _chrono_key(f):
    """Sortable (year, month, day) tuple from DD/MM/YYYY date_watched. Missing dates sort last."""
    import re as _re
    dw = f.date_watched or ""
    m = _re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', dw)
    if m:
        d, mo, y = m.groups()
        return (int(y), int(mo), int(d))
    m = _re.match(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})', dw)
    if m:
        y, mo, d = m.groups()
        return (int(y), int(mo), int(d))
    return (9999, 99, 99)

@app.route("/api/films")
def api_films():
    view      = request.args.get("view",      "ranked")
    limit     = request.args.get("limit",     type=int, default=None)
    from_year = request.args.get("from_year", type=int, default=None)
    to_year   = request.args.get("to_year",   type=int, default=None)
    year_type = request.args.get("year_type", "watch")  # 'watch' or 'release'
    films_list = (
        sorted(manager.films, key=_chrono_key) if view == "chrono"
        else sorted(manager.films, key=lambda f: f.rank_idx)
    )
    if from_year is not None or to_year is not None:
        if year_type == "release":
            films_list = [
                f for f in films_list
                if f.year and f.year.isdigit()
                and (from_year is None or int(f.year) >= from_year)
                and (to_year   is None or int(f.year) <= to_year)
            ]
        else:
            films_list = [
                f for f in films_list
                if (y := _watch_year(f)) is not None
                and (from_year is None or y >= from_year)
                and (to_year   is None or y <= to_year)
            ]
    elif limit:
        films_list = films_list[:limit]
    return jsonify([{
        "title":        f.title,
        "year":         f.year or "",
        "rank":         f.rank_idx + 1,
        "date":         f.date_watched or "",
        "release_date": getattr(f, "release_date", "") or "",
    } for f in films_list])


@app.route("/api/reorder", methods=["POST"])
def api_reorder():
    data   = request.get_json(silent=True) or {}
    titles = data.get("titles", [])
    if not titles:
        return jsonify({"error": "No titles"}), 400
    title_map = {f.title: f for f in manager.films}
    for idx, title in enumerate(titles):
        if title in title_map:
            title_map[title].rank_idx = idx
    # Films absent from the submitted list (e.g. filtered out) keep relative order at the end
    in_list = set(titles)
    tail = sorted((f for f in manager.films if f.title not in in_list), key=lambda f: f.rank_idx)
    for i, f in enumerate(tail):
        f.rank_idx = len(titles) + i
    if hasattr(manager, "save_data"):
        manager.save_data()
    return jsonify({"ok": True})


@app.route("/films")
def films():
    view  = request.args.get("view", "ranked")
    query = request.args.get("q", "").strip().lower()

    films_list = (
        sorted(manager.films, key=_chrono_key) if view == "chrono"
        else sorted(manager.films, key=lambda f: f.rank_idx)
    )
    if query:
        films_list = [f for f in films_list if query in f.title.lower()]

    return render_template("films.html",
        films=films_list, view=view, query=query, total=len(films_list)
    )


@app.route("/film/<path:title>")
def film_detail(title):
    film = next((f for f in manager.films if f.title == title), None)
    if not film:
        return redirect(url_for("films"))
    ranked     = sorted(manager.films, key=lambda f: f.rank_idx)
    rank_pos   = next((i + 1 for i, f in enumerate(ranked)         if f is film), "—")
    chrono_pos = next((i + 1 for i, f in enumerate(manager.films)  if f is film), "—")
    return render_template("film.html",
        film=film, rank_pos=rank_pos, chrono_pos=chrono_pos,
        total=len(manager.films)
    )


@app.route("/stats")
def stats():
    directors, actors, genres, composers = Counter(), Counter(), Counter(), Counter()
    for film in manager.films:
        for d in film.directors:
            if d.strip() and d.upper() != "N/A":
                directors[d] += 1
        for a in film.actors:
            if a.strip() and a.upper() != "N/A":
                actors[a] += 1
        for g in film.genres:
            if g.strip():
                genres[g] += 1
        c = (film.composer or "").strip()
        if c and c.upper() not in {"N/A", "NO COMPOSERS", ""}:
            composers[c] += 1

    top_genre_max = genres.most_common(1)[0][1] if genres else 1

    return render_template("stats.html",
        directors=directors.most_common(30),
        actors=actors.most_common(30),
        genres=genres.most_common(15),
        composers=composers.most_common(20),
        top_genre_max=top_genre_max,
        total=len(manager.films),
        hours=round(_total_minutes() / 60),
    )


@app.route("/api/cast-stats")
def api_cast_stats():
    view  = request.args.get("view", "ranked")
    limit = request.args.get("limit", type=int, default=None)
    films_list = (
        sorted(manager.films, key=_chrono_key) if view == "chrono"
        else sorted(manager.films, key=lambda f: f.rank_idx)
    )
    if limit:
        films_list = films_list[:limit]
    directors, actors = Counter(), Counter()
    for film in films_list:
        for d in film.directors:
            if d.strip() and d.upper() != "N/A":
                directors[d] += 1
        for a in film.actors:
            if a.strip() and a.upper() != "N/A":
                actors[a] += 1
    return jsonify({
        "actors":    actors.most_common(20),
        "directors": directors.most_common(20),
    })


@app.route("/api/poster")
def api_poster():
    title = request.args.get("title", "").strip()
    year  = request.args.get("year",  "").strip()
    if not title:
        return jsonify({"url": None})
    key = f"{title}|{year}"
    if key in _poster_cache:
        return jsonify({"url": _poster_cache[key]})
    try:
        params = {"api_key": flm.TMDB_API_KEY, "query": title}
        if year:
            params["year"] = year
        results = http_req.get(
            f"{flm.TMDB_BASE_URL}/search/movie", params=params, timeout=5
        ).json().get("results", [])
        url = None
        if results and results[0].get("poster_path"):
            url = f"https://image.tmdb.org/t/p/w185{results[0]['poster_path']}"
        _poster_cache[key] = url
        return jsonify({"url": url})
    except Exception:
        return jsonify({"url": None})


if __name__ == "__main__":
    import webbrowser, threading, os
    def _open():
        ff_paths = [
            r"C:\Program Files\Mozilla Firefox\firefox.exe",
            r"C:\Program Files (x86)\Mozilla Firefox\firefox.exe",
        ]
        ff = next((p for p in ff_paths if os.path.exists(p)), None)
        if ff:
            webbrowser.register("firefox", None, webbrowser.BackgroundBrowser(ff))
            webbrowser.get("firefox").open("http://localhost:5000")
        else:
            webbrowser.open("http://localhost:5000")
    threading.Timer(1.0, _open).start()
    app.run(debug=False, port=5000)
