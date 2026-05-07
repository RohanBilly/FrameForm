from flask import Flask, render_template, request, jsonify, redirect, url_for
from pathlib import Path
from collections import Counter
import glob
import sys
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

flm.DATA_FILE = _find_data_file()
manager = flm.FilmManager()


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
    return render_template("poster.html", total=len(manager.films),
                           min_year=min_year, max_year=max_year)


def _watch_year(f):
    import re as _re
    if not f.date_watched:
        return None
    m = _re.match(r'^(\d{4})', f.date_watched) or \
        _re.match(r'^\d{1,2}/\d{1,2}/(\d{4})$', f.date_watched)
    return int(m.group(1)) if m else None

@app.route("/api/films")
def api_films():
    view      = request.args.get("view", "ranked")
    limit     = request.args.get("limit",     type=int, default=None)
    from_year = request.args.get("from_year", type=int, default=None)
    to_year   = request.args.get("to_year",   type=int, default=None)
    films_list = (
        list(manager.films) if view == "chrono"
        else sorted(manager.films, key=lambda f: f.rank_idx)
    )
    if from_year is not None or to_year is not None:
        films_list = [
            f for f in films_list
            if (y := _watch_year(f)) is not None
            and (from_year is None or y >= from_year)
            and (to_year   is None or y <= to_year)
        ]
    elif limit:
        films_list = films_list[:limit]
    return jsonify([{
        "title": f.title,
        "year":  f.year or "",
        "rank":  f.rank_idx + 1,
        "date":  f.date_watched or "",
    } for f in films_list])


@app.route("/films")
def films():
    view  = request.args.get("view", "ranked")
    query = request.args.get("q", "").strip().lower()

    films_list = (
        list(manager.films) if view == "chrono"
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
        list(manager.films) if view == "chrono"
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
    import webbrowser, threading
    threading.Timer(1.0, lambda: webbrowser.open("http://localhost:5000")).start()
    app.run(debug=False, port=5000)
