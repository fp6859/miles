import streamlit as st
import requests
import time
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from bs4 import BeautifulSoup
import pandas as pd

st.set_page_config(page_title="Odds Movement Tracker", page_icon="📉", layout="wide")
st.title("📉 Odds Movement Tracker")
st.caption("Pulls the 1X2 odds shown on the Flashscore homepage list and tracks shortening since last check.")

SPORT_PAGES = {
    "Football (Soccer)": "https://www.flashscore.com/football/",
    "Tennis": "https://www.flashscore.com/tennis/",
    "Basketball": "https://www.flashscore.com/basketball/",
    "Ice Hockey": "https://www.flashscore.com/hockey/",
    "American Football": "https://www.flashscore.com/american-football/",
    "Baseball": "https://www.flashscore.com/baseball/",
    "Handball": "https://www.flashscore.com/handball/",
    "Rugby Union": "https://www.flashscore.com/rugby-union/",
    "Volleyball": "https://www.flashscore.com/volleyball/",
}

CACHE_FILE = Path("odds_cache.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-GB,en;q=0.9",
    "Referer": "https://www.flashscore.com/",
}

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

def scrape_homepage_odds(sport_url: str) -> list[dict]:
    """Scrape the main sport page for the visible 1X2 odds shown in the list."""
    try:
        resp = requests.get(sport_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        st.error(f"Failed to load page: {e}")
        return []

    matches = []
    # Flashscore match rows typically contain team names and odds cells
    for row in soup.select("div.event__match, div[class*='event__match']"):
        try:
            home = row.select_one(".event__participant--home, [class*='participant--home']")
            away = row.select_one(".event__participant--away, [class*='participant--away']")
            if not home or not away:
                continue
            home_name = home.get_text(strip=True)
            away_name = away.get_text(strip=True)

            # Odds are usually in cells with class containing "odds"
            odds_cells = row.select("[class*='odds'], .oddsCell, [data-odd]")
            odds_values = []
            for cell in odds_cells:
                text = cell.get_text(strip=True)
                # Extract decimal numbers
                found = re.findall(r"\d+\.\d+", text)
                for f in found:
                    try:
                        val = float(f)
                        if 1.01 <= val <= 50:
                            odds_values.append(val)
                    except ValueError:
                        pass

            # Also check data attributes
            for el in row.select("[data-odd], [data-odds]"):
                val = el.get("data-odd") or el.get("data-odds")
                if val:
                    try:
                        v = float(val)
                        if 1.01 <= v <= 50:
                            odds_values.append(v)
                    except ValueError:
                        pass

            if len(odds_values) >= 2:
                # Deduplicate while keeping order
                seen = set()
                unique = []
                for v in odds_values:
                    if v not in seen:
                        seen.add(v)
                        unique.append(v)

                match = {
                    "home": home_name,
                    "away": away_name,
                    "home_odds": unique[0] if len(unique) > 0 else None,
                    "draw_odds": unique[1] if len(unique) > 2 else None,
                    "away_odds": unique[-1] if len(unique) > 1 else None,
                    "event_id": row.get("id", "") or f"{home_name}-{away_name}",
                }
                # Clean event id
                if match["event_id"].startswith("g_1_"):
                    match["event_id"] = match["event_id"].replace("g_1_", "")
                matches.append(match)
        except Exception:
            continue

    return matches

# ── Sidebar ──────────────────────────────────────────────────
with st.sidebar:
    st.header("Settings")
    sport_name = st.selectbox("Sport", list(SPORT_PAGES.keys()))
    threshold = st.slider("Minimum shortening since last check (%)", 1.0, 20.0, 5.0, 0.5)
    delay = st.slider("Delay (seconds)", 0.5, 3.0, 1.5, 0.1)
    clear_cache = st.button("Clear stored odds", use_container_width=True)
    run_button = st.button("🔄 Check homepage odds", type="primary", use_container_width=True)

if clear_cache:
    st.session_state.odds_cache = {}
    if CACHE_FILE.exists():
        CACHE_FILE.unlink(missing_ok=True)
    st.success("Stored odds cleared.")

# ── Main ─────────────────────────────────────────────────────
cache = load_cache()

if run_button:
    url = SPORT_PAGES[sport_name]
    with st.spinner(f"Loading {sport_name} homepage..."):
        matches = scrape_homepage_odds(url)

    if not matches:
        st.warning("No matches with odds found on the page. The page structure may have changed.")
        st.stop()

    st.info(f"Found {len(matches)} matches with visible odds.")

    movements = []
    all_rows = []
    new_cache = {}

    for m in matches:
        key = m["event_id"] or f"{m['home']}|{m['away']}"
        new_cache[key] = {
            "home": m["home_odds"],
            "draw": m["draw_odds"],
            "away": m["away_odds"],
            "updated": datetime.now(timezone.utc).isoformat(),
            "match": f"{m['home']} vs {m['away']}",
        }

        row = {
            "Home": m["home"],
            "Away": m["away"],
            "Home Odds": m["home_odds"],
            "Draw Odds": m["draw_odds"],
            "Away Odds": m["away_odds"],
        }
        all_rows.append(row)

        previous = cache.get(key)
        if previous:
            drops = []
            for side, curr in [("home", m["home_odds"]), ("draw", m["draw_odds"]), ("away", m["away_odds"])]:
                old = previous.get(side)
                if old and curr:
                    pct = percentage_drop(old, curr)
                    if pct >= threshold:
                        drops.append(f"{side.title()} {old} → {curr} ({pct}%)")
            if drops:
                row["Movement"] = " | ".join(drops)
                movements.append(row)

        time.sleep(delay)

    cache.update(new_cache)
    save_cache(cache)

    st.subheader("Current Homepage Odds")
    if all_rows:
        st.dataframe(pd.DataFrame(all_rows), hide_index=True, use_container_width=True, height=450)

    if movements:
        st.success(f"**{len(movements)}** games shortened ≥ {threshold}% since last check")
        st.dataframe(pd.DataFrame(movements), hide_index=True, use_container_width=True)
    else:
        if any(cache):
            st.info("No significant shortening since the last check.")
        else:
            st.success("First snapshot saved from homepage odds. Run again later to detect movement.")

else:
    if cache:
        st.info(f"Stored snapshot for **{len(cache)}** matches. Click the button to refresh from the homepage.")
    else:
        st.info("No snapshot yet. Click **Check homepage odds** to capture the current list.")

st.markdown("---")
st.caption("Odds are scraped from the main sport page (the same view you showed). Movement is calculated against the previous snapshot of those odds.")
