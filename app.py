import os
from flask import Flask, render_template, request, jsonify, redirect, url_for, Response, stream_with_context
from flask_login import LoginManager, login_required, current_user
from pathlib import Path
from collections import Counter
import sys
import json
import csv
import io
import re
import zipfile
import hashlib
import requests as http_req
from curl_cffi import requests as _curl_cffi
from html import unescape as _html_unescape

_lb_session = _curl_cffi.Session(impersonate="chrome131")

_POSTER_CACHE_DIR = Path(__file__).parent / "static" / "poster_cache"
_POSTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(Path(__file__).parent))
import FLM as flm

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-only-insecure-key")

# ── Database ──────────────────────────────────────────────────────────────────
_raw_db = os.environ.get("DATABASE_URL", "")
if _raw_db and "sslmode" not in _raw_db and "postgresql" in _raw_db:
    _raw_db += ("&" if "?" in _raw_db else "?") + "sslmode=require"
app.config["SQLALCHEMY_DATABASE_URI"]      = _raw_db or "sqlite:///flm_local.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["GOOGLE_CLIENT_ID"]             = os.environ.get("GOOGLE_CLIENT_ID",     "")
app.config["GOOGLE_CLIENT_SECRET"]         = os.environ.get("GOOGLE_CLIENT_SECRET", "")

from models import db, User, FilmLibrary
db.init_app(app)

login_manager = LoginManager(app)
login_manager.login_view  = "auth.login"
login_manager.login_message = ""

@login_manager.user_loader
def _load_user(user_id):
    return User.query.get(int(user_id))

from auth import auth_bp, init_auth
app.register_blueprint(auth_bp)
init_auth(app)

with app.app_context():
    db.create_all()

# ── Per-user in-memory state ──────────────────────────────────────────────────
_poster_cache = {}
_user_state   = {}


class _EmptyManager:
    films = []


def _default_state():
    return {
        "manager":            _EmptyManager(),
        "data_loaded":        False,
        "is_preview":         False,
        "has_dates":          True,
        "pending_imdb_films": [],
        "lb_username":        "",
        "lb_avatar":          "",
    }


def _get_state(uid=None):
    if uid is None:
        if not current_user.is_authenticated:
            return _default_state()
        uid = current_user.id
    if uid not in _user_state:
        try:
            lib = FilmLibrary.query.filter_by(user_id=uid).first()
            if lib and lib.films_json:
                films = [_dict_to_film(d) for d in json.loads(lib.films_json)]
                class _M:
                    pass
                m = _M()
                m.films = films
                _user_state[uid] = {
                    "manager":            m,
                    "data_loaded":        True,
                    "is_preview":         False,
                    "has_dates":          lib.has_dates,
                    "pending_imdb_films": [],
                    "lb_username":        "",
                    "lb_avatar":          "",
                }
            else:
                _user_state[uid] = _default_state()
        except Exception:
            _user_state[uid] = _default_state()
    return _user_state[uid]


def _save_library(uid):
    try:
        st = _user_state.get(uid)
        if not st or not st["data_loaded"]:
            return
        films_data = [_film_to_dict(f) for f in st["manager"].films]
        lib = FilmLibrary.query.filter_by(user_id=uid).first()
        if lib:
            lib.films_json = json.dumps(films_data)
            lib.has_dates  = st["has_dates"]
        else:
            lib = FilmLibrary(
                user_id=uid,
                films_json=json.dumps(films_data),
                has_dates=st["has_dates"],
            )
            db.session.add(lib)
        db.session.commit()
    except Exception:
        db.session.rollback()


# ── Film helpers ──────────────────────────────────────────────────────────────
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


def _film_to_dict(f):
    return {
        "title":        f.title,
        "year":         f.year,
        "date_watched": f.date_watched,
        "rank_idx":     f.rank_idx,
        "runtime":      getattr(f, "runtime",      ""),
        "release_date": getattr(f, "release_date", ""),
        "directors":    getattr(f, "directors",    []),
        "actors":       getattr(f, "actors",       []),
        "genres":       getattr(f, "genres",       []),
        "composer":     getattr(f, "composer",     ""),
        "views":        getattr(f, "views",        1),
    }


