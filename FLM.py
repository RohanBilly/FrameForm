# film_tracker_updated.py

import os
import requests
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
try:
    import matplotlib.pyplot as plt
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False

import time
import random
import sys

DATA_FILE = "2020 Vision.txt"
BACKUP_FILE = "2020 Vision Backup.txt"
TMDB_API_KEY = os.environ.get("TMDB_API_KEY", "c9e7e3075b9ea8dae513713df3d0a139")
TMDB_BASE_URL = "https://api.themoviedb.org/3"

def is_android():
    import os
    return "ANDROID_ROOT" in os.environ

def safe_input(prompt=""):
        try:
            return input(prompt).strip()
        except EOFError:
            print("Input failed. Please run this in an interactive terminal.")
            return ""

def pause_print(text="", delay=0.005):
    print(text)
    time.sleep(delay)

def clean_text(text):
    try:
        return text.encode("latin1").decode("utf-8")
    except:
        return text.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")

def clean_entire_file(filename):
        with open(filename, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()

        cleaned_lines = []
        for line in lines:
            try:
                cleaned_line = line.encode("latin1").decode("utf-8")
            except:
                cleaned_line = line.encode("latin1", errors="ignore").decode("utf-8", errors="ignore")
            cleaned_lines.append(cleaned_line)

        with open(filename, "w", encoding="utf-8") as f:
            f.writelines(cleaned_lines)

        print("✓ File cleaned and saved.")


class FilmEntry:
    def __init__(self, title, rank_idx=None, date_watched=None, runtime=None, imdb_id=None,
                 directors=None, actors=None, genres=None, composer=None, year=None,
                 views=1, rated="", language="", all_dates=None):
        self.title = title
        self.rank_idx = rank_idx
        self.date_watched = date_watched
        self.runtime = runtime
        self.imdb_id = imdb_id
        self.directors = directors or []   # list of director name strings
        self.actors = actors or []         # list of actor name strings
        self.genres = genres or []
        self.composer = composer
        self.year = year
        self.views = views
        self.rated = rated
        self.language = language
        self.all_dates = all_dates or ([] if not date_watched else [date_watched])


class FilmManager:
    def __init__(self):
        self.api_key = TMDB_API_KEY
        self.films = []
        self.sections = {}
        self.load_data()

    def load_data(self):
        if not Path(DATA_FILE).exists():
            self._initialize_file()
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        self._parse_file(lines)

    def _initialize_file(self):
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            sections = [
                "    CHRONILOGICAL LIST", "    RANKED LIST OF FILMS SEEN", "    DATES",
                "    RUN TIMES", "    DIRECTORS", "    ACTORS", "    GENRES",
                "    COMPOSERS", "    YEAR", "    VIEW COUNT"
            ]
            for section in sections:
                f.write(section + "\n\n")
        pause_print(f"Created '{DATA_FILE}' with empty structure.")

    def _parse_file(self, lines):
        # Support both the old FILM CAST section and the new DIRECTORS/ACTORS split.
        # On first load of an old file, FILM CAST will be present; we split it automatically.
        # On subsequent saves the file will have DIRECTORS and ACTORS instead.
        keys = [
            "CHRONILOGICAL LIST", "RANKED LIST OF FILMS SEEN", "DATES", "RUN TIMES",
            "DIRECTORS", "ACTORS", "FILM CAST",   # FILM CAST kept for migration
            "GENRES", "COMPOSERS", "YEAR", "VIEW COUNT"
        ]
        indexes = {k: i for i, line in enumerate(lines) for k in keys if k in line}
        self.sections = {k: [] for k in keys}

        for key in keys:
            if key not in indexes:
                self.sections[key] = []
                continue
            start = indexes[key] + 1
            end = next(
                (i for i in range(start, len(lines)) if lines[i].strip() == "" and i > start),
                len(lines)
            )
            self.sections[key] = [l.strip() for l in lines[start:end] if l.strip()]

        titles     = self.sections["CHRONILOGICAL LIST"]
        ranked     = self.sections["RANKED LIST OF FILMS SEEN"]
        dates      = self.sections["DATES"]
        runtimes   = self.sections["RUN TIMES"]
        genres     = self.sections["GENRES"]
        composers  = self.sections["COMPOSERS"]
        years      = self.sections["YEAR"]
        views      = self.sections["VIEW COUNT"]

        # ── Decide whether to use the new split sections or migrate from FILM CAST ──
        has_new_sections = bool(self.sections["DIRECTORS"] or self.sections["ACTORS"])
        has_old_section  = bool(self.sections["FILM CAST"])

        if has_new_sections:
            directors_section = self.sections["DIRECTORS"]
            actors_section    = self.sections["ACTORS"]
        elif has_old_section:
            # Migrate: split each old FILM CAST line into directors (first ≤2) and actors (rest)
            pause_print("Migrating FILM CAST → DIRECTORS + ACTORS sections…")
            directors_section = []
            actors_section    = []
            for line in self.sections["FILM CAST"]:
                parts = line.split(",")
                # Directors: up to 2, dropping trailing N/A entries
                raw_dirs = parts[:2]
                dirs = [d.strip() for d in raw_dirs if d.strip() and d.strip().upper() != "N/A"]
                acts = [a.strip() for a in parts[2:] if a.strip()]
                directors_section.append(",".join(dirs) if dirs else "N/A")
                actors_section.append(",".join(acts) if acts else "N/A")
            # Update sections so save_data writes the new format immediately
            self.sections["DIRECTORS"] = directors_section
            self.sections["ACTORS"]    = actors_section
        else:
            directors_section = []
            actors_section    = []

        for i, title in enumerate(titles):
            raw_dates_line = dates[i] if i < len(dates) else ""
            dates_list = [d for d in raw_dates_line.split(",") if d] if raw_dates_line else []
            original_date = dates_list[0] if dates_list else (raw_dates_line or "")

            try:
                view_count = int(views[i]) if i < len(views) else 1
            except Exception:
                view_count = 1

            if original_date and len(dates_list) < view_count:
                dates_list = [original_date] + [original_date] * (view_count - 1)
            if len(dates_list) > view_count:
                view_count = len(dates_list)

            # Parse directors
            raw_dirs = directors_section[i] if i < len(directors_section) else ""
            film_directors = [d.strip() for d in raw_dirs.split(",") if d.strip() and d.strip().upper() != "N/A"]

            # Parse actors
            raw_acts = actors_section[i] if i < len(actors_section) else ""
            film_actors = [a.strip() for a in raw_acts.split(",") if a.strip() and a.strip().upper() != "N/A"]

            f = FilmEntry(
                title=title,
                rank_idx=ranked.index(title) if title in ranked else len(ranked),
                date_watched=original_date,
                runtime=runtimes[i] if i < len(runtimes) else "",
                directors=film_directors,
                actors=film_actors,
                genres=genres[i].split(",") if i < len(genres) else [],
                composer=composers[i] if i < len(composers) else "",
                year=years[i] if i < len(years) else "",
                views=view_count if view_count > 0 else 1,
                all_dates=dates_list if dates_list else ([original_date] if original_date else []),
            )
            self.films.append(f)

        # Keep sections["DATES"] as original dates only (for graphs/compatibility)
        self.sections["DATES"] = [film.date_watched for film in self.films]

    def save_data(self):
        def section(title, items):
            return f"    {title}\n" + "\n".join(str(i) for i in items) + "\n\n"

        with open(DATA_FILE, "w", encoding="utf-8") as f:
            f.write(section("CHRONILOGICAL LIST",
                            [film.title for film in self.films]))
            f.write(section("RANKED LIST OF FILMS SEEN",
                            [fl.title for fl in sorted(self.films, key=lambda x: x.rank_idx)]))
            f.write(section("DATES",
                            [",".join(film.all_dates) if film.all_dates else film.date_watched
                             for film in self.films]))
            f.write(section("RUN TIMES",
                            [film.runtime for film in self.films]))
            f.write(section("DIRECTORS",
                            [",".join(film.directors) if film.directors else "N/A"
                             for film in self.films]))
            f.write(section("ACTORS",
                            [",".join(film.actors) if film.actors else "N/A"
                             for film in self.films]))
            f.write(section("GENRES",
                            [",".join(film.genres) for film in self.films]))
            f.write(section("COMPOSERS",
                            [film.composer for film in self.films]))
            f.write(section("YEAR",
                            [film.year for film in self.films]))
            f.write(section("VIEW COUNT",
                            [film.views for film in self.films]))

    # ──────────────────────────────────────────────────────────
    # TMDB
    # ──────────────────────────────────────────────────────────

    def fetch_tmdb_data(self, title):
        search_url = f"{TMDB_BASE_URL}/search/movie"
        params = {"api_key": self.api_key, "query": title}
        try:
            response = requests.get(search_url, params=params)
            results = response.json().get("results", [])
            return results[0] if results else None
        except:
            return None

    def fetch_tmdb_details(self, tmdb_id):
        movie_url   = f"{TMDB_BASE_URL}/movie/{tmdb_id}"
        credits_url = f"{TMDB_BASE_URL}/movie/{tmdb_id}/credits"
        params = {"api_key": self.api_key}
        try:
            movie_res   = requests.get(movie_url,   params=params).json()
            credits_res = requests.get(credits_url, params=params).json()

            directors = [c["name"] for c in credits_res.get("crew", []) if c["job"] == "Director"]
            actors    = [a["name"] for a in credits_res.get("cast", [])]

            return {
                "title":     movie_res.get("title", ""),
                "year":      movie_res.get("release_date", "")[:4],
                "runtime":   str(movie_res.get("runtime", 0)),
                "language":  movie_res.get("original_language", "").upper(),
                "genres":    [g["name"] for g in movie_res.get("genres", [])],
                "directors": directors,
                "actors":    actors,
                "tmdb_id":   movie_res.get("id"),
            }
        except:
            return None

    # ──────────────────────────────────────────────────────────
    # ADD FILM
    # ──────────────────────────────────────────────────────────

    def add_film(self):
        title = safe_input("Enter film title: ")

        def fetch_and_confirm(title, year_hint=None):
            params = {"api_key": self.api_key, "query": title}
            if year_hint:
                params["year"] = year_hint
            search_url = f"{TMDB_BASE_URL}/search/movie"
            try:
                res     = requests.get(search_url, params=params)
                results = res.json().get("results", [])
                if not results:
                    return None
                details = self.fetch_tmdb_details(results[0]["id"])
                if not details:
                    return None

                details["title"]     = clean_text(details["title"])
                details["year"]      = clean_text(details["year"])
                details["runtime"]   = clean_text(details["runtime"])
                details["language"]  = clean_text(details["language"])
                details["genres"]    = [clean_text(g) for g in details["genres"]]
                details["directors"] = [clean_text(d) for d in details["directors"]]
                details["actors"]    = [clean_text(a) for a in details["actors"]]

                pause_print(f"\nFound: {details['title']} ({details['year']})")
                dir_str = ", ".join(details["directors"]) if details["directors"] else "N/A"
                pause_print(f"Director(s): {dir_str}")
                pause_print(f"Cast: {', '.join(details['actors'])[:100]}...")

                confirm = safe_input("Use this film? (Y/N): ").lower()
                return details if confirm == "y" else None
            except Exception as e:
                pause_print(f"Error fetching details: {e}")
                return None

        details = fetch_and_confirm(title)
        if not details:
            year_hint = safe_input("Enter the correct release year to refine search: ").strip()
            details = fetch_and_confirm(title, year_hint=year_hint)
        if not details:
            pause_print("✗ Could not find film. Aborting.")
            return

        # Duplicate check
        title_clean = details["title"].strip().lower()
        year_clean  = details["year"].strip()
        for film in self.films:
            if film.title.strip().lower() == title_clean and film.year.strip() == year_clean:
                film.views += 1
                film.all_dates.append(date.today().strftime("%d/%m/%Y"))
                pause_print(f"✓ You've already seen this film. View count updated to {film.views}.")
                pause_print("Rerank this film based on your rewatch.")
                self._rerank_existing_film(film)
                return

        new_film = FilmEntry(
            title=details["title"],
            date_watched=date.today().strftime("%d/%m/%Y"),
            runtime=details["runtime"],
            imdb_id=f"tmdb:{details['tmdb_id']}",
            directors=details["directors"],
            actors=details["actors"],
            genres=details["genres"],
            composer="N/A",
            year=details["year"],
            rated="N/A",
            language=details["language"],
            all_dates=[date.today().strftime("%d/%m/%Y")],
        )

        self._insert_ranked(new_film)
        self.films.append(new_film)
        self.sections["CHRONILOGICAL LIST"].append(new_film.title)
        self.sections["DATES"].append(new_film.date_watched)
        pause_print(f"✓ Added: {new_film.title}")

    # ──────────────────────────────────────────────────────────
    # RANKING HELPERS
    # ──────────────────────────────────────────────────────────

    def _get_rank_probe_index(self, low, high, question_number):
        window_size = high - low
        if window_size <= 0:
            return low
        mid = (low + high) // 2
        offset_steps = [10, 8, 6, 4, 3, 2, 1]
        max_offset = offset_steps[min(question_number, len(offset_steps) - 1)]
        min_offset = max(low - mid, -max_offset)
        max_offset = min((high - 1) - mid, max_offset)
        if min_offset == 0 and max_offset == 0:
            return mid
        possible_offsets = [o for o in range(min_offset, max_offset + 1) if o != 0]
        if not possible_offsets:
            possible_offsets = [0]
        return mid + random.choice(possible_offsets)

    def _find_rank_position(self, subject_title, ranked_films):
        low, high = 0, len(ranked_films)
        question_number = 0
        while low < high:
            probe_idx = self._get_rank_probe_index(low, high, question_number)
            comparison_film = ranked_films[probe_idx]
            while True:
                ans = safe_input(
                    f"Is '{subject_title}' better than '{comparison_film.title}'? (Y/N): "
                ).upper()
                if ans in ["Y", "N"]:
                    break
                print("Please respond with Y or N.")
            if ans == "Y":
                high = probe_idx
            else:
                low = probe_idx + 1
            question_number += 1
        return low

    def _insert_ranked(self, new_film):
        ranked = sorted(self.films, key=lambda f: f.rank_idx)
        insert_at = self._find_rank_position(new_film.title, ranked)
        new_film.rank_idx = insert_at
        for i, film in enumerate(ranked):
            if i >= insert_at:
                film.rank_idx += 1

    def _rerank_existing_film(self, target_film):
        ranked = sorted(self.films, key=lambda f: f.rank_idx)
        ranked_without = [f for f in ranked if f is not target_film]
        if not ranked_without:
            target_film.rank_idx = 0
            return
        insert_at = self._find_rank_position(target_film.title, ranked_without)
        ranked_without.insert(insert_at, target_film)
        for idx, film in enumerate(ranked_without):
            film.rank_idx = idx

    # ──────────────────────────────────────────────────────────
    # DISPLAY HELPERS
    # ──────────────────────────────────────────────────────────

    def _format_name_list(self, names, limit=8):
        cleaned = [n.strip() for n in names if n and n.strip() and n.strip().upper() != "N/A"]
        return ", ".join(cleaned[:limit]) if cleaned else "N/A"

    def _choose_film_from_matches(self, matches):
        if not matches:
            return None
        if len(matches) == 1:
            return matches[0]
        pause_print("\nMultiple films found:\n")
        for i, film in enumerate(matches, start=1):
            year = f" ({film.year})" if str(film.year).strip() else ""
            pause_print(f"{i}. {film.title}{year}")
        while True:
            choice = safe_input("Choose a film by number: ")
            if choice.isdigit():
                idx = int(choice)
                if 1 <= idx <= len(matches):
                    return matches[idx - 1]
            pause_print("Invalid choice.")

    def show_film_details(self, film):
        chrono_position = next((i + 1 for i, f in enumerate(self.films) if f is film), "N/A")
        ranked_sorted   = sorted(self.films, key=lambda x: x.rank_idx)
        rank_position   = next((i + 1 for i, f in enumerate(ranked_sorted) if f is film), "N/A")

        all_dates = getattr(film, "all_dates", None) or ([] if not film.date_watched else [film.date_watched])
        all_dates = [d.strip() for d in all_dates if isinstance(d, str) and d.strip()]
        first_seen = all_dates[0]  if all_dates else (film.date_watched or "N/A")
        last_seen  = all_dates[-1] if all_dates else (film.date_watched or "N/A")
        rewatches  = all_dates[1:] if len(all_dates) > 1 else []

        directors  = self._format_name_list(film.directors, limit=5)
        cast_str   = self._format_name_list(film.actors,    limit=10)
        genres     = ", ".join([g for g in film.genres if g and g.strip()]) if film.genres else "N/A"
        runtime    = film.runtime  if str(film.runtime).strip()  else "N/A"
        year       = film.year     if str(film.year).strip()     else "N/A"
        language   = film.language if str(film.language).strip() else "N/A"
        composer   = film.composer if str(film.composer).strip() else "N/A"
        views      = film.views    if str(film.views).strip()    else len(all_dates)

        pause_print("\n" + "=" * 70)
        pause_print(film.title)
        pause_print("=" * 70)
        pause_print(f"Year:                 {year}")
        pause_print(f"Ranked position:      {rank_position}")
        pause_print(f"Chronological entry:  {chrono_position}")
        pause_print(f"Runtime:              {runtime} mins")
        pause_print(f"Language:             {language}")
        pause_print(f"Genres:               {genres}")
        pause_print(f"Composer:             {composer}")
        pause_print(f"Times watched:        {views}")
        pause_print(f"First watched:        {first_seen}")
        pause_print(f"Last watched:         {last_seen}")
        pause_print(f"All watch dates:      {', '.join(all_dates) if all_dates else 'N/A'}")
        pause_print(f"Rewatches:            {', '.join(rewatches) if rewatches else 'None'}")
        pause_print(f"Director(s):          {directors}")
        pause_print(f"Cast:                 {cast_str}")
        pause_print("=" * 70)

    # ──────────────────────────────────────────────────────────
    # SEARCH / FILTER
    # ──────────────────────────────────────────────────────────

    def search_film_by_title(self):
        query = safe_input("Enter a film title or keyword: ").strip().lower()
        if not query:
            pause_print("No search entered.")
            return
        query_words = [w for w in re.findall(r"[a-z0-9']+", query) if w]

        def matches_query(title):
            tl = title.lower()
            return all(w in tl for w in query_words) if query_words else query in tl

        matches = [film for film in self.films if matches_query(film.title)]
        matches.sort(key=lambda f: (f.rank_idx, f.title.lower()))
        if not matches:
            pause_print("No films found.")
            return
        chosen = self._choose_film_from_matches(matches)
        if chosen:
            self.show_film_details(chosen)

    def search_by_name(self):
        name = safe_input("Enter actor, director or composer name: ").strip()
        if not name:
            pause_print("No name entered.")
            return
        query = name.lower()
        found = []
        for film in self.films:
            roles = []
            if any(query in d.lower() for d in film.directors if d.strip()):
                roles.append("D")
            if any(query in a.lower() for a in film.actors if a.strip()):
                roles.append("A")
            comp = (film.composer or "").strip()
            if comp and comp.upper() not in {"N/A", "NO COMPOSERS"} and query in comp.lower():
                roles.append("C")
            if roles:
                found.append((film, "/".join(roles), film.date_watched))

        if not found:
            pause_print("No films found.")
            return

        chrono = found
        ranked = sorted(found, key=lambda x: x[0].rank_idx)
        row_fmt = "{:<4} {:<35} {:<6} {:<15} | {}"
        pause_print("\n" + row_fmt.format("#", "Chronological Title", "Role", "Watched Date", "Ranked Title"))
        pause_print("-" * 98)
        for i in range(max(len(chrono), len(ranked))):
            ct = chrono[i][0].title if i < len(chrono) else ""
            cr = chrono[i][1]       if i < len(chrono) else ""
            cd = chrono[i][2]       if i < len(chrono) else ""
            rt = ranked[i][0].title if i < len(ranked)  else ""
            pause_print(row_fmt.format(i + 1, ct[:35], cr, cd, rt[:35]))

    def filter_by_year(self):
        try:
            year = safe_input("Enter a release year (e.g. 1999): ")
            if not year.isdigit():
                pause_print("Invalid year.")
                return
            matching = [f for f in self.films if f.year == year]
            if not matching:
                pause_print(f"No films found from {year}.")
                return
            chrono = sorted(matching, key=lambda x: x.date_watched or "")
            ranked = sorted(matching, key=lambda x: x.rank_idx)
            pause_print("\n{:<4} {:<35} {:<15} {}".format("#", "Chronological Title", "Watched Date", "Ranked Title"))
            pause_print("-" * 85)
            for i in range(max(len(chrono), len(ranked))):
                ct = chrono[i].title        if i < len(chrono) else ""
                cd = chrono[i].date_watched if i < len(chrono) else ""
                rt = ranked[i].title        if i < len(ranked)  else ""
                pause_print("{:<4} {:<35} {:<15} {}".format(i + 1, ct[:35], cd, rt[:35]))
        except Exception as e:
            pause_print(f"Error: {e}")

    # ──────────────────────────────────────────────────────────
    # TOP CREATIVES
    # ──────────────────────────────────────────────────────────

    def top_creatives(self):
        directors_list = []
        actors_list    = []
        for film in self.films:
            directors_list.extend([d for d in film.directors if d.strip()])
            actors_list.extend([a for a in film.actors if a.strip()])

        top_directors = Counter(directors_list).most_common(30)
        top_actors    = Counter(actors_list).most_common(30)

        pause_print("\nTop 30 directors and actors\n")
        pause_print("{:<4} {:<24} {:<4} {:<24} {:<4}".format("#", "Director", "#", "Actor", "#"))
        pause_print("-" * 68)
        for i in range(max(len(top_directors), len(top_actors))):
            d_index = i + 1               if i < len(top_directors) else ""
            d_name  = top_directors[i][0] if i < len(top_directors) else ""
            d_count = top_directors[i][1] if i < len(top_directors) else ""
            a_name  = top_actors[i][0]    if i < len(top_actors)    else ""
            a_count = top_actors[i][1]    if i < len(top_actors)    else ""
            pause_print("{:<4} {:<24} {:<4} {:<24} {:<4}".format(
                d_index, d_name[:24], d_count, a_name[:24], a_count))
            time.sleep(0.01)

    def top_composers(self):
        composers = []
        for film in self.films:
            c = (film.composer or "").strip()
            if c and c.upper() not in {"N/A", "NO COMPOSERS"}:
                composers.append(c)
        top = Counter(composers).most_common(30)
        pause_print("\nTop 30 composers\n")
        pause_print("{:<4} {:<30} {:<4}".format("#", "Composer", "#"))
        pause_print("-" * 42)
        for i, (name, count) in enumerate(top, start=1):
            pause_print("{:<4} {:<30} {:<4}".format(i, name[:30], count))
            time.sleep(0.01)

    def favorite_creatives_by_rank(self):
        from collections import defaultdict
        actor_scores    = defaultdict(list)
        director_scores = defaultdict(list)
        total_films     = len(self.films)
        ranked_films    = sorted(self.films, key=lambda x: x.rank_idx)

        for i, film in enumerate(ranked_films):
            score = total_films - i
            for d in film.directors:
                if d and d.strip():
                    director_scores[d].append(score)
            for a in film.actors:
                if a and a.strip():
                    actor_scores[a].append(score)

        qualified_directors = {k: sum(v)/len(v) for k, v in director_scores.items() if len(v) >= 4}
        qualified_actors    = {k: sum(v)/len(v) for k, v in actor_scores.items()    if len(v) >= 8}

        top_directors = sorted(qualified_directors.items(), key=lambda x: x[1], reverse=True)[:20]
        top_actors    = sorted(qualified_actors.items(),    key=lambda x: x[1], reverse=True)[:20]

        print("\n Top 20 Creatives (Based on Average Film Ranking Score)\n")
        print("{:<4} {:<25} {:<8} {:<25} {:<8}".format("#", "Director", "Score", "Actor", "Score"))
        print("-" * 75)
        for i in range(20):
            d_name  = top_directors[i][0]          if i < len(top_directors) else ""
            d_score = f"{top_directors[i][1]:.2f}" if i < len(top_directors) else ""
            a_name  = top_actors[i][0]             if i < len(top_actors)    else ""
            a_score = f"{top_actors[i][1]:.2f}"    if i < len(top_actors)    else ""
            print("{:<4} {:<25} {:<8} {:<25} {:<8}".format(i+1, d_name[:25], d_score, a_name[:25], a_score))

    # ──────────────────────────────────────────────────────────
    # STATS
    # ──────────────────────────────────────────────────────────

    def show_rewatched_films(self):
        rewatched = [(f.views, f.title) for f in self.films if isinstance(f.views, int) and f.views > 1]
        if not rewatched:
            pause_print("\nNo films with view count > 1 found.")
            return
        rewatched.sort(key=lambda x: (-x[0], x[1].lower()))
        pause_print("\nFilms watched more than once (sorted by views):\n")
        pause_print("{:<4} {:<6} {}".format("#", "Views", "Title"))
        pause_print("-" * 60)
        for i, (views, title) in enumerate(rewatched, start=1):
            pause_print("{:<4} {:<6} {}".format(i, views, title))

    def show_total_runtime(self):
        total_minutes = 0
        missing = 0
        for film in self.films:
            try:
                minutes = int(film.runtime) if str(film.runtime).strip().isdigit() else 0
                views   = int(film.views) if isinstance(film.views, int) or str(film.views).isdigit() else 1
                if minutes <= 0:
                    missing += 1
                    continue
                total_minutes += minutes * max(views, 1)
            except Exception:
                missing += 1
        total_hours = total_minutes / 60.0
        total_days  = total_hours  / 24.0
        total_weeks = total_days   / 7.0
        total_years = total_days   / 365.25
        pause_print("\nTotal watch time (includes re-watches):\n")
        pause_print(f"Minutes: {total_minutes:,}")
        pause_print(f"Hours:   {total_hours:,.2f}")
        pause_print(f"Days:    {total_days:,.2f}")
        pause_print(f"Weeks:   {total_weeks:,.2f}")
        pause_print(f"Years:   {total_years:,.2f}")
        if missing:
            pause_print(f"\nNote: {missing} film(s) had missing/invalid runtime and were skipped.")

    def show_rewatch_habits(self):
        from collections import Counter
        first_watch_dates = []
        all_watch_dates   = []
        rewatch_dates     = []

        for film in self.films:
            dates = getattr(film, "all_dates", None) or ([] if not film.date_watched else [film.date_watched])
            dates = [d for d in dates if isinstance(d, str) and d.strip()]
            if not dates and film.date_watched:
                dates = [film.date_watched]
            if dates:
                first_watch_dates.append(dates[0])
                if len(dates) > 1:
                    rewatch_dates.extend(dates[1:])
                all_watch_dates.extend(dates)

        def parse_date(s):
            return datetime.strptime(s, "%d/%m/%Y").date()

        try:
            all_days     = [parse_date(d) for d in all_watch_dates]
            rewatch_days = [parse_date(d) for d in rewatch_dates]
        except Exception as e:
            pause_print(f"Date parse error: {e}")
            return

        total_watches   = len(all_days)
        total_rewatches = len(rewatch_days)
        rewatch_rate    = (total_rewatches / total_watches * 100.0) if total_watches else 0.0

        rewatch_month = Counter([d.strftime("%Y-%m") for d in rewatch_days])
        rewatch_year  = Counter([d.strftime("%Y")    for d in rewatch_days])

        unique_days = sorted(set(all_days))
        longest = current = 0
        prev = None
        for d in unique_days:
            if prev is None or (d - prev).days == 1:
                current += 1
            else:
                longest = max(longest, current)
                current = 1
            prev = d
        longest = max(longest, current) if unique_days else 0

        pause_print("\nRewatch habits over time\n")
        pause_print(f"Total watches (incl. rewatches): {total_watches:,}")
        pause_print(f"Total rewatches:               {total_rewatches:,}")
        pause_print(f"Rewatch rate:                  {rewatch_rate:.2f}%")
        pause_print(f"Longest watch streak:          {longest} day(s)")
        if total_rewatches == 0:
            pause_print("\nNo rewatches recorded yet.")
            return
        pause_print("\nTop rewatch months:")
        for i, (m, c) in enumerate(rewatch_month.most_common(12), start=1):
            pause_print(f"{i:>2}. {m}  —  {c}")
        pause_print("\nRewatches by year:")
        for y in sorted(rewatch_year.keys()):
            pause_print(f"{y}: {rewatch_year[y]}")

    def rewatch_suggestions(self):
        ranked_films = sorted(self.films, key=lambda x: x.rank_idx)
        if not ranked_films:
            pause_print("\nNo films found.")
            return
        cutoff = max(1, int(len(ranked_films) * 0.35))
        candidates = ranked_films[:cutoff]
        today = datetime.today().date()
        scored = []
        for rank_pos, film in enumerate(candidates, start=1):
            dates = getattr(film, "all_dates", None) or ([] if not film.date_watched else [film.date_watched])
            valid_dates = []
            for d in dates:
                if not isinstance(d, str) or not d.strip():
                    continue
                try:
                    valid_dates.append(datetime.strptime(d.strip(), "%d/%m/%Y").date())
                except Exception:
                    continue
            if valid_dates:
                last_seen = max(valid_dates)
                last_seen_str = last_seen.strftime("%d/%m/%Y")
                days_since = (today - last_seen).days
            else:
                last_seen_str = "Unknown"
                days_since = 0
            rank_strength  = (cutoff - rank_pos + 1) / cutoff
            recency_strength = max(days_since, 0) / 365.25
            base_score = (rank_strength * 0.55) + (recency_strength * 0.45)
            weight = max(base_score, 0.01) * random.uniform(0.88, 1.12)
            scored.append({"film": film, "last_seen": last_seen_str, "base_score": base_score, "weight": weight})

        shortlist_size = min(10, len(scored))
        pool = scored[:]
        shortlist = []
        while pool and len(shortlist) < shortlist_size:
            total_w = sum(item["weight"] for item in pool)
            pick = random.uniform(0, total_w) if total_w > 0 else 0
            running = 0
            chosen_index = 0
            for idx, item in enumerate(pool):
                running += item["weight"]
                if running >= pick:
                    chosen_index = idx
                    break
            shortlist.append(pool.pop(chosen_index))

        shortlist.sort(key=lambda x: x["base_score"], reverse=True)
        suggestions = shortlist[:5]
        pause_print("\nRewatch suggestions\n")
        pause_print("{:<4} {:<35} {}".format("#", "Film", "Last Seen"))
        pause_print("-" * 60)
        for i, item in enumerate(suggestions, start=1):
            pause_print("{:<4} {:<35} {}".format(i, item["film"].title[:35], item["last_seen"]))

    # ──────────────────────────────────────────────────────────
    # GRAPHS
    # ──────────────────────────────────────────────────────────

    def show_graphs(self):
        if not HAS_MATPLOTLIB:
            print("Matplotlib is not available. Graph features are disabled.")
            return
        import matplotlib.pyplot as plt
        from collections import defaultdict, Counter
        import matplotlib.dates as mdates

        while True:
            print("\nGraph Options:\n")
            print("1. Cumulative films watched over time")
            print("2. Films watched by release year")
            print("3. Films watched per year since 2020")
            print("4. Genre popularity bar chart")
            print("5. Average runtime per year")
            print("6. Rank vs runtime scatter plot")
            print("7. Back to main menu\n")
            choice = safe_input("Enter your choice: ")

            if choice == "1":
                dates = [datetime.strptime(d, "%d/%m/%Y")
                         for d in self.sections.get("DATES", []) if d.strip()]
                dates.sort()
                counts = list(range(1, len(dates) + 1))
                all_watch_dates = []
                for film in self.films:
                    for d in getattr(film, "all_dates", []) or []:
                        if isinstance(d, str) and d.strip():
                            try:
                                all_watch_dates.append(datetime.strptime(d, "%d/%m/%Y"))
                            except:
                                pass
                all_watch_dates.sort()
                all_counts = list(range(1, len(all_watch_dates) + 1))
                plt.figure()
                plt.plot(dates, counts)
                if all_watch_dates:
                    plt.plot(all_watch_dates, all_counts)
                plt.title("Cumulative Films Watched Over Time")
                plt.xlabel("Date"); plt.ylabel("Total Films Watched")
                plt.legend(["First watches", "All watches"], loc="best")
                plt.grid(True)
                plt.gca().xaxis.set_major_locator(mdates.YearLocator())
                plt.gca().xaxis.set_major_formatter(mdates.DateFormatter('%Y'))
                plt.xticks(rotation=45); plt.tight_layout(); plt.show()

            elif choice == "2":
                years = [int(y) for y in self.sections.get("YEAR", []) if y.isdigit()]
                year_counts = Counter(years)
                sorted_years = sorted(year_counts)
                counts = [year_counts[y] for y in sorted_years]
                labels = [str(y)[-2:] for y in sorted_years]
                plt.figure()
                plt.bar(labels, counts)
                plt.title("Film Count by Release Year")
                plt.xlabel("Release Year (Last 2 Digits)"); plt.ylabel("Number of Films")
                plt.tight_layout(); plt.show()

            elif choice == "3":
                years = [d.split("/")[-1] for d in self.sections.get("DATES", []) if d.strip()]
                year_counts = Counter(years)
                sorted_years = sorted(year_counts)
                counts = [year_counts[y] for y in sorted_years]
                plt.figure()
                plt.bar(sorted_years, counts)
                plt.title("Films Watched Per Year")
                plt.xlabel("Year"); plt.ylabel("Number of Films Watched")
                plt.tight_layout(); plt.show()

            elif choice == "4":
                all_genres = [g for f in self.films for g in f.genres if g]
                genre_counts = Counter(all_genres)
                genres, counts = zip(*genre_counts.most_common(15))
                plt.figure()
                plt.barh(genres[::-1], counts[::-1])
                plt.title("Top 15 Most Common Genres"); plt.xlabel("Number of Films")
                plt.tight_layout(); plt.show()

            elif choice == "5":
                year_runtime = defaultdict(list)
                for film in self.films:
                    if film.year.isdigit() and film.runtime.isdigit():
                        year_runtime[int(film.year)].append(int(film.runtime))
                years = sorted(year_runtime.keys())
                averages = [sum(r)/len(r) for r in [year_runtime[y] for y in years]]
                plt.figure()
                plt.plot(years, averages, marker="o")
                plt.title("Average Runtime Per Year")
                plt.xlabel("Release Year"); plt.ylabel("Average Runtime (minutes)")
                plt.grid(True); plt.tight_layout(); plt.show()

            elif choice == "6":
                ranked = sorted(self.films, key=lambda x: x.rank_idx)
                runtimes = [int(f.runtime) if f.runtime.isdigit() else 0 for f in ranked]
                indices  = list(range(1, len(ranked) + 1))
                plt.figure()
                plt.scatter(indices, runtimes)
                plt.title("Rank vs Runtime")
                plt.xlabel("Film Rank (1 = Best)"); plt.ylabel("Runtime (minutes)")
                plt.grid(True); plt.tight_layout(); plt.show()

            elif choice == "7":
                break
            else:
                print("Invalid choice.")

    # ──────────────────────────────────────────────────────────
    # VIEW LISTS
    # ──────────────────────────────────────────────────────────

    def view_lists(self):
        from time import sleep

        def print_table(header, rows):
            print(f"\n{header}")
            print("-" * 50)
            for i, (title, watched_date) in enumerate(rows, start=1):
                print(f"{i:<3} {title[:35]:<35} {watched_date}")
                sleep(0.01)

        if is_android():
            print("\nYou're on Android. Choose what to view:")
            print("1. Chronological List")
            print("2. Ranked List")
            choice = input("Enter choice (1 or 2): ").strip()
            if choice == "1":
                rows = list(zip(self.sections.get("CHRONILOGICAL LIST", []),
                                self.sections.get("DATES", [])))
                print_table("Chronological List", rows)
            elif choice == "2":
                ranked_sorted = sorted(self.films, key=lambda x: x.rank_idx)
                rows = [(f.title, f.date_watched) for f in ranked_sorted]
                print_table("Ranked List", rows)
            else:
                print("Invalid choice.")
        else:
            print("\n{:<4} {:<35} {:<15} {}".format("#", "Chronological Title", "Watched Date", "Ranked Title"))
            print("-" * 85)
            chrono_titles  = self.sections.get("CHRONILOGICAL LIST", [])
            chrono_dates   = self.sections.get("DATES", [])
            ranked_titles  = [f.title for f in sorted(self.films, key=lambda x: x.rank_idx)]
            for i in range(max(len(chrono_titles), len(ranked_titles))):
                ct = chrono_titles[i] if i < len(chrono_titles) else ""
                cd = chrono_dates[i]  if i < len(chrono_dates)  else ""
                rt = ranked_titles[i] if i < len(ranked_titles)  else ""
                print("{:<4} {:<35} {:<15} {}".format(i + 1, ct[:35], cd, rt[:35]))
                sleep(0.01)

    def secret_view_last_20(self):
        pause_print("\n{:<4} {:<35} {:<15} {}".format("#", "Chronological Title", "Watched Date", "Ranked Title"))
        pause_print("-" * 85)
        chrono_titles = self.sections.get("CHRONILOGICAL LIST", [])[-20:]
        chrono_dates  = self.sections.get("DATES", [])[-20:]
        ranked_titles = [f.title for f in sorted(self.films, key=lambda x: x.rank_idx)][-20:]
        for i in range(max(len(chrono_titles), len(ranked_titles))):
            ct = chrono_titles[i] if i < len(chrono_titles) else ""
            cd = chrono_dates[i]  if i < len(chrono_dates)  else ""
            rt = ranked_titles[i] if i < len(ranked_titles)  else ""
            pause_print("{:<4} {:<35} {:<15} {}".format(i + 1, ct[:35], cd, rt[:35]))

    def secret_view_last_20_with_rewatches(self):
        all_watches = []
        for film_idx, film in enumerate(self.films):
            dates = getattr(film, "all_dates", None) or ([] if not film.date_watched else [film.date_watched])
            for watch_idx, watch_date in enumerate(dates):
                if not isinstance(watch_date, str) or not watch_date.strip():
                    continue
                try:
                    parsed = datetime.strptime(watch_date.strip(), "%d/%m/%Y")
                except Exception:
                    continue
                all_watches.append((parsed, film_idx, watch_idx, film.title, watch_date.strip()))
        all_watches.sort(key=lambda x: (x[0], x[1], x[2]))
        recent = all_watches[-20:]
        pause_print("\nRecent 20 watches (including rewatches)\n")
        pause_print("{:<4} {:<35} {}".format("#", "Chronological Title", "Watched Date"))
        pause_print("-" * 60)
        for i, (_, _, _, title, watched_date) in enumerate(recent, start=1):
            pause_print("{:<4} {:<35} {}".format(i, title[:35], watched_date))

    # ──────────────────────────────────────────────────────────
    # MISC
    # ──────────────────────────────────────────────────────────

    def backup(self):
        timestamp   = datetime.now().strftime("%Y_%m_%d")
        backup_file = f"2020 Vision {timestamp}.txt"
        with open(DATA_FILE, "r", encoding="utf-8") as original:
            with open(backup_file, "w", encoding="latin-1") as f:
                f.write(original.read())
        print(f"Backup saved to '{backup_file}'")

    def check_lengths(self):
        pause_print("\nList Lengths:\n")
        for key, values in self.sections.items():
            pause_print(f"{key:<30}: {len(values)}")


# ──────────────────────────────────────────────────────────────
# LETTERBOXD IMPORT
# ──────────────────────────────────────────────────────────────

def _fmt_date(raw):
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").strftime("%d/%m/%Y")
    except Exception:
        return ""


def parse_letterboxd_csv(ratings_path, diary_path=None):
    """Parse ratings.csv (all films) and optionally merge diary.csv for accurate watch dates."""
    import csv

    entries = []
    try:
        with open(ratings_path, "r", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                title = row.get("Name", "").strip()
                if not title:
                    continue
                date_raw = (row.get("Watched Date") or row.get("Date") or "").strip()
                rating_raw = row.get("Rating", "").strip()
                rating = 0
                if rating_raw:
                    try:
                        rating = int(float(rating_raw) * 2)
                    except ValueError:
                        pass
                entries.append({
                    "title":  title,
                    "year":   row.get("Year", "").strip(),
                    "date":   _fmt_date(date_raw),
                    "rating": rating,
                })
    except FileNotFoundError:
        print(f"File not found: {ratings_path}")
        return []
    except Exception as e:
        print(f"Error reading {ratings_path}: {e}")
        return []

    # Overlay real watch dates from diary.csv where available
    if diary_path and Path(diary_path).exists():
        diary_dates = {}
        try:
            with open(diary_path, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    title = row.get("Name", "").strip().lower()
                    watched = (row.get("Watched Date") or row.get("Date") or "").strip()
                    if title and watched:
                        diary_dates.setdefault(title, []).append(_fmt_date(watched))
        except Exception:
            pass
        for entry in entries:
            key = entry["title"].lower()
            if key in diary_dates:
                entry["date"] = diary_dates[key][0]

    return entries


def import_from_letterboxd(csv_path, username, diary_path=None, tmdb_limit=20):
    print(f"\nReading {csv_path}...")
    raw = parse_letterboxd_csv(csv_path, diary_path=diary_path)
    if not raw:
        print("No entries found in CSV.")
        return None

    print(f"Found {len(raw)} diary entries.")

    from collections import defaultdict

    def parse_date_safe(d):
        try:
            return datetime.strptime(d, "%d/%m/%Y")
        except Exception:
            return datetime.min

    grouped = defaultdict(lambda: {"title": "", "year": "", "dates": [], "rating": 0})
    for entry in raw:
        key = entry["title"].strip().lower()
        grouped[key]["title"] = entry["title"].strip()
        if entry["year"] and not grouped[key]["year"]:
            grouped[key]["year"] = entry["year"]
        if entry["date"]:
            grouped[key]["dates"].append(entry["date"])
        grouped[key]["rating"] = max(grouped[key]["rating"], entry["rating"])

    for key in grouped:
        grouped[key]["dates"].sort(key=parse_date_safe)

    films_list = list(grouped.values())
    films_list.sort(key=lambda x: parse_date_safe(x["dates"][0]) if x["dates"] else datetime.max)

    ranked_order = sorted(films_list, key=lambda x: (-x["rating"], x["title"].lower()))
    rank_map = {id(f): i for i, f in enumerate(ranked_order)}

    total = len(films_list)
    tmdb_count = min(tmdb_limit, total) if tmdb_limit else total
    print(f"\nLoading all {total} films — fetching TMDB details for first {tmdb_count}...")

    film_entries = []
    for i, film_data in enumerate(films_list, 1):
        title = film_data["title"]
        year_hint = film_data["year"]

        details = None
        if i <= tmdb_count:
            sys.stdout.write(f"\r[{i}/{tmdb_count}] {title[:50]:<50}")
            sys.stdout.flush()
            try:
                params = {"api_key": TMDB_API_KEY, "query": title}
                if year_hint:
                    params["year"] = year_hint
                results = requests.get(f"{TMDB_BASE_URL}/search/movie", params=params, timeout=10).json().get("results", [])
                if not results and year_hint:
                    results = requests.get(f"{TMDB_BASE_URL}/search/movie", params={"api_key": TMDB_API_KEY, "query": title}, timeout=10).json().get("results", [])
                if results:
                    tmdb_id = results[0]["id"]
                    movie_res   = requests.get(f"{TMDB_BASE_URL}/movie/{tmdb_id}",         params={"api_key": TMDB_API_KEY}, timeout=10).json()
                    credits_res = requests.get(f"{TMDB_BASE_URL}/movie/{tmdb_id}/credits", params={"api_key": TMDB_API_KEY}, timeout=10).json()
                    details = {
                        "title":     movie_res.get("title", title),
                        "year":      movie_res.get("release_date", "")[:4],
                        "runtime":   str(movie_res.get("runtime", "")),
                        "genres":    [g["name"] for g in movie_res.get("genres", [])],
                        "directors": [c["name"] for c in credits_res.get("crew", []) if c["job"] == "Director"],
                        "actors":    [a["name"] for a in credits_res.get("cast", [])[:15]],
                    }
            except Exception:
                pass
            time.sleep(0.2)

        dates = film_data["dates"]
        entry = FilmEntry(
            title=details["title"] if details else title,
            rank_idx=rank_map[id(film_data)],
            date_watched=dates[0] if dates else "",
            runtime=details["runtime"] if details else "",
            directors=details["directors"] if details else [],
            actors=details["actors"] if details else [],
            genres=details["genres"] if details else [],
            composer="",
            year=details["year"] if details else year_hint,
            views=max(len(dates), 1),
            all_dates=dates,
        )
        film_entries.append(entry)

    print(f"\n\nWriting data file...")

    data_file = f"{username}_films.txt"
    chrono = film_entries
    ranked = sorted(film_entries, key=lambda f: f.rank_idx)

    def section(heading, items):
        return f"    {heading}\n" + "\n".join(str(x) for x in items) + "\n\n"

    with open(data_file, "w", encoding="utf-8") as f:
        f.write(section("CHRONILOGICAL LIST",        [e.title for e in chrono]))
        f.write(section("RANKED LIST OF FILMS SEEN", [e.title for e in ranked]))
        f.write(section("DATES",      [",".join(e.all_dates) if e.all_dates else e.date_watched for e in chrono]))
        f.write(section("RUN TIMES",  [e.runtime   for e in chrono]))
        f.write(section("DIRECTORS",  [",".join(e.directors) if e.directors else "N/A" for e in chrono]))
        f.write(section("ACTORS",     [",".join(e.actors)    if e.actors    else "N/A" for e in chrono]))
        f.write(section("GENRES",     [",".join(e.genres)    for e in chrono]))
        f.write(section("COMPOSERS",  [e.composer  for e in chrono]))
        f.write(section("YEAR",       [e.year      for e in chrono]))
        f.write(section("VIEW COUNT", [e.views     for e in chrono]))

    print(f"\nCreated '{data_file}' with {len(film_entries)} films.")
    return data_file


def setup_data_file():
    import glob as _glob

    if Path(DATA_FILE).exists():
        return DATA_FILE

    existing = _glob.glob("*_films.txt")
    if existing:
        if len(existing) == 1:
            pause_print(f"Found existing data file: {existing[0]}")
            return existing[0]
        pause_print("Multiple data files found:")
        for i, f in enumerate(existing, 1):
            pause_print(f"  {i}. {f}")
        choice = safe_input("Choose one (number): ")
        if choice.isdigit() and 1 <= int(choice) <= len(existing):
            return existing[int(choice) - 1]
        return existing[0]

    pause_print("\nNo data file found.")
    ans = safe_input("Import from Letterboxd? (Y/N): ").lower()
    if ans != "y":
        return DATA_FILE

    pause_print("\nTo export your Letterboxd data:")
    pause_print("  1. Go to letterboxd.com and sign in")
    pause_print("  2. Click your profile picture > Settings > Data")
    pause_print("  3. Click 'Export your data' and download the ZIP")
    pause_print("  4. Extract diary.csv from the ZIP into this folder\n")

    script_dir = Path(__file__).parent
    ratings_csv = script_dir / "ratings.csv"
    diary_csv   = script_dir / "diary.csv"

    if ratings_csv.exists():
        pause_print(f"Found: {ratings_csv}")
        csv_path = str(ratings_csv)
    elif diary_csv.exists():
        pause_print(f"Found: {diary_csv}")
        csv_path = str(diary_csv)
    else:
        csv_path = safe_input("Enter path to ratings.csv or diary.csv: ").strip().strip('"')

    if not csv_path or not Path(csv_path).exists():
        pause_print("CSV file not found. Starting with empty data file.")
        return DATA_FILE

    username = safe_input("Enter your Letterboxd username (used for the output filename): ").strip()
    if not username:
        username = "letterboxd"

    diary_path = str(diary_csv) if diary_csv.exists() else None
    result = import_from_letterboxd(csv_path, username, diary_path=diary_path)
    return result if result else DATA_FILE


# ──────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────

def main():
    global DATA_FILE
    DATA_FILE = setup_data_file()
    manager = FilmManager()

    menu_options = [
        ("1",  "Add watched film"),
        ("2",  "View your lists"),
        ("3",  "Save and quit"),
        ("4",  "Make backup"),
        ("5",  "Top 30 actors and directors"),
        ("6",  "Find films by actor, director or composer"),
        ("7",  "Graphs"),
        ("8",  "Check list lengths"),
        ("9",  "Find top 20 actor and director (based on rankings)"),
        ("10", "Filter films by release year"),
        ("11", "Show films watched more than once"),
        ("12", "Total runtime watched (incl. re-watches)"),
        ("13", "Rewatch habits over time"),
        ("14", "Search for a film"),
        ("15", "Rewatch suggestions"),
        ("16", "Top 30 composers"),
    ]
    number_width = max(len(number) for number, _ in menu_options)

    while True:
        pause_print("\n" + "=" * 34)
        pause_print("Main Menu")
        pause_print("=" * 34)
        for number, label in menu_options:
            pause_print(f"{number:>{number_width}}. {label}")
        pause_print(" ")

        choice = safe_input("Enter your choice: ")
        if   choice == "1":  manager.add_film()
        elif choice == "2":  manager.view_lists()
        elif choice == "3":  manager.save_data(); pause_print("Saved. Goodbye!"); break
        elif choice == "4":  manager.backup()
        elif choice == "5":  manager.top_creatives()
        elif choice == "6":  manager.search_by_name()
        elif choice == "7":  manager.show_graphs()
        elif choice == "8":  manager.check_lengths()
        elif choice == "9":  manager.favorite_creatives_by_rank()
        elif choice == "10": manager.filter_by_year()
        elif choice == "11": manager.show_rewatched_films()
        elif choice == "12": manager.show_total_runtime()
        elif choice == "13": manager.show_rewatch_habits()
        elif choice == "14": manager.search_film_by_title()
        elif choice == "15": manager.rewatch_suggestions()
        elif choice == "16": manager.top_composers()
        elif choice == "22":  manager.secret_view_last_20()
        elif choice == "222": manager.secret_view_last_20_with_rewatches()
        elif choice == "99":  clean_entire_file("2020 Vision.txt")
        else: pause_print("Invalid choice.")

if __name__ == "__main__":
    main()
