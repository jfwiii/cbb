"""Settings shared across the pipeline. Everything tunable lives here."""
from __future__ import annotations

import datetime as dt
import os
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DIVISIONS = ("d1", "d2", "d3")

# Self-hosted henrygd/ncaa-api container in Actions; the public demo works for local runs.
NCAA_API_BASE = os.environ.get("NCAA_API_BASE", "https://ncaa-api.henrygd.me").rstrip("/")
ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/scoreboard?groups=50&limit=500&dates={ymd}"
)
# ESPN's edge returns 403 for user agents containing a URL, so keep it plain.
USER_AGENT = "jfwiii-cbb-pipeline/1.0"
RATE_PER_HOST = float(os.environ.get("CBB_RATE", "2"))  # DESIGN: 2 requests/second per host

REFETCH_DAYS = 3          # each nightly run re-fetches the previous 3 days
SCHEDULE_AHEAD_DAYS = 7   # daily refresh of the coming week
COVERAGE_FAIL_BELOW = 0.90  # DESIGN: coverage below 90% in any division fails the run
COVERAGE_MIN_GAMES = 10     # ...only judged when a division had at least this many finals
FT_POSS_COEF = 0.475        # possessions = FGA - OREB + TOV + 0.475*FTA


def season_for(d: dt.date) -> int:
    """season_id 2027 = 2026-27."""
    return d.year + 1 if d.month >= 7 else d.year


def season_bounds(season_id: int) -> tuple[dt.date, dt.date]:
    # D2 can open in late October (e.g. Oct 30-31, 2026); D1 opens in early November.
    return dt.date(season_id - 1, 10, 15), dt.date(season_id, 4, 10)


def today_et() -> dt.date:
    return dt.datetime.now(ET).date()