def _dict_to_film(d):
    f = ScrapedFilm(d["title"], d["year"], d["date_watched"], d["rank_idx"])
    f.runtime      = d.get("runtime",      "")
    f.release_date = d.get("release_date", "")
    f.directors    = d.get("directors",    [])
    f.actors       = d.get("actors",       [])
    f.genres       = d.get("genres",       [])
    f.composer     = d.get("composer",     "")
    f.views        = d.get("views",        1)
    return f


_LB_NS_LB   = "https://letterboxd.com"
_LB_NS_TMDB = "https://themoviedb.org"


def _lb_rss_entries(username):
    """Yield (title, year, date_watched, tmdb_id, rating) from the Letterboxd RSS feed."""
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
        title    = item.findtext(f"{{{_LB_NS_LB}}}filmTitle") or ""
        year     = item.findtext(f"{{{_LB_NS_LB}}}filmYear")  or ""
        date_iso = item.findtext(f"{{{_LB_NS_LB}}}watchedDate") or ""
        tmdb_id  = item.findtext(f"{{{_LB_NS_TMDB}}}movieId")   or ""
        rating_s = item.findtext(f"{{{_LB_NS_LB}}}memberRating") or ""
        rating   = float(rating_s) if rating_s else 0.0
        if not title:
            continue
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


# ── Context processor ─────────────────────────────────────────────────────────
@app.context_processor
def _inject_globals():
    if current_user.is_authenticated:
        try:
            st = _get_state()
            return {
                "data_loaded":   st["data_loaded"],
                "is_preview":    st["is_preview"],
                "preview_count": len(st["manager"].films) if st["is_preview"] else 0,
                "has_dates":     st["has_dates"],
            }
        except Exception:
            pass
    return {"data_loaded": False, "is_preview": False, "preview_count": 0, "has_dates": True}


# ── Routes ────────────────────────────────────────────────────────────────────
@app.route("/api/reset")
@login_required
def api_reset():
    uid = current_user.id
    _user_state.pop(uid, None)
    try:
        lib = FilmLibrary.query.filter_by(user_id=uid).first()
        if lib:
            db.session.delete(lib)
            db.session.commit()
    except Exception:
        db.session.rollback()
    return jsonify({"reset": True})


@app.route("/api/load-demo")
@login_required
def api_load_demo():
    uid = current_user.id
    flm.DATA_FILE = _find_data_file()
    m = flm.FilmManager()
    st = _get_state(uid)
    st["manager"]     = m
    st["data_loaded"] = True
    st["is_preview"]  = False
    _save_library(uid)
    return jsonify({"loaded": True, "count": len(m.films)})


def _lb_regex_parse_films(html_text):
    films = []
    seen  = set()
    parts = re.split(r'(?=data-target-link="/film/)', html_text)
    for part in parts[1:]:
        slug_m = re.match(r'data-target-link="/film/([^"/]+)/"', part)
        if not slug_m:
            continue
        slug = slug_m.group(1)
        if not slug or slug in seen:
            continue
        seen.add(slug)
        chunk = part[:1500]
        alt_m = re.search(r'\balt="([^"]*)"', chunk)
        title = _html_unescape(alt_m.group(1)) if alt_m and alt_m.group(1) else ""
        if not title:
            continue
        rated_m = re.search(r'\brated-(\d+)\b', chunk)
        rated   = int(rated_m.group(1)) if rated_m else -1
        year_m  = re.search(r'-(\d{4})$', slug)
        year    = year_m.group(1) if year_m else ""
        films.append({
            "title":  title,
            "year":   year,
            "slug":   slug,
            "rating": rated / 2.0 if rated > 0 else 0.0,
        })
    return films


