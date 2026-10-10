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
st.caption("GraphQL first, HTML scrape fallback. Shows current odds and tracks shortening since last check.")

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
    "Accept-Language": "en-GB,en;q=0.9",
}

def parse_fields(section: str) -> dict:
    out = {}
    for part in section.split(SEP_FIELD):
        if SEP_KV in part:
            k, _, v = part.partition(SEP_KV)
            out[k] = v
    return out

def fetch_matches(sport_id: int, day_offset: int = 0) -> list[dict]:
    url = f"https://www.flashscore.com/x/feed/f_{sport_id}_{day_offset}_0_en-gb_1"
    try:
        resp = requests.get(url, headers=FS_HEADERS, timeout=15)
        resp.raise_for_status()
        raw = resp.text
    except Exception as e:
        st.error(f"Match feed failed: {e}")
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
        matches.append({
            "event_id": fields["AA"],
            "home": fields.get("AE", ""),
            "away": fields.get("AF", ""),
            "tourney": cur_tourney,
            "ts_start": int(fields.get("AD", 0) or 0),
        })
    return matches

def fetch_odds_graphql(event_id: str, geo_code: str = "GB") -> dict:
    params = {
        "_hash": "oce",
        "eventId": event_id,
        "projectId": "2",
        "geoIpCode": geo_code,
    }
    try:
        resp = requests.get(
            "https://global.ds.lsapp.eu/odds/pq_graphql",
            params=params,
            headers=FS_HEADERS,
            timeout=10
        )
        if resp.status_code != 200:
            return {}
        data = resp.json()
        event_odds = data.get("data", {}).get("findOddsByEventId") or {}
        if not event_odds:
            return {}
        for item in event_odds.get("odds", []):
            if item.get("bettingScope") != "FULL_TIME":
                continue
            if item.get("bettingType") not in ("HOME_DRAW_AWAY", "HOME_AWAY"):
                continue
            odds_items = item.get("odds", [])
            if not odds_items:
                continue
            try:
                result = {
                    "home_current": float(odds_items[0]["value"]),
                    "home_opening": float(odds_items[0].get("opening") or odds_items[0]["value"]),
                    "away_current": float(odds_items[-1]["value"]),
                    "away_opening": float(odds_items[-1].get("opening") or odds_items[-1]["value"]),
                    "source": "graphql",
                }
                if len(odds_items) > 2:
                    result["draw_current"] = float(odds_items[1]["value"])
                    result["draw_opening"] = float(odds_items[1].get("opening") or odds_items[1]["value"])
                return result
            except (TypeError, ValueError, KeyError, IndexError):
                continue
    except Exception:
        pass
    return {}

def fetch_odds_html_fallback(event_id: str) -> dict:
    """Simple HTML scrape fallback – extracts the first few decimal odds it finds."""
    url = f"https://www.flashscore.com/match/{event_id}/"
    try:
        resp = requests.get(url, headers=FS_HEADERS, timeout=12)
        if resp.status_code != 200:
            return {}
        html = resp.text
        # Look for sequences of decimal odds (common on the page)
        odds_matches = re.findall(r'\b([1-9]\d?\.\d{2})\b', html)
        odds = []
        for o in odds_matches:
            try:
                val = float(o)
                if 1.01 <= val <= 50.0:  # reasonable decimal odds range
                    odds.append(val)
            except ValueError:
                continue
        # Deduplicate while preserving order
        seen = set()
        unique = []
        for o in odds:
            if o not in seen:
                seen.add(o)
                unique.append(o)
            if len(unique) >= 6:
                break
        if len(unique) >= 2:
            result = {
                "home_current": unique[0],
                "away_current": unique[1] if len(unique) > 1 else unique[0],
                "source": "html",
            }
            if len(unique) >= 3:
                result["draw_current"] = unique[2]
            return result
    except Exception:
        pass
    return {}

def fetch_odds(event_id: str, geo_code: str = "GB") -> dict:
    odds = fetch_odds_graphql(event_id, geo_code)
    if odds:
        return odds
    return fetch_odds_html_fallback(event_id)

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
    threshold = st.slider("Minimum shortening since last check (%)", 1.0, 25.0, 5.0, 0.5)
    day_label = st.radio("Day", ["Today", "Tomorrow", "Yesterday"], horizontal=True)
    day_offset = {"Today": 0, "Tomorrow": 1, "Yesterday": -1}[day_label]
    geo_code = st.selectbox("Bookmaker region", ["GB", "US", "DE", "FR", "ES", "IT"])
    max_matches = st.number_input("Max matches to scan", 5, 40, 15, 5)
    delay = st.slider("Delay between requests (s)", 0.5, 2.5, 1.0, 0.1)
    clear_cache = st.button("Clear stored odds", use_container_width=True)
    run_button = st.button("🔄 Check for movement", type="primary", use_container_width=True)

