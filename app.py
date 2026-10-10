import streamlit as st
import requests
import time
from datetime import datetime, timezone
import pandas as pd

# ── Page config ──────────────────────────────────────────────
st.set_page_config(
    page_title="Flashscore Odds Shortening Finder",
    page_icon="📉",
    layout="wide"
)

st.title("📉 Flashscore Odds Shortening Finder")
st.caption("Find games where main-market odds have shortened by a chosen percentage (opening → current).")

# ── Sport mapping ────────────────────────────────────────────
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

# ── Core functions ───────────────────────────────────────────
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
        st.error(f"Failed to fetch matches: {e}")
        return []

    matches = []
    cur_tourney = ""
    for section in raw.split("~"):
        fields = parse_fields(section)
        if "ZA" in fields:
            cur_tourney = fields["ZA"]
            continue
        if "AA" not in fields:
            continue
        if fields.get("AB") == "3":  # finished
            continue

        matches.append({
            "event_id": fields["AA"],
            "home": fields.get("AE", ""),
            "away": fields.get("AF", ""),
            "tourney": cur_tourney,
            "ts_start": int(fields.get("AD", 0) or 0),
        })
    return matches

def fetch_odds(event_id: str, geo_code: str = "GB") -> list[dict]:
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
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        return []

    event_odds = data.get("data", {}).get("findOddsByEventId") or {}
    if not event_odds:
        return []

    bk_map = {}
    for bk in event_odds.get("settings", {}).get("bookmakers", []):
        inner = bk.get("bookmaker", {})
        bid = str(inner.get("id", ""))
        if bid:
            bk_map[bid] = inner.get("name", bid)

    results = []
    for item in event_odds.get("odds", []):
        btype = item.get("bettingType")
        scope = item.get("bettingScope")
        if scope != "FULL_TIME" or btype not in ("HOME_DRAW_AWAY", "HOME_AWAY"):
            continue

        odds_items = item.get("odds", [])
        outcomes = []
        for o in odds_items:
            try:
                curr = float(o["value"])
                open_raw = o.get("opening")
                open_ = float(open_raw) if open_raw is not None else curr
                outcomes.append({
                    "current": curr,
                    "opening": open_,
                    "opening_raw": open_raw,  # for debugging
                })
            except (TypeError, ValueError, KeyError):
                continue

        if outcomes:
            results.append({
                "bookmaker_name": bk_map.get(str(item.get("bookmakerId", "")), "Unknown"),
                "bettingType": btype,
                "outcomes": outcomes,
            })
    return results

def percentage_shortening(opening: float, current: float) -> float:
    if not opening or opening <= 0:
        return 0.0
    if current >= opening:
        return 0.0
    return round((opening - current) / opening * 100.0, 1)

# ── Sidebar controls ─────────────────────────────────────────
with st.sidebar:
    st.header("Settings")
    sport_name = st.selectbox("Sport", list(SPORTS.keys()), index=0)
    threshold = st.slider("Minimum shortening (%)", 1.0, 30.0, 10.0, 0.5)
    day_label = st.radio("Day", ["Today", "Tomorrow", "Yesterday"], horizontal=True)
    day_offset = {"Today": 0, "Tomorrow": 1, "Yesterday": -1}[day_label]
    geo_code = st.selectbox("Bookmaker region", ["GB", "US", "DE", "FR", "ES", "IT"], index=0)
    max_matches = st.number_input("Max matches to scan", min_value=10, max_value=200, value=50, step=10)
    delay = st.slider("Delay between requests (seconds)", 0.2, 1.5, 0.4, 0.1)
    show_debug = st.checkbox("Show debug info", value=True)

    run_button = st.button("🔍 Search for shortened odds", type="primary", use_container_width=True)