@app.route("/api/debug-letterboxd")
@login_required
def api_debug_letterboxd():
    username = request.args.get("username", "").strip().lower()
    if not username:
        return jsonify({"error": "No username"}), 400
    results = {}
    for page in (1, 2):
        url = f"https://letterboxd.com/{username}/films/page/{page}/"
        try:
            r = _lb_session.get(url, timeout=20)
        except Exception as e:
            results[f"page{page}"] = {"error": str(e)}
            continue
        html  = r.text
        films = _lb_regex_parse_films(html)
        has_next = bool(
            re.search(r'class="[^"]*paginate-next[^"]*"', html) or
            re.search(r'rel="next"', html)
        )
        results[f"page{page}"] = {
            "status_code":            r.status_code,
            "html_length":            len(html),
            "films_parsed":           len(films),
            "first_3_films":          films[:3],
            "has_next_link":          has_next,
            "data_target_link_count": html.count("data-target-link"),
        }
    return jsonify(results)


@app.route("/api/scrape-letterboxd")
@login_required
def api_scrape_letterboxd():
    uid      = current_user.id
    username = request.args.get("username", "").strip().lower()
    if not username:
        return jsonify({"error": "No username"}), 400
    st = _get_state(uid)
    st["lb_username"] = username
    st["lb_avatar"]   = ""

    def generate():
        def ev(msg_type, **kwargs):
            return f"data: {json.dumps({'type': msg_type, **kwargs})}\n\n"

        s = _get_state(uid)

        yield ev("status", message="Connecting to Letterboxd…")
        all_film_data = []
        seen_slugs    = set()
        page = 1
        while True:
            yield ev("status", message=f"Fetching film list — page {page}… ({len(all_film_data)} found so far)")
            yield f": keepalive\n\n"
            url = f"https://letterboxd.com/{username}/films/page/{page}/"
            try:
                r = _lb_session.get(url, timeout=20)
            except Exception as e:
                if page == 1:
                    yield ev("error", message=f"Network error: {e}")
                    return
                break
            if r.status_code == 404 and page == 1:
                yield ev("error", message=f"User '{username}' not found on Letterboxd.")
                return
            if r.status_code != 200:
                if page == 1:
                    yield ev("error", message=f"Letterboxd returned HTTP {r.status_code}. The profile may be private.")
                    return
                break
            page_films = _lb_regex_parse_films(r.text)
            if not page_films:
                break
            for f in page_films:
                if f["slug"] and f["slug"] not in seen_slugs:
                    seen_slugs.add(f["slug"])
                    all_film_data.append(f)
            page += 1

        if not all_film_data:
            yield ev("error", message="No films found. Make sure the profile is public and has watched films.")
            return

        all_film_data.sort(key=lambda f: -f["rating"])

        yield ev("status", message=f"Found {len(all_film_data)} films. Fetching watch dates…")
        diary_dates = {}
        diary_page  = 1
        while True:
            yield ev("status", message=f"Fetching watch dates — page {diary_page}… ({len(diary_dates)} dates so far)")
            url = f"https://letterboxd.com/{username}/films/diary/page/{diary_page}/"
            try:
                r = _lb_session.get(url, timeout=20)
            except Exception:
                break
            if r.status_code != 200:
                break
            html_text     = r.text
            found_any_row = False
            for row_m in re.finditer(r'<tr[^>]*diary-entry-row[^>]*>(.*?)</tr>', html_text, re.DOTALL):
                row    = row_m.group(1)
                slug_m = re.search(r'data-film-slug="([^"]+)"', row)
                date_m = re.search(r'/diary/for/(\d{4})/(\d{1,2})/(\d{1,2})/', row)
                if slug_m and date_m:
                    slug = slug_m.group(1)
                    y, mo, day = date_m.groups()
                    if slug not in diary_dates:
                        diary_dates[slug] = f"{int(day):02d}/{int(mo):02d}/{y}"
                    found_any_row = True
            if not found_any_row:
                break
            if not re.search(r'class="[^"]*paginate-nextprev-next[^"]*"', html_text):
                break
            diary_page += 1
            if diary_page > 300:
                break

        has_any_dates = bool(diary_dates)
        if has_any_dates:
            yield ev("status", message=f"Got dates for {len(diary_dates)} films. Enriching with TMDB data…")
        else:
            yield ev("status", message=f"No diary dates found. Enriching {len(all_film_data)} films with TMDB…")

        films = []
        for i, fd in enumerate(all_film_data):
            date_watched = diary_dates.get(fd["slug"], "")
            film = ScrapedFilm(fd["title"], fd["year"], date_watched, rank_idx=i)
            yield ev("progress", current=i + 1, total=len(all_film_data), film=fd["title"])
            _tmdb_enrich(film)
            films.append(film)

        class _M:
            pass
        m = _M()
        m.films = films

        s["manager"]     = m
        s["data_loaded"] = True
        s["is_preview"]  = False
        s["has_dates"]   = has_any_dates
        _save_library(uid)
        yield ev("done", count=len(films))

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@app.route("/api/upload-letterboxd-zip", methods=["POST"])
@login_required
def api_upload_letterboxd_zip():
    uid = current_user.id
    f   = request.files.get("zip")
    if not f:
        return jsonify({"error": "No file"}), 400
    try:
        zf = zipfile.ZipFile(io.BytesIO(f.read()))
    except Exception:
        return jsonify({"error": "Invalid ZIP file — please upload the ZIP downloaded from letterboxd.com/settings/data"}), 400

    names = zf.namelist()

    def _parse_date(row):
        date_iso = (row.get("Watched Date") or row.get("Date") or "").strip()
        if len(date_iso) == 10 and date_iso[4] == "-":
            yr, mo, dy = date_iso.split("-")
            return f"{dy}/{mo}/{yr}"
        return date_iso

    films_by_slug = {}
    watched_name  = next((n for n in names if n.lower().endswith("watched.csv")), None)
    if not watched_name:
        return jsonify({"error": "No watched.csv found in ZIP"}), 400
    reader = csv.DictReader(io.StringIO(zf.read(watched_name).decode("utf-8-sig")))
    for row in reader:
        title = row.get("Name", "").strip()
        if not title:
            continue
        uri  = row.get("Letterboxd URI", "").strip()
        slug = uri.rstrip("/").split("/")[-1] if uri else title.lower()
        rating_s = row.get("Rating", "").strip()
        films_by_slug[slug] = {
            "title":  title,
            "year":   row.get("Year", "").strip(),
            "date":   _parse_date(row),
            "rating": float(rating_s) if rating_s else 0.0,
        }

    ratings_name = next((n for n in names if n.lower().endswith("ratings.csv")), None)
    if ratings_name:
        reader = csv.DictReader(io.StringIO(zf.read(ratings_name).decode("utf-8-sig")))
        for row in reader:
            uri      = row.get("Letterboxd URI", "").strip()
            slug     = uri.rstrip("/").split("/")[-1] if uri else ""
            rating_s = row.get("Rating", "").strip()
            if slug in films_by_slug and films_by_slug[slug]["rating"] == 0.0 and rating_s:
                films_by_slug[slug]["rating"] = float(rating_s)

    if not films_by_slug:
        return jsonify({"error": "No films found in ZIP"}), 400

    raw   = [(v["title"], v["year"], v["date"], v["rating"]) for v in films_by_slug.values()]
    raw.sort(key=lambda x: -x[3])
    films = [ScrapedFilm(t, y, d, rank_idx=i) for i, (t, y, d, _r) in enumerate(raw)]

    class _M:
        pass
    m = _M()
    m.films = films

    st = _get_state(uid)
    st["manager"]     = m
    st["data_loaded"] = True
    st["is_preview"]  = False
    st["has_dates"]   = True
    _save_library(uid)
    return jsonify({"loaded": True, "count": len(films)})


