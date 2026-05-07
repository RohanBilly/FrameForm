"""
2020 Vision — Film Tracker Web App
Run with: streamlit run film_app.py
Requires: streamlit, pandas  (pip install streamlit pandas)
Place this file in the same folder as your "2020 Vision.txt" data file.
"""

import streamlit as st
import pandas as pd
from pathlib import Path
from collections import Counter
from datetime import datetime

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────
DATA_FILE = "2020 Vision.txt"

st.set_page_config(
    page_title="2020 Vision",
    page_icon="🎬",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────
# CUSTOM CSS  — cinematic dark theme
# ─────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Playfair+Display:ital,wght@0,700;1,400&family=DM+Sans:wght@300;400;500&display=swap');

html, body, [class*="css"] {
    font-family: 'DM Sans', sans-serif;
    background-color: #0d0d0f;
    color: #e8e4dc;
}

h1, h2, h3 { font-family: 'Playfair Display', serif; color: #f5c842; }

/* Sidebar */
section[data-testid="stSidebar"] {
    background: #13131a;
    border-right: 1px solid #2a2a35;
}

/* Metric cards */
[data-testid="stMetric"] {
    background: #1a1a24;
    border: 1px solid #2e2e40;
    border-radius: 8px;
    padding: 12px 16px;
}

/* Dataframe */
[data-testid="stDataFrame"] { border-radius: 8px; overflow: hidden; }

/* Inputs */
[data-testid="stTextInput"] input,
[data-testid="stSelectbox"] select {
    background: #1a1a24 !important;
    border: 1px solid #2e2e40 !important;
    color: #e8e4dc !important;
}

/* Tabs */
button[data-baseweb="tab"] {
    font-family: 'DM Sans', sans-serif;
    font-size: 13px;
    color: #888 !important;
}
button[data-baseweb="tab"][aria-selected="true"] {
    color: #f5c842 !important;
    border-bottom: 2px solid #f5c842 !important;
}

/* Divider */
hr { border-color: #2a2a35; }

.rank-badge {
    display: inline-block;
    background: #f5c842;
    color: #0d0d0f;
    font-weight: 700;
    font-size: 11px;
    padding: 2px 7px;
    border-radius: 4px;
    font-family: 'DM Sans', sans-serif;
}
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────
# DATA LOADING
# ─────────────────────────────────────────────
@st.cache_data
def load_data(filepath: str) -> pd.DataFrame:
    """Parse the flat-text data file into a DataFrame."""
    path = Path(filepath)
    if not path.exists():
        return pd.DataFrame()

    lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()

    KEYS = [
        "CHRONILOGICAL LIST", "RANKED LIST OF FILMS SEEN", "DATES",
        "RUN TIMES", "DIRECTORS", "ACTORS", "FILM CAST",
        "GENRES", "COMPOSERS", "YEAR", "VIEW COUNT",
    ]

    def extract_section(key):
        try:
            start = next(i for i, l in enumerate(lines) if key in l) + 1
        except StopIteration:
            return []
        result = []
        for l in lines[start:]:
            stripped = l.strip()
            if stripped == "" and result:
                break
            if stripped:
                result.append(stripped)
        return result

    sections = {k: extract_section(k) for k in KEYS}

    chrono    = sections["CHRONILOGICAL LIST"]
    ranked    = sections["RANKED LIST OF FILMS SEEN"]
    dates     = sections["DATES"]
    runtimes  = sections["RUN TIMES"]
    genres    = sections["GENRES"]
    composers = sections["COMPOSERS"]
    years     = sections["YEAR"]
    views     = sections["VIEW COUNT"]

    # Support both new split sections and legacy FILM CAST
    has_new = bool(sections["DIRECTORS"] or sections["ACTORS"])
    if has_new:
        directors_sec = sections["DIRECTORS"]
        actors_sec    = sections["ACTORS"]
    else:
        # Migrate on the fly from FILM CAST
        directors_sec, actors_sec = [], []
        for line in sections["FILM CAST"]:
            parts = line.split(",")
            dirs = [p.strip() for p in parts[:2] if p.strip() and p.strip().upper() != "N/A"]
            acts = [p.strip() for p in parts[2:] if p.strip()]
            directors_sec.append(",".join(dirs) if dirs else "N/A")
            actors_sec.append(",".join(acts) if acts else "N/A")

    rank_lookup = {title: idx for idx, title in enumerate(ranked)}

    rows = []
    for i, title in enumerate(chrono):
        raw_dates = dates[i] if i < len(dates) else ""
        date_list = [d for d in raw_dates.split(",") if d.strip()]
        first_date = date_list[0] if date_list else ""

        try:
            first_dt = datetime.strptime(first_date, "%d/%m/%Y") if first_date else None
        except ValueError:
            first_dt = None

        genre_list = [g.strip() for g in (genres[i].split(",") if i < len(genres) else []) if g.strip()]

        raw_dirs  = directors_sec[i] if i < len(directors_sec) else ""
        raw_acts  = actors_sec[i]    if i < len(actors_sec)    else ""
        dir_parts = [d.strip() for d in raw_dirs.split(",") if d.strip() and d.strip().upper() != "N/A"]
        act_parts = [a.strip() for a in raw_acts.split(",") if a.strip() and a.strip().upper() != "N/A"]
        director  = ", ".join(dir_parts) if dir_parts else "N/A"
        lead_cast = ", ".join(act_parts[:10]) if act_parts else "N/A"

        try:
            runtime_int = int(runtimes[i]) if i < len(runtimes) and runtimes[i].strip().isdigit() else None
        except Exception:
            runtime_int = None

        try:
            year_int = int(years[i]) if i < len(years) and years[i].strip().isdigit() else None
        except Exception:
            year_int = None

        try:
            view_count = int(views[i]) if i < len(views) and views[i].strip().isdigit() else 1
        except Exception:
            view_count = 1

        rows.append({
            "Title":       title,
            "Rank":        rank_lookup.get(title, len(ranked)),
            "Chrono #":    i + 1,
            "Date Watched": first_date,
            "_date_dt":    first_dt,
            "Year":        year_int,
            "Runtime (min)": runtime_int,
            "Views":       view_count,
            "Genres":      ", ".join(genre_list) if genre_list else "N/A",
            "_genre_list": genre_list,
            "Director":    director,
            "Lead Cast":   lead_cast,
            "Composer":    (composers[i] if i < len(composers) else "").strip() or "N/A",
            "All Dates":   ", ".join(date_list),
        })

    df = pd.DataFrame(rows)
    df["Rank"] = df["Rank"] + 1   # 1-indexed display rank
    return df


df_all = load_data(DATA_FILE)

if df_all.empty:
    st.error(f"Could not load '{DATA_FILE}'. Make sure it's in the same folder as this script.")
    st.stop()


# ─────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("# 🎬 2020 Vision")
    st.markdown("*Your personal film archive*")
    st.divider()

    page = st.radio(
        "Navigate",
        ["📋 Lists", "🔍 Search & Filter", "📊 Stats & Charts", "👤 Creatives"],
        label_visibility="collapsed",
    )

    st.divider()
    total = len(df_all)
    total_views = df_all["Views"].sum()
    st.metric("Films logged", total)
    st.metric("Total watches", int(total_views))
    total_hrs = df_all["Runtime (min)"].dropna().astype(int)
    # weight by views
    weighted_mins = sum(
        int(row["Runtime (min)"]) * int(row["Views"])
        for _, row in df_all.iterrows()
        if pd.notna(row["Runtime (min)"]) and pd.notna(row["Views"])
    )
    st.metric("Hours watched", f"{weighted_mins / 60:,.0f}")


# ─────────────────────────────────────────────
# PAGE: LISTS
# ─────────────────────────────────────────────
if page == "📋 Lists":
    st.markdown("## Your Film Lists")

    tab_ranked, tab_chrono = st.tabs(["🏆 Ranked (Best → Worst)", "📅 Chronological"])

    display_cols = ["Rank", "Title", "Year", "Date Watched", "Runtime (min)", "Views", "Genres", "Director"]

    with tab_ranked:
        df_ranked = df_all.sort_values("Rank")[display_cols].reset_index(drop=True)
        st.dataframe(
            df_ranked,
            use_container_width=True,
            hide_index=True,
            height=700,
            column_config={
                "Rank": st.column_config.NumberColumn("Rank", width="small"),
                "Title": st.column_config.TextColumn("Title", width="medium"),
                "Year": st.column_config.NumberColumn("Year", format="%d", width="small"),
                "Runtime (min)": st.column_config.NumberColumn("Mins", width="small"),
                "Views": st.column_config.NumberColumn("👁", width="small"),
                "Genres": st.column_config.TextColumn("Genres", width="medium"),
                "Director": st.column_config.TextColumn("Director", width="medium"),
            },
        )

    with tab_chrono:
        df_chrono = df_all.sort_values("Chrono #")[["Chrono #", "Title", "Year", "Date Watched", "Runtime (min)", "Views", "Genres", "Director"]].reset_index(drop=True)
        st.dataframe(
            df_chrono,
            use_container_width=True,
            hide_index=True,
            height=700,
            column_config={
                "Chrono #": st.column_config.NumberColumn("#", width="small"),
                "Title": st.column_config.TextColumn("Title", width="medium"),
                "Year": st.column_config.NumberColumn("Year", format="%d", width="small"),
                "Runtime (min)": st.column_config.NumberColumn("Mins", width="small"),
                "Views": st.column_config.NumberColumn("👁", width="small"),
            },
        )


# ─────────────────────────────────────────────
# PAGE: SEARCH & FILTER
# ─────────────────────────────────────────────
elif page == "🔍 Search & Filter":
    st.markdown("## Search & Filter")

    col1, col2, col3 = st.columns([3, 2, 2])
    with col1:
        query = st.text_input("Search title, director, cast…", placeholder="e.g. Nolan, Gyllenhaal, Parasite")
    with col2:
        all_genres = sorted(set(g for glist in df_all["_genre_list"] for g in glist if g))
        genre_filter = st.selectbox("Genre", ["All"] + all_genres)
    with col3:
        years_available = sorted(df_all["Year"].dropna().astype(int).unique())
        year_filter = st.selectbox("Release Year", ["All"] + [str(y) for y in years_available])

    df_filtered = df_all.copy()

    if query:
        q = query.lower()
        mask = (
            df_filtered["Title"].str.lower().str.contains(q, na=False) |
            df_filtered["Director"].str.lower().str.contains(q, na=False) |
            df_filtered["Lead Cast"].str.lower().str.contains(q, na=False) |
            df_filtered["Composer"].str.lower().str.contains(q, na=False)
        )
        df_filtered = df_filtered[mask]

    if genre_filter != "All":
        df_filtered = df_filtered[df_filtered["_genre_list"].apply(lambda g: genre_filter in g)]

    if year_filter != "All":
        df_filtered = df_filtered[df_filtered["Year"] == int(year_filter)]

    st.markdown(f"**{len(df_filtered)}** films match")

    show_cols = ["Rank", "Title", "Year", "Date Watched", "Runtime (min)", "Views", "Genres", "Director"]
    st.dataframe(
        df_filtered[show_cols].sort_values("Rank").reset_index(drop=True),
        use_container_width=True,
        hide_index=True,
        height=600,
        column_config={
            "Rank": st.column_config.NumberColumn("Rank", width="small"),
            "Runtime (min)": st.column_config.NumberColumn("Mins", width="small"),
            "Views": st.column_config.NumberColumn("👁", width="small"),
        },
    )

    # Film detail expander
    if not df_filtered.empty:
        st.divider()
        st.markdown("#### View film details")
        chosen = st.selectbox(
            "Select a film",
            df_filtered.sort_values("Rank")["Title"].tolist(),
            label_visibility="collapsed",
        )
        if chosen:
            row = df_filtered[df_filtered["Title"] == chosen].iloc[0]
            c1, c2 = st.columns(2)
            with c1:
                st.markdown(f"### {row['Title']}")
                st.markdown(f"**Year:** {row['Year']}  |  **Runtime:** {row['Runtime (min)']} min")
                st.markdown(f"**Rank:** #{int(row['Rank'])}  |  **Times watched:** {row['Views']}")
                st.markdown(f"**Genres:** {row['Genres']}")
                st.markdown(f"**Director:** {row['Director']}")
                st.markdown(f"**Composer:** {row['Composer']}")
            with c2:
                st.markdown(f"**First watched:** {row['Date Watched']}")
                st.markdown(f"**All watch dates:** {row['All Dates']}")
                st.markdown(f"**Lead cast:**")
                st.caption(row["Lead Cast"])


# ─────────────────────────────────────────────
# PAGE: STATS & CHARTS
# ─────────────────────────────────────────────
elif page == "📊 Stats & Charts":
    st.markdown("## Stats & Charts")

    # --- Row 1: top-level metrics
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total films", len(df_all))
    m2.metric("Total watches", int(df_all["Views"].sum()))
    avg_rt = df_all["Runtime (min)"].dropna().mean()
    m3.metric("Avg runtime", f"{avg_rt:.0f} min")
    oldest = df_all["Year"].dropna().astype(int).min()
    newest = df_all["Year"].dropna().astype(int).max()
    m4.metric("Oldest film year", oldest)
    m5.metric("Newest film year", newest)

    st.divider()

    col_l, col_r = st.columns(2)

    with col_l:
        st.markdown("#### Films watched per year")
        df_all["Watch Year"] = df_all["_date_dt"].apply(lambda d: d.year if d else None)
        watches_per_year = df_all["Watch Year"].dropna().astype(int).value_counts().sort_index()
        st.bar_chart(watches_per_year, use_container_width=True)

    with col_r:
        st.markdown("#### Films by release decade")
        df_all["Decade"] = df_all["Year"].dropna().astype(int).apply(lambda y: f"{(y // 10) * 10}s")
        decade_counts = df_all["Decade"].value_counts().sort_index()
        st.bar_chart(decade_counts, use_container_width=True)

    col_l2, col_r2 = st.columns(2)

    with col_l2:
        st.markdown("#### Top 15 genres")
        all_genres_flat = [g for glist in df_all["_genre_list"] for g in glist if g]
        genre_counts = Counter(all_genres_flat).most_common(15)
        genre_df = pd.DataFrame(genre_counts, columns=["Genre", "Count"]).set_index("Genre")
        st.bar_chart(genre_df, use_container_width=True)

    with col_r2:
        st.markdown("#### Runtime distribution")
        rt_series = df_all["Runtime (min)"].dropna().astype(int)
        bins = [0, 80, 100, 120, 140, 160, 180, 300]
        labels = ["<80", "80-100", "100-120", "120-140", "140-160", "160-180", "180+"]
        rt_bucketed = pd.cut(rt_series, bins=bins, labels=labels)
        st.bar_chart(rt_bucketed.value_counts().sort_index(), use_container_width=True)

    st.divider()
    st.markdown("#### Rewatched films")
    rewatched = df_all[df_all["Views"] > 1][["Rank", "Title", "Year", "Views", "All Dates"]].sort_values("Views", ascending=False)
    st.dataframe(rewatched, use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────
# PAGE: CREATIVES
# ─────────────────────────────────────────────
elif page == "👤 Creatives":
    st.markdown("## Directors, Actors & Composers")

    # Build frequency counters
    directors, actors, composers_list = [], [], []
    for _, row in df_all.iterrows():
        cast_raw = row.get("Director", "")
        dirs = [d.strip() for d in cast_raw.split(",") if d.strip() and d.strip().upper() != "N/A"]
        directors.extend(dirs)

        cast_raw2 = row.get("Lead Cast", "")
        acts = [a.strip() for a in cast_raw2.split(",") if a.strip() and a.strip().upper() != "N/A"]
        actors.extend(acts)

        comp = str(row.get("Composer", "")).strip()
        if comp and comp.upper() not in {"N/A", "NO COMPOSERS", ""}:
            composers_list.append(comp)

    top_n = st.slider("Show top N", 10, 50, 25)

    tab_d, tab_a, tab_c = st.tabs(["🎬 Directors", "🌟 Actors", "🎵 Composers"])

    with tab_d:
        top_dirs = pd.DataFrame(Counter(directors).most_common(top_n), columns=["Director", "Films"])
        st.dataframe(top_dirs, use_container_width=True, hide_index=True, height=600)

    with tab_a:
        top_acts = pd.DataFrame(Counter(actors).most_common(top_n), columns=["Actor", "Films"])
        st.dataframe(top_acts, use_container_width=True, hide_index=True, height=600)

    with tab_c:
        top_comps = pd.DataFrame(Counter(composers_list).most_common(top_n), columns=["Composer", "Films"])
        st.dataframe(top_comps, use_container_width=True, hide_index=True, height=600)
