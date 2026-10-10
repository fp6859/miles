import streamlit as st
import requests
import time
from datetime import datetime, timezone
import pandas as pd

st.set_page_config(page_title="Flashscore Odds Shortening Finder", page_icon="📉", layout="wide")
st.title("📉 Flashscore Odds Shortening Finder")
st.caption("Find games where main-market odds have shortened (opening → current).")

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

def fetch_odds(event_id: str, geo_code: str = "GB") -> tuple[list[dict], str]:
    """Returns (odds_list, error_message)"""
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
            timeout=12
        )
        if resp.status_code != 200:
            return [], f"HTTP {resp.status_code}"
        data = resp.json()
        event_odds = data.get("data", {}).get("findOddsByEventId") or {}
        if not event_odds:
            return [], "Empty findOddsByEventId"
    except Exception as e:
        return [], str(e)

    bk_map = {}
    for bk in event_odds.get("settings", {}).get("bookmakers", []):
        inner = bk.get("bookmaker", {})
        bid = str(inner.get("id", ""))
        if bid:
            bk_map[bid] = inner.get("name", bid)

    results = []
    for item in event_odds.get("odds", []):
        if item.get("bettingScope") != "FULL_TIME":
            continue
        if item.get("bettingType") not in ("HOME_DRAW_AWAY", "HOME_AWAY"):
            continue
        odds_items = item.get("odds", [])
        outcomes = []
        for o in odds_items:
            try:
                curr = float(o["value"])
                open_raw = o.get("opening")
                open_ = float(open_raw) if open_raw is not None else curr
                outcomes.append({"current": curr, "opening": open_})
            except (TypeError, ValueError, KeyError):
                continue
        if outcomes:
            results.append({
                "bookmaker_name": bk_map.get(str(item.get("bookmakerId", "")), "Unknown"),
                "bettingType": item.get("bettingType"),
                "outcomes": outcomes,
            })
    return results, ""

def percentage_shortening(opening: float, current: float) -> float:
    if not opening or opening <= 0 or current >= opening:
        return 0.0
    return round((opening - current) / opening * 100.0, 1)

# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.header("Settings")
    sport_name = st.selectbox("Sport", list(SPORTS.keys()))
    threshold = st.slider("Minimum shortening (%)", 1.0, 30.0, 5.0, 0.5)
    day_label = st.radio("Day", ["Today", "Tomorrow", "Yesterday"], horizontal=True)
    day_offset = {"Today": 0, "Tomorrow": 1, "Yesterday": -1}[day_label]
    geo_code = st.selectbox("Bookmaker region", ["GB", "US", "DE", "FR", "ES", "IT"])
    max_matches = st.number_input("Max matches to scan", 10, 100, 30, 5)
    delay = st.slider("Delay (seconds)", 0.3, 2.0, 0.6, 0.1)
    run_button = st.button("🔍 Search", type="primary", use_container_width=True)

# ── Main ─────────────────────────────────────────────────────
if run_button:
    sport_id = SPORTS[sport_name]
    with st.spinner("Fetching matches..."):
        matches = fetch_matches(sport_id, day_offset)

    if not matches:
        st.warning("No matches returned from the feed.")
        st.stop()

    st.info(f"Found {len(matches)} matches. Checking odds...")

    progress = st.progress(0)
    status = st.empty()
    candidates = []
    all_moves = []
    odds_errors = {}
    matches_with_odds = 0

    to_scan = matches[:max_matches]

    for i, m in enumerate(to_scan):
        status.text(f"{i+1}/{len(to_scan)}: {m['home']} vs {m['away']}")
        progress.progress((i + 1) / len(to_scan))

        odds_list, err = fetch_odds(m["event_id"], geo_code)
        if err:
            odds_errors[err] = odds_errors.get(err, 0) + 1
            continue

        if not odds_list:
            odds_errors["No usable odds"] = odds_errors.get("No usable odds", 0) + 1
            continue

        matches_with_odds += 1
        best_pct = 0.0
        best_detail = None

        for bk in odds_list:
            for oc in bk["outcomes"]:
                pct = percentage_shortening(oc["opening"], oc["current"])
                if pct > 0:
                    all_moves.append({
                        "Match": f"{m['home']} vs {m['away']}",
                        "Bookmaker": bk["bookmaker_name"],
                        "Opening": oc["opening"],
                        "Current": oc["current"],
                        "Shortening %": pct,
                    })
                if pct > best_pct:
                    best_pct = pct
                    best_detail = (bk["bookmaker_name"], oc["opening"], oc["current"], pct)

        if best_pct >= threshold:
            start = datetime.fromtimestamp(m["ts_start"], tz=timezone.utc) if m["ts_start"] else None
            candidates.append({
                "Home": m["home"],
                "Away": m["away"],
                "Tournament": m["tourney"],
                "Start (UTC)": start.strftime("%Y-%m-%d %H:%M") if start else "",
                "Max Shortening %": best_pct,
                "Best Move": f"{best_detail[0]} {best_detail[1]} → {best_detail[2]}" if best_detail else "",
                "Link": f"https://www.flashscore.com/match/{m['event_id']}/",
            })

        time.sleep(delay)

    progress.empty()
    status.empty()

    # Error summary
    if odds_errors:
        st.warning("Odds request errors:")
        for err, count in sorted(odds_errors.items(), key=lambda x: -x[1]):
            st.write(f"- **{err}**: {count} matches")

    st.info(f"Scanned {len(to_scan)} matches → {matches_with_odds} had usable odds data")

    if all_moves:
        st.subheader("Biggest moves found")
        st.dataframe(pd.DataFrame(all_moves).sort_values("Shortening %", ascending=False).head(20), hide_index=True)

    candidates.sort(key=lambda x: -x["Max Shortening %"])
    st.success(f"{len(candidates)} games ≥ {threshold}%")

    if candidates:
        df = pd.DataFrame(candidates)
        st.dataframe(df, column_config={"Link": st.column_config.LinkColumn()}, hide_index=True, use_container_width=True)

else:
    st.info("Set filters on the left and click Search.")

st.caption("⚠️ Unofficial Flashscore endpoints — they can break or get blocked at any time.")