@app.route("/api/upload-imdb-csv", methods=["POST"])
@login_required
def api_upload_imdb_csv():
    uid = current_user.id
    f   = request.files.get("csv")
    if not f:
        return jsonify({"error": "No file"}), 400
    content = f.read().decode("utf-8-sig")
    reader  = csv.DictReader(io.StringIO(content))
    raw     = []
    for row in reader:
        title = row.get("Title", "").strip()
        if not title:
            continue
        year     = row.get("Year", "").strip()
        rating_s = row.get("Your Rating", "").strip()
        rating   = float(rating_s) / 2.0 if rating_s else 0.0
        date_iso = (row.get("Date Rated") or row.get("Created") or "").strip()
        if len(date_iso) == 10 and date_iso[4] == "-":
            yr, mo, dy = date_iso.split("-")
            date_watched = f"{dy}/{mo}/{yr}"
        else:
            date_watched = date_iso
        runtime     = row.get("Runtime (mins)", "").strip()
        directors_s = row.get("Directors", "").strip()
        directors   = [d.strip() for d in directors_s.split(",") if d.strip()]
        genres_s    = row.get("Genres", "").strip()
        genres      = [g.strip() for g in genres_s.split(",") if g.strip()]
        imdb_id     = row.get("Const", "").strip()
        raw.append((title, year, date_watched, rating, runtime, directors, genres, imdb_id))
    if not raw:
        return jsonify({"error": "No films found in CSV"}), 400
    raw.sort(key=lambda x: -x[3])
    st = _get_state(uid)
    st["pending_imdb_films"] = raw
    return jsonify({"staged": True, "count": len(raw)})


