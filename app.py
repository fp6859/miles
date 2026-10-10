import streamlit as st
import requests
import pandas as pd
from datetime import datetime

st.set_page_config(page_title="Value Scanner", page_icon="💰", layout="wide")
st.title("💰 Value Scanner")
st.caption("Current market odds vs simple model baseline → sorted by highest value")

# ── API key (sidebar or secrets) ─────────────────────────────
API_KEY = st.secrets.get("ODDS_API_KEY", "")
if not API_KEY:
    API_KEY = st.sidebar.text_input("Odds API Key", type="password",
                                    help="Free key from the-odds-api.com")

SPORTS = {
    "Football (EPL)": "soccer_epl",
    "Football (La Liga)": "soccer_spain_la_liga",
    "Football (Bundesliga)": "soccer_germany_bundesliga",
    "Football (Serie A)": "soccer_italy_serie_a",
    "American Football (NFL)": "americanfootball_nfl",
    "Basketball (NBA)": "basketball_nba",
    "Ice Hockey (NHL)": "icehockey_nhl",
    "Baseball (MLB)": "baseball_mlb",
    "Tennis (ATP)": "tennis_atp_wimbledon",  # adjust tournament as needed
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

def simple_model_prob(home: str, away: str) -> dict:
    """
    Very basic placeholder model.
    Replace this with a real model (Elo, Poisson, ratings, etc.) later.
    Currently returns a mild home advantage + slight randomness so it isn't flat.
    """
    # Mild home advantage baseline
    base_home = 0.42
    base_draw = 0.28
    base_away = 0.30
    # Tiny name-based tweak so different matches aren't identical
    tweak = (hash(home + away) % 100) / 1000.0
    home_p = min(0.75, max(0.15, base_home + tweak))
    away_p = min(0.75, max(0.15, base_away - tweak))
    draw_p = max(0.05, 1.0 - home_p - away_p)
    total = home_p + draw_p + away_p
    return {
        "home": home_p / total,
        "draw": draw_p / total,
        "away": away_p / total,
    }

def implied_prob(odds: float) -> float:
    return 1.0 / odds if odds and odds > 1 else 0.0

# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.header("Settings")
    if not API_KEY:
        st.warning("Enter your free Odds API key")
    sport_name = st.selectbox("Sport / League", list(SPORTS.keys()))
    regions = st.selectbox("Region", ["uk", "eu", "us", "au"], index=0)
    min_value = st.slider("Minimum value (%)", 0.0, 30.0, 5.0, 0.5)
    run = st.button("🔍 Scan for value", type="primary", use_container_width=True)

# ── Main ─────────────────────────────────────────────────────
if run and API_KEY:
    sport_key = SPORTS[sport_name]
    with st.spinner("Fetching current odds..."):
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
        if not bookmakers:
            continue

        # Use the first bookmaker for simplicity (or you can average later)
        bm = bookmakers[0]
        market = next((m for m in bm.get("markets", []) if m["key"] == "h2h"), None)
        if not market:
            continue

        outcomes = {o["name"]: o["price"] for o in market.get("outcomes", [])}
        home_odds = outcomes.get(home)
        away_odds = outcomes.get(away)
        draw_odds = outcomes.get("Draw")

        model = simple_model_prob(home, away)

        for side, odds, model_p, label in [
            ("home", home_odds, model["home"], home),
            ("draw", draw_odds, model["draw"], "Draw"),
            ("away", away_odds, model["away"], away),
        ]:
            if not odds or odds <= 1:
                continue
            market_p = implied_prob(odds)
            edge = (model_p - market_p) / market_p * 100 if market_p else 0
            if edge >= min_value:
                rows.append({
                    "Match": f"{home} vs {away}",
                    "Start": commence,
                    "Selection": label,
                    "Market Odds": round(odds, 2),
                    "Market Prob": f"{market_p*100:.1f}%",
                    "Model Prob": f"{model_p*100:.1f}%",
                    "Value %": round(edge, 1),
                    "Bookmaker": bm.get("title", ""),
                })

    if rows:
        df = pd.DataFrame(rows).sort_values("Value %", ascending=False)
        st.success(f"Found {len(df)} value selections ≥ {min_value}%")
        st.dataframe(df, hide_index=True, use_container_width=True, height=500)
    else:
        st.info("No selections above the value threshold with the current model.")

else:
    st.info("Enter your API key and click **Scan for value**.")

st.markdown("---")
st.caption("""
**Model note:** The baseline model is a simple placeholder (mild home advantage).  
Replace the `simple_model_prob()` function with a real model (Elo, Poisson, ratings, etc.) for meaningful value.
""")
