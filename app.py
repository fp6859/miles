import streamlit as st
import requests
import pandas as pd
from datetime import datetime, timezone
import time

st.set_page_config(page_title="Flashscore Scanner", page_icon="⚽", layout="wide")
st.title("⚽ Flashscore Multi-Sport Scanner")
st.caption("Match list + attempted opening/current odds. Odds endpoint can be flaky.")

# Sport ID mapping used by Flashscore feeds
SPORTS = {
    "Football": 1,
    "Tennis": 2,
    "Basketball": 3,
    "Ice Hockey": 4,
    "American Football": 5,
    "Baseball": 6,
    "Rugby Union": 8,
    "Rugby League": 19,
    "Cricket": 13,
    "Darts": 14,
    "Horse Racing": 31,
}

SEP_FIELD = chr(0xAC)
SEP_KV = chr(0xF7)

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

def fetch_matches(sport_id: int, day_offset: int = 0) -> list[dict]:
    url = f"https://www.flashscore.com/x/feed/f_{sport_id}_{day_offset}_0_en-gb_1"
    try:
        resp = requests.get(url, headers=FS_HEADERS, timeout=15)
        resp.raise_for_status()
        raw = resp.text
    except Exception as e:
        st.error(f"Feed error: {e}")
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
            "tournament": cur_tourney,
            "ts_start": int(fields.get("AD", 0) or 0),
        })
    return matches

def fetch_odds(event_id: str) -> dict:
    """Attempts GraphQL odds. Returns empty dict on failure."""
    params = {
        "_hash": "oce",
        "eventId": event_id,
        "projectId": "2",
        "geoIpCode": "GB",
    }
    try:
        resp = requests.get(
            "https://global.ds.lsapp.eu/odds/pq_graphql",
            params=params,
            headers=FS_HEADERS,
            timeout=10,
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

# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.header("Filters")
    selected_sports = st.multiselect(
        "Sports",
        list(SPORTS.keys()),
        default=["Football", "Tennis"],
    )
    day_label = st.radio("Day", ["Today", "Tomorrow", "Yesterday"], horizontal=True)
    day_offset = {"Today": 0, "Tomorrow": 1, "Yesterday": -1}[day_label]
    league_filter = st.text_input("League / Tournament contains (optional)", "")
    max_matches = st.number_input("Max matches to check for odds", 10, 100, 30, 5)
    try_odds = st.checkbox("Try to fetch opening + current odds", value=True)
    delay = st.slider("Delay between odds requests (s)", 0.3, 2.0, 0.6, 0.1)
    run = st.button("🔍 Scan matches", type="primary", use_container_width=True)

# ── Main ─────────────────────────────────────────────────────
if run and selected_sports:
    all_matches = []
    for sport_name in selected_sports:
        sport_id = SPORTS[sport_name]
        with st.spinner(f"Loading {sport_name}..."):
            matches = fetch_matches(sport_id, day_offset)
            for m in matches:
                m["sport"] = sport_name
            all_matches.extend(matches)

    if league_filter:
        all_matches = [m for m in all_matches if league_filter.lower() in m["tournament"].lower()]

    if not all_matches:
        st.warning("No matches found for the selected filters.")
        st.stop()

    st.info(f"Found {len(all_matches)} matches.")

    # Limit odds checks
    to_check = all_matches[:max_matches]
    rows = []
    progress = st.progress(0)
    status = st.empty()

    for i, m in enumerate(to_check):
        status.text(f"Checking {i+1}/{len(to_check)}: {m['home']} vs {m['away']}")
        progress.progress((i + 1) / len(to_check))

        odds = fetch_odds(m["event_id"]) if try_odds else {}
        start = datetime.fromtimestamp(m["ts_start"], tz=timezone.utc) if m["ts_start"] else None

        row = {
            "Sport": m["sport"],
            "Tournament": m["tournament"],
            "Home": m["home"],
            "Away": m["away"],
            "Start (UTC)": start.strftime("%Y-%m-%d %H:%M") if start else "",
            "Home Current": odds.get("home_current"),
            "Home Opening": odds.get("home_opening"),
            "Draw Current": odds.get("draw_current"),
            "Draw Opening": odds.get("draw_opening"),
            "Away Current": odds.get("away_current"),
            "Away Opening": odds.get("away_opening"),
            "Link": f"https://www.flashscore.com/match/{m['event_id']}/",
        }
        rows.append(row)
        if try_odds:
            time.sleep(delay)

    progress.empty()
    status.empty()

    df = pd.DataFrame(rows)
    st.dataframe(
        df,
        column_config={"Link": st.column_config.LinkColumn()},
        hide_index=True,
        use_container_width=True,
        height=500,
    )

    odds_found = df["Home Current"].notna().sum()
    st.caption(f"Odds data returned for {odds_found} of {len(df)} checked matches.")

else:
    st.info("Select sports and click **Scan matches**.")

st.markdown("---")
st.caption("Match list comes from the Flashscore feed (reliable). Odds are attempted via the GraphQL endpoint and may be empty if it is blocked or rate-limited.")