@app.route("/api/enrich-imdb-films")
@login_required
def api_enrich_imdb_films():
    uid = current_user.id

    def generate():
        def ev(msg_type, **kwargs):
            return f"data: {json.dumps({'type': msg_type, **kwargs})}\n\n"

        s   = _get_state(uid)
        raw = s.get("pending_imdb_films", [])
        if not raw:
            yield ev("error", message="No staged films found — please upload the CSV again.")
            return

        yield ev("status", message=f"Fetching cast & crew for {len(raw)} films from TMDB…")
        films = []
        for i, (t, y, d, _r, rt, dirs, gens, imdb_id) in enumerate(raw):
            film           = ScrapedFilm(t, y, d, rank_idx=i)
            film.runtime   = rt
            film.directors = dirs
            film.genres    = gens
            yield ev("progress", current=i + 1, total=len(raw), film=t)
            _tmdb_enrich(film, imdb_id=imdb_id)
            films.append(film)

        class _M:
            pass
        m = _M()
        m.films = films

        s["manager"]            = m
        s["data_loaded"]        = True
        s["is_preview"]         = False
        s["pending_imdb_films"] = []
        _save_library(uid)
        yield ev("done", count=len(films))

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


def _total_minutes():
    st = _get_state()
    return sum(
        int(f.runtime) * max(int(f.views), 1)
        for f in st["manager"].films
        if str(f.runtime).strip().isdigit()
    )


@app.route("/")
def index():
    return redirect(url_for("poster"))


@app.route("/home")
@login_required
def home():
    st     = _get_state()
    ranked = sorted(st["manager"].films, key=lambda f: f.rank_idx)
    recent = st["manager"].films[-12:][::-1]
    mins   = _total_minutes()

    directors, actors, genres = Counter(), Counter(), Counter()
    years_watched = Counter()
    for film in st["manager"].films:
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
        total=len(st["manager"].films),
        hours=round(mins / 60),
        this_year=this_year,
        top5=ranked[:5],
        recent=recent,
        top_directors=directors.most_common(5),
        top_actors=actors.most_common(5),
        top_genres=genres.most_common(8),
    )


@app.route("/welcome")
@login_required
def welcome():
    st = _get_state()
    if not st["data_loaded"]:
        return redirect(url_for("poster"))
    total = len(st["manager"].films)
    top_n = 100 if total >= 100 else (50 if total >= 50 else 10 if total >= 10 else total)

    from datetime import date as _date
    current_year  = _date.today().year
    years_in_data = {y for f in st["manager"].films if (y := _watch_year(f)) is not None}
    past_years    = sorted([y for y in years_in_data if y < current_year], reverse=True)
    last_year     = past_years[0] if past_years else (max(years_in_data) if years_in_data else current_year - 1)
    last_year_count = sum(1 for f in st["manager"].films if _watch_year(f) == last_year)

    return render_template("welcome.html",
        total=total, top_n=top_n,
        last_year=last_year, last_year_count=last_year_count,
    )


