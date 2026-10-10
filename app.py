import streamlit as st
import requests
import pandas as pd

st.set_page_config(page_title="bet365 vs Market", page_icon="📊", layout="wide")
st.title("📊 bet365 vs Wider Market")
st.caption("Finds where bet365 odds are higher than the best price elsewhere")

# ── API key ──────────────────────────────────────────────────
API_KEY = st.secrets.get("ODDS_API_KEY", "")
if not API_KEY:
    API_KEY = st.sidebar.text_input("Odds API Key", type="password",
                                    help="Free key from the-odds-api.com")

SPORTS = {
    "Football (EPL)": "soccer_epl",
    "Football (La Liga)": "soccer_spain_la_liga",
    "Football (Bundesliga)": "soccer_germany_bundesliga",
    "Football (Serie A)": "soccer_italy_serie_a",
    "Football (Ligue 1)": "soccer_france_ligue_one",
    "American Football (NFL)": "americanfootball_nfl",
    "Basketball (NBA)": "basketball_nba",
    "Ice Hockey (NHL)": "icehockey_nhl",
    "Baseball (MLB)": "baseball_mlb",
}

def fetch_odds(sport_key: str, regions: str = "uk"):
    if not API_KEY:
        return []
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
    params = {
        "apiKey": API_KEY,
        "regions": regions,
        "markets": "h2h",
        "oddsFormat": "decimal",
    }
    try:
        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        remaining = r.headers.get("x-requests-remaining", "?")
        st.sidebar.caption(f"Requests remaining: {remaining}")
        return r.json()
    except Exception as e:
        st.error(f"API error: {e}")
        return []

# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.header("Settings")
    if not API_KEY:
        st.warning("Enter your free Odds API key")
    sport_name = st.selectbox("Sport / League", list(SPORTS.keys()))
    regions = st.selectbox("Region", ["uk", "eu", "us", "au"], index=0)
    min_edge = st.slider("Minimum bet365 edge (%)", 0.0, 25.0, 3.0, 0.5)
    run = st.button("🔍 Scan bet365 vs market", type="primary", use_container_width=True)

# ── Main ─────────────────────────────────────────────────────
if run and API_KEY:
    sport_key = SPORTS[sport_name]
    with st.spinner("Fetching odds..."):
        events = fetch_odds(sport_key, regions)

    if not events:
        st.warning("No events returned.")
        st.stop()

    rows = []
    for ev in events:
        home = ev.get("home_team", "")
        away = ev.get("away_team", "")
        commence = ev.get("commence_time", "")[:16].replace("T", " ")

        bookmakers = ev.get("bookmakers", [])
        if len(bookmakers) < 2:
            continue

        # Separate bet365 from the rest
        bet365 = None
        others = []
        for bm in bookmakers:
            title = bm.get("title", "").lower()
            if "bet365" in title or "bet 365" in title:
                bet365 = bm
            else:
                others.append(bm)

        if not bet365 or not others:
            continue

        # Get bet365 h2h outcomes
        b365_market = next((m for m in bet365.get("markets", []) if m["key"] == "h2h"), None)
        if not b365_market:
            continue
        b365_odds = {o["name"]: o["price"] for o in b365_market.get("outcomes", [])}

        # Best odds from the rest of the market
        best_odds = {}
        for bm in others:
            market = next((m for m in bm.get("markets", []) if m["key"] == "h2h"), None)
            if not market:
                continue
            for o in market.get("outcomes", []):
                name = o["name"]
                price = o["price"]
                if name not in best_odds or price > best_odds[name]:
                    best_odds[name] = price

        # Compare each selection
        for selection in [home, away, "Draw"]:
            b365_price = b365_odds.get(selection)
            market_best = best_odds.get(selection)
            if not b365_price or not market_best or b365_price <= 1 or market_best <= 1:
                continue

            edge = (b365_price - market_best) / market_best * 100
            if edge >= min_edge:
                rows.append({
                    "Match": f"{home} vs {away}",
                    "Start": commence,
                    "Selection": selection,
                    "bet365 Odds": round(b365_price, 2),
                    "Best Elsewhere": round(market_best, 2),
                    "Edge %": round(edge, 1),
                    "Bookmakers compared": len(others),
                })

    if rows:
        df = pd.DataFrame(rows).sort_values("Edge %", ascending=False)
        st.success(f"Found {len(df)} selections where bet365 is ≥ {min_edge}% better")
        st.dataframe(df, hide_index=True, use_container_width=True, height=500)
    else:
        st.info("No selections found where bet365 beats the wider market by the chosen margin.")

else:
    st.info("Enter your API key and click **Scan bet365 vs market**.")

st.markdown("---")
st.caption("Compares bet365 h2h odds against the best available price from other bookmakers in the same region. Higher Edge % = bet365 is offering more value than the rest of the market.")
