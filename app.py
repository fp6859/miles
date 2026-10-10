import streamlit as st
import requests
import time
import json
import re
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd

st.set_page_config(page_title="Odds Movement Tracker", page_icon="📉", layout="wide")
st.title("📉 Odds Movement Tracker")
st.caption("Uses the Flashscore feed (which works) and extracts any odds present.")

SPORTS = {
    "Football (Soccer)": 1,
    "Tennis": 2,
    "Basketball": 3,
    "Ice Hockey": 4,
    "American Football": 5,
    "Baseball": 6,
    "Handball": 7,
    "Rugby Union": 8,
    "Volleyball": 12,
}

SEP_FIELD = chr(0xAC)
SEP_KV = chr(0xF7)
CACHE_FILE = Path("odds_cache.json")

FS_HEADERS = {
    "x-fsign": "SW9D1eZo",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://www.flashscore.com/",
    "Accept": "*/*",
    "Origin": "https://www.flashscore.com",
}

def parse_fields(section: str) -> dict:
    out = {}
    for part in section.split(SEP_FIELD):
        if SEP_KV in part:
            k, _, v = part.partition(SEP_KV)
            out[k] = v
    return out

def fetch_matches_with_odds(sport_id: int, day_offset: int = 0) -> list[dict]:
    url = f"https://www.flashscore.com/x/feed/f_{sport_id}_{day_offset}_0_en-gb_1"
    try:
        resp = requests.get(url, headers=FS_HEADERS, timeout=15)
        resp.raise_for_status()
        raw = resp.text
    except Exception as e:
        st.error(f"Feed failed: {e}")
        return []

    matches = []
    cur_tourney = ""
    for section in raw.split("~"):
        fields = parse_fields(section)
        if "ZA" in fields:
            cur_tourney = fields["ZA"]
            continue
        if "AA" not in fields or fields.get("AB") == "3":
            continue

        # Try to pull any odds-like fields that sometimes appear in the feed
        odds = {}
        for k, v in fields.items():
            if k.startswith("OD") or "odd" in k.lower():
                try:
                    odds[k] = float(v)
                except ValueError:
                    pass

        matches.append({
            "event_id": fields["AA"],
            "home": fields.get("AE", ""),
            "away": fields.get("AF", ""),
            "tourney": cur_tourney,
            "ts_start": int(fields.get("AD", 0) or 0),
            "raw_odds": odds,
            "fields_sample": {k: fields[k] for k in list(fields.keys())[:8]},  # debug
        })
    return matches

def load_cache() -> dict:
    if "odds_cache" not in st.session_state:
        st.session_state.odds_cache = {}
        if CACHE_FILE.exists():
            try:
                st.session_state.odds_cache = json.loads(CACHE_FILE.read_text())
            except Exception:
                pass
    return st.session_state.odds_cache

def save_cache(cache: dict):
    st.session_state.odds_cache = cache
    try:
        CACHE_FILE.write_text(json.dumps(cache, indent=2))
    except Exception:
        pass

def percentage_drop(old: float, new: float) -> float:
    if not old or old <= 0 or new >= old:
        return 0.0
    return round((old - new) / old * 100.0, 1)

# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.header("Settings")
    sport_name = st.selectbox("Sport", list(SPORTS.keys()))
    threshold = st.slider("Minimum shortening since last check (%)", 1.0, 20.0, 5.0, 0.5)
    day_label = st.radio("Day", ["Today", "Tomorrow", "Yesterday"], horizontal=True)
    day_offset = {"Today": 0, "Tomorrow": 1, "Yesterday": -1}[day_label]
    max_matches = st.number_input("Max matches", 10, 100, 40, 10)
    clear_cache = st.button("Clear stored odds", use_container_width=True)
    run_button = st.button("🔄 Check for movement", type="primary", use_container_width=True)

if clear_cache:
    st.session_state.odds_cache = {}
    if CACHE_FILE.exists():
        CACHE_FILE.unlink(missing_ok=True)
    st.success("Cleared.")

# ── Main ─────────────────────────────────────────────────────
cache = load_cache()

if run_button:
    sport_id = SPORTS[sport_name]
    with st.spinner("Fetching feed..."):
        matches = fetch_matches_with_odds(sport_id, day_offset)

    if not matches:
        st.warning("No matches returned.")
        st.stop()

    st.info(f"Found {len(matches)} matches in the feed.")

    # Debug: show a sample of what fields are present
    with st.expander("Debug: sample feed fields from first few matches"):
        for m in matches[:3]:
            st.write(f"**{m['home']} vs {m['away']}**")
            st.json(m["fields_sample"])
            if m["raw_odds"]:
                st.write("Odds-like fields:", m["raw_odds"])

    # For now just display the matches we found
    rows = []
    for m in matches[:max_matches]:
        start = datetime.fromtimestamp(m["ts_start"], tz=timezone.utc) if m["ts_start"] else None
        rows.append({
            "Home": m["home"],
            "Away": m["away"],
            "Tournament": m["tourney"],
            "Start (UTC)": start.strftime("%Y-%m-%d %H:%M") if start else "",
            "Event ID": m["event_id"],
            "Has odds fields": bool(m["raw_odds"]),
        })

    st.subheader("Matches from feed")
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True, height=400)

    st.info("The feed works for matches. Odds are not currently appearing in the feed fields. We’ll need a different source for the actual 1X2 numbers.")

else:
    st.info("Click the button to load matches from the feed.")

st.markdown("---")
st.caption("This version confirms the feed works. Next step is a reliable odds source.")