@app.route("/poster")
@login_required
def poster():
    import re as _re
    st    = _get_state()
    years = set()
    for f in st["manager"].films:
        if f.date_watched:
            m = (_re.match(r'^(\d{4})', f.date_watched) or
                 _re.match(r'^\d{1,2}/\d{1,2}/(\d{4})$', f.date_watched))
            if m:
                years.add(int(m.group(1)))
    min_year = min(years) if years else 2000
    max_year = max(years) if years else 2025
    has_cast = any(
        (getattr(f, "directors", None) and any(d.strip() and d.upper() != "N/A" for d in f.directors)) or
        (getattr(f, "actors",    None) and any(a.strip() and a.upper() != "N/A" for a in f.actors))
        for f in st["manager"].films
    )
    return render_template("poster.html", total=len(st["manager"].films),
                           min_year=min_year, max_year=max_year, has_cast=has_cast)


def _watch_year(f):
    import re as _re
    if not f.date_watched:
        return None
    m = (_re.match(r'^(\d{4})', f.date_watched) or
         _re.match(r'^\d{1,2}/\d{1,2}/(\d{4})$', f.date_watched))
    return int(m.group(1)) if m else None


def _chrono_key(f):
    """Sortable (year, month, day) tuple from DD/MM/YYYY. Missing dates sort last."""
    import re as _re
    dw = f.date_watched or ""
    m  = _re.match(r'^(\d{1,2})/(\d{1,2})/(\d{4})$', dw)
    if m:
        d, mo, y = m.groups()
        return (int(y), int(mo), int(d))
    m = _re.match(r'^(\d{4})[-/](\d{1,2})[-/](\d{1,2})', dw)
    if m:
        y, mo, d = m.groups()
        return (int(y), int(mo), int(d))
    return (9999, 99, 99)


@app.route("/api/films")
@login_required
def api_films():
    st        = _get_state()
    view      = request.args.get("view",      "ranked")
    limit     = request.args.get("limit",     type=int, default=None)
    from_year = request.args.get("from_year", type=int, default=None)
    to_year   = request.args.get("to_year",   type=int, default=None)
    year_type = request.args.get("year_type", "watch")
    films_list = (
        sorted(st["manager"].films, key=_chrono_key) if view == "chrono"
        else sorted(st["manager"].films, key=lambda f: f.rank_idx)
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


@app.route("/api/user-info")
@login_required
def api_user_info():
    uid = current_user.id
    st  = _get_state(uid)
    lb_username = st["lb_username"]
    if not lb_username:
        return jsonify({"username": "", "avatar": ""})
    if not st["lb_avatar"]:
        try:
            r = http_req.get(
                f"https://letterboxd.com/{lb_username}/",
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
                timeout=6,
            )
            m = re.search(r'<img[^>]+class="[^"]*\bavatar\b[^"]*"[^>]+src="([^"]+)"', r.text)
            if not m:
                m = re.search(r'<img[^>]+src="([^"]+)"[^>]+class="[^"]*\bavatar\b[^"]*"', r.text)
            if m:
                url = m.group(1)
                if url.startswith("//"):
                    url = "https:" + url
                st["lb_avatar"] = url
            else:
                m2 = re.search(r'<meta\s+property=["\']og:image["\']\s+content=["\']([^"\']+)["\']', r.text)
                if not m2:
                    m2 = re.search(r'<meta\s+content=["\']([^"\']+)["\']\s+property=["\']og:image["\']', r.text)
                if m2:
                    url = m2.group(1)
                    if "a.ltrbxd.com" in url or "resized" in url:
                        st["lb_avatar"] = url
        except Exception:
            pass
    avatar_proxy = "/api/proxy/avatar" if st["lb_avatar"] else ""
    return jsonify({"username": lb_username, "avatar": avatar_proxy})


@app.route("/api/proxy/avatar")
@login_required
def api_proxy_avatar():
    uid       = current_user.id
    st        = _get_state(uid)
    lb_avatar = st["lb_avatar"]
    if not lb_avatar:
        return "Not found", 404
    try:
        r = http_req.get(lb_avatar, headers={"User-Agent": "Mozilla/5.0"}, timeout=6, stream=True)
        from flask import make_response
        resp = make_response(r.content)
        resp.headers["Content-Type"]               = r.headers.get("Content-Type", "image/jpeg")
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Cache-Control"]              = "public, max-age=3600"
        return resp
    except Exception:
        return "Error", 500


@app.route("/api/reorder", methods=["POST"])
@login_required
def api_reorder():
    uid    = current_user.id
    st     = _get_state(uid)
    data   = request.get_json(silent=True) or {}
    titles = data.get("titles", [])
    if not titles:
        return jsonify({"error": "No titles"}), 400
    title_map = {f.title: f for f in st["manager"].films}
    for idx, title in enumerate(titles):
        if title in title_map:
            title_map[title].rank_idx = idx
    in_list = set(titles)
    tail    = sorted((f for f in st["manager"].films if f.title not in in_list), key=lambda f: f.rank_idx)
    for i, f in enumerate(tail):
        f.rank_idx = len(titles) + i
    _save_library(uid)
    return jsonify({"ok": True})


@app.route("/films")
@login_required
def films():
    st    = _get_state()
    view  = request.args.get("view", "ranked")
    query = request.args.get("q", "").strip().lower()
    films_list = (
        sorted(st["manager"].films, key=_chrono_key) if view == "chrono"
        else sorted(st["manager"].films, key=lambda f: f.rank_idx)
    )
    if query:
        films_list = [f for f in films_list if query in f.title.lower()]
    return render_template("films.html",
        films=films_list, view=view, query=query, total=len(films_list)
    )


@app.route("/film/<path:title>")
@login_required
def film_detail(title):
    st   = _get_state()
    film = next((f for f in st["manager"].films if f.title == title), None)
    if not film:
        return redirect(url_for("films"))
    ranked     = sorted(st["manager"].films, key=lambda f: f.rank_idx)
    rank_pos   = next((i + 1 for i, f in enumerate(ranked)              if f is film), "—")
    chrono_pos = next((i + 1 for i, f in enumerate(st["manager"].films) if f is film), "—")
    return render_template("film.html",
        film=film, rank_pos=rank_pos, chrono_pos=chrono_pos,
        total=len(st["manager"].films),
    )


@app.route("/stats")
@login_required
def stats():
    st = _get_state()
    directors, actors, genres, composers = Counter(), Counter(), Counter(), Counter()
    for film in st["manager"].films:
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
        total=len(st["manager"].films),
        hours=round(_total_minutes() / 60),
    )