if clear_cache:
    st.session_state.odds_cache = {}
    if CACHE_FILE.exists():
        CACHE_FILE.unlink(missing_ok=True)
    st.success("Stored odds cleared.")

# ── Main ─────────────────────────────────────────────────────
cache = load_cache()

if run_button:
    sport_id = SPORTS[sport_name]
    with st.spinner("Fetching matches..."):
        matches = fetch_matches(sport_id, day_offset)

    if not matches:
        st.warning("No matches found.")
        st.stop()

    st.info(f"Found {len(matches)} matches. Checking odds (GraphQL + HTML fallback)...")

    progress = st.progress(0)
    status = st.empty()
    movements = []
    all_odds_rows = []
    new_cache = {}
    scanned_with_odds = 0

    to_scan = matches[:max_matches]

    for i, m in enumerate(to_scan):
        status.text(f"{i+1}/{len(to_scan)}: {m['home']} vs {m['away']}")
        progress.progress((i + 1) / len(to_scan))

        odds = fetch_odds(m["event_id"], geo_code)
        if not odds:
            continue

        scanned_with_odds += 1
        key = m["event_id"]

        new_cache[key] = {
            "home": odds.get("home_current"),
            "draw": odds.get("draw_current"),
            "away": odds.get("away_current"),
            "updated": datetime.now(timezone.utc).isoformat(),
            "match": f"{m['home']} vs {m['away']}",
        }

        start = datetime.fromtimestamp(m["ts_start"], tz=timezone.utc) if m["ts_start"] else None
        row = {
            "Home": m["home"],
            "Away": m["away"],
            "Tournament": m["tourney"],
            "Start (UTC)": start.strftime("%Y-%m-%d %H:%M") if start else "",
            "Home Current": odds.get("home_current"),
            "Home Opening": odds.get("home_opening"),
            "Draw Current": odds.get("draw_current"),
            "Draw Opening": odds.get("draw_opening"),
            "Away Current": odds.get("away_current"),
            "Away Opening": odds.get("away_opening"),
            "Source": odds.get("source", "unknown"),
            "Link": f"https://www.flashscore.com/match/{m['event_id']}/",
        }
        all_odds_rows.append(row)

        previous = cache.get(key)
        if previous:
            drops = []
            for side, curr_key in [("home", "home_current"), ("draw", "draw_current"), ("away", "away_current")]:
                old = previous.get(side)
                new = odds.get(curr_key)
                if old and new:
                    pct = percentage_drop(old, new)
                    if pct >= threshold:
                        drops.append(f"{side.title()} {old} → {new} ({pct}%)")
            if drops:
                row["Movement"] = " | ".join(drops)
                movements.append(row)

        time.sleep(delay)

    progress.empty()
    status.empty()

    cache.update(new_cache)
    save_cache(cache)

    st.info(f"Scanned {scanned_with_odds} matches that returned odds data.")

    if all_odds_rows:
        st.subheader("Current & Opening Odds")
        df_all = pd.DataFrame(all_odds_rows)
        st.dataframe(
            df_all,
            column_config={"Link": st.column_config.LinkColumn()},
            hide_index=True,
            use_container_width=True,
            height=400,
        )

    if movements:
        st.success(f"**{len(movements)}** games shortened ≥ {threshold}% since last check")
        st.dataframe(pd.DataFrame(movements), column_config={"Link": st.column_config.LinkColumn()}, hide_index=True, use_container_width=True)
    else:
        if any(cache):
            st.info("No significant shortening since the last check.")
        else:
            st.success("First snapshot saved. Run again later to detect movement.")

else:
    if cache:
        st.info(f"Stored snapshot for **{len(cache)}** matches. Click **Check for movement** to refresh.")
    else:
        st.info("No snapshot yet. Click **Check for movement** to capture current odds.")

st.markdown("---")
st.caption("HTML fallback is basic and may pick up non-odds numbers. GraphQL is preferred when it works. Movement is based on previous current odds.")