# ── Main area ────────────────────────────────────────────────
if run_button:
    sport_id = SPORTS[sport_name]

    with st.spinner(f"Fetching {sport_name} matches..."):
        matches = fetch_matches(sport_id, day_offset)

    if not matches:
        st.warning("No matches found or the feed failed.")
        st.stop()

    st.info(f"Found {len(matches)} matches. Scanning odds...")

    progress = st.progress(0)
    status = st.empty()
    candidates = []
    all_moves_summary = []
    debug_samples = []
    matches_with_odds = 0
    matches_with_any_movement = 0

    matches_to_scan = matches[:max_matches]

    for i, m in enumerate(matches_to_scan):
        status.text(f"Checking {i+1}/{len(matches_to_scan)}: {m['home']} vs {m['away']}")
        progress.progress((i + 1) / len(matches_to_scan))

        odds_list = fetch_odds(m["event_id"], geo_code=geo_code)
        if not odds_list:
            continue

        matches_with_odds += 1

        best_pct = 0.0
        best_detail = None
        match_moves = []

        for bk in odds_list:
            for oc in bk["outcomes"]:
                opening = oc["opening"]
                current = oc["current"]
                pct = percentage_shortening(opening, current)

                if pct > 0:
                    matches_with_any_movement += 1
                    move = {
                        "bookmaker": bk["bookmaker_name"],
                        "opening": opening,
                        "current": current,
                        "pct": pct,
                    }
                    match_moves.append(move)
                    all_moves_summary.append({
                        "Match": f"{m['home']} vs {m['away']}",
                        "Bookmaker": bk["bookmaker_name"],
                        "Opening": opening,
                        "Current": current,
                        "Shortening %": pct,
                    })

                if pct > best_pct:
                    best_pct = pct
                    best_detail = {
                        "bookmaker": bk["bookmaker_name"],
                        "opening": opening,
                        "current": current,
                        "pct": pct,
                    }

        # Collect debug samples
        if len(debug_samples) < 6 and odds_list:
            sample_odds = []
            for bk in odds_list[:3]:
                for oc in bk["outcomes"]:
                    sample_odds.append({
                        "bookmaker": bk["bookmaker_name"],
                        "opening_raw": oc.get("opening_raw"),
                        "opening": oc["opening"],
                        "current": oc["current"],
                    })
            debug_samples.append({
                "match": f"{m['home']} vs {m['away']}",
                "odds": sample_odds[:6],  # limit size
            })

        if best_pct >= threshold:
            start_dt = datetime.fromtimestamp(m["ts_start"], tz=timezone.utc) if m["ts_start"] else None
            candidates.append({
                "Home": m["home"],
                "Away": m["away"],
                "Tournament": m["tourney"],
                "Start (UTC)": start_dt.strftime("%Y-%m-%d %H:%M") if start_dt else "",
                "Max Shortening %": best_pct,
                "Best Move": f"{best_detail['bookmaker']} {best_detail['opening']} → {best_detail['current']}" if best_detail else "",
                "Link": f"https://www.flashscore.com/match/{m['event_id']}/",
                "_moves": match_moves,
            })

        if delay and i < len(matches_to_scan) - 1:
            time.sleep(delay)

    progress.empty()
    status.empty()

    # Summary stats
    st.info(
        f"Scanned **{len(matches_to_scan)}** matches → "
        f"**{matches_with_odds}** had odds data → "
        f"**{matches_with_any_movement}** had at least some shortening"
    )

    # Biggest moves (any size)
    if all_moves_summary:
        st.subheader("Biggest moves found (any size)")
        moves_df = (
            pd.DataFrame(all_moves_summary)
            .sort_values("Shortening %", ascending=False)
            .head(25)
        )
        st.dataframe(moves_df, hide_index=True, use_container_width=True)

    # Results that meet the threshold
    candidates.sort(key=lambda x: (-x["Max Shortening %"], x["Start (UTC)"]))

    st.success(f"Found **{len(candidates)}** games with ≥ {threshold}% shortening")

    if candidates:
        df = pd.DataFrame([{k: v for k, v in c.items() if not k.startswith("_")} for c in candidates])
        st.dataframe(
            df,
            column_config={
                "Link": st.column_config.LinkColumn("Flashscore"),
                "Max Shortening %": st.column_config.NumberColumn(format="%.1f%%"),
            },
            hide_index=True,
            use_container_width=True,
            height=350
        )

        st.subheader("Detailed moves")
        for c in candidates:
            with st.expander(f"{c['Home']} vs {c['Away']} — {c['Max Shortening %']}% ({c['Tournament']})"):
                st.write(f"**Start:** {c['Start (UTC)']}")
                st.markdown(f"[Open on Flashscore]({c['Link']})")
                if c["_moves"]:
                    moves_df = pd.DataFrame(c["_moves"]).sort_values("pct", ascending=False)
                    st.dataframe(moves_df, hide_index=True, use_container_width=True)
    else:
        st.info("No games met the threshold. Check the 'Biggest moves found' table above or lower the percentage.")

    # Debug section
    if show_debug and debug_samples:
        with st.expander("Debug: sample opening vs current values from the API"):
            for sample in debug_samples:
                st.write(f"**{sample['match']}**")
                st.json(sample["odds"])

else:
    st.info("👈 Set your filters on the left and click **Search for shortened odds**.")

st.markdown("---")
st.caption(
    "⚠️ Uses Flashscore’s unofficial internal feeds. Endpoints can change or break without notice. "
    "Use responsibly and respect rate limits. Not affiliated with Flashscore."
)