@app.route("/api/cast-stats")
@login_required
def api_cast_stats():
    st    = _get_state()
    view  = request.args.get("view", "ranked")
    limit = request.args.get("limit", type=int, default=None)
    films_list = (
        sorted(st["manager"].films, key=_chrono_key) if view == "chrono"
        else sorted(st["manager"].films, key=lambda f: f.rank_idx)
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
@login_required
def api_poster():
    title = request.args.get("title", "").strip()
    year  = request.args.get("year",  "").strip()
    if not title:
        return jsonify({"url": None})
    key = f"{title}|{year}"
    if key in _poster_cache:
        return jsonify({"url": _poster_cache[key]})
    fname = hashlib.md5(key.encode()).hexdigest() + ".jpg"
    local = _POSTER_CACHE_DIR / fname
    if local.exists() and local.stat().st_size > 0:
        url = f"/static/poster_cache/{fname}"
        _poster_cache[key] = url
        return jsonify({"url": url})
    try:
        params = {"api_key": flm.TMDB_API_KEY, "query": title}
        if year:
            params["year"] = year
        results = http_req.get(
            f"{flm.TMDB_BASE_URL}/search/movie", params=params, timeout=5
        ).json().get("results", [])
        url = None
        if results and results[0].get("poster_path"):
            tmdb_url = f"https://image.tmdb.org/t/p/w185{results[0]['poster_path']}"
            img = http_req.get(tmdb_url, timeout=8)
            if img.status_code == 200:
                local.write_bytes(img.content)
                url = f"/static/poster_cache/{fname}"
            else:
                url = tmdb_url
        _poster_cache[key] = url
        return jsonify({"url": url})
    except Exception:
        return jsonify({"url": None})


if __name__ == "__main__":
    import webbrowser, threading
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
