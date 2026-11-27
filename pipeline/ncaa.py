"""ncaa.com endpoints via the henrygd/ncaa-api route pattern, plus pure parsers."""
from __future__ import annotations

import datetime as dt
import re

from . import config
from .fetch import Fetcher


class NcaaClient:
    def __init__(self, fetcher: Fetcher, base: str = config.NCAA_API_BASE):
        self.f = fetcher
        self.base = base.rstrip("/")

    def _get(self, path: str):
        return self.f.get_json(f"{self.base}{path}")

    def scoreboard(self, div: str, d: dt.date):
        """Returns (status, [raw game dicts]). 404 = no games that day."""
        status, body, _ = self._get(f"/scoreboard/basketball-men/{div}/{d:%Y/%m/%d}/all-conf")
        games = [g["game"] for g in (body or {}).get("games", [])] if status == 200 else []
        return status, games

    def schedule_month(self, div: str, year: int, month: int) -> dict[str, int] | None:
        """{'YYYY-MM-DD': game count} for a month, or None on failure."""
        status, body, _ = self._get(f"/schedule/basketball-men/{div}/{year}/{month:02d}")
        if status != 200 or not body:
            return None
        out = {}
        for gd in body.get("gameDates", []):
            m, d, y = gd["contest_date"].split("-")
            out[f"{y}-{m}-{d}"] = int(gd.get("games") or 0)
        return out

    def contest(self, gid: str):
        status, body, _ = self._get(f"/game/{gid}")
        c = (body or {}).get("contests") or []
        return status, (c[0] if c else None)

    def boxscore(self, gid: str):
        status, body, _ = self._get(f"/game/{gid}/boxscore")
        return status, body

    def pbp(self, gid: str):
        status, body, raw = self._get(f"/game/{gid}/play-by-play")
        return status, body, raw


# --------------------------------------------------------------------------- #
# Parsers (pure functions, unit-tested)
# --------------------------------------------------------------------------- #
def to_int(x):
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


def parse_minutes(s) -> float | None:
    if s in (None, ""):
        return None
    s = str(s).strip()
    if ":" in s:
        m, sec = s.split(":", 1)
        mi, si = to_int(m), to_int(sec)
        return None if mi is None else mi + (si or 0) / 60
    try:
        return float(s)
    except ValueError:
        return None


def game_status(g: dict) -> str:
    msg = (g.get("finalMessage") or "").lower()
    state = (g.get("gameState") or "").lower()
    if "cancel" in msg:
        return "canceled"
    if "postpon" in msg or "ppd" in msg:
        return "postponed"
    return {"final": "final", "live": "live", "pre": "scheduled"}.get(state, state or "scheduled")


def conf_seo(side: dict) -> str:
    for c in side.get("conferences") or []:
        seo = c.get("conferenceSeo") or ""
        if seo and seo != "top-25":
            return seo
    return ""


def conf_name(side: dict) -> str:
    for c in side.get("conferences") or []:
        if (c.get("conferenceSeo") or "") not in ("", "top-25"):
            return c.get("conferenceName") or ""
    return ""


def tipoff_utc(g: dict) -> str | None:
    e = to_int(g.get("startTimeEpoch"))
    if not e:
        return None
    return dt.datetime.fromtimestamp(e, dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


STAT_MAP = {
    "fgm": "fieldGoalsMade", "fga": "fieldGoalsAttempted",
    "tpm": "threePointsMade", "tpa": "threePointsAttempted",
    "ftm": "freeThrowsMade", "fta": "freeThrowsAttempted",
    "oreb": "offensiveRebounds", "ast": "assists", "stl": "steals",
    "blk": "blockedShots", "tov": "turnovers", "pf": "personalFouls", "pts": "points",
}


def parse_stat_line(s: dict) -> dict:
    out = {k: to_int(s.get(v)) for k, v in STAT_MAP.items()}
    treb = to_int(s.get("totalRebounds"))
    out["dreb"] = None if treb is None or out["oreb"] is None else treb - out["oreb"]
    if out["pts"] is None and None not in (out["fgm"], out["tpm"], out["ftm"]):
        out["pts"] = 2 * out["fgm"] + out["tpm"] + out["ftm"]
    return out


def possessions(b: dict) -> float | None:
    if None in (b.get("fga"), b.get("oreb"), b.get("tov"), b.get("fta")):
        return None
    return b["fga"] - b["oreb"] + b["tov"] + config.FT_POSS_COEF * b["fta"]


def parse_boxscore(box: dict) -> dict[str, dict]:
    """{ncaa seo: {'team': stat line or None, 'players': [...]}} for teams with box data."""
    out = {}
    if not isinstance(box, dict):
        return out
    seo_by_id = {str(t.get("teamId")): t.get("seoname") for t in box.get("teams") or []}
    for tb in box.get("teamBoxscore") or []:
        seo = seo_by_id.get(str(tb.get("teamId")))
        if not seo:
            continue
        ts = tb.get("teamStats") or {}
        team = parse_stat_line(ts) if ts else None
        if team and not team.get("fga"):
            team = None
        players = []
        for p in tb.get("playerStats") or []:
            line = parse_stat_line(p)
            players.append({
                "first": (p.get("firstName") or "").strip(),
                "last": (p.get("lastName") or "").strip(),
                "jersey": str(p.get("number") or "").strip(),
                "position": (p.get("position") or "").strip(),
                "class": (p.get("year") or "").strip(),
                "starter": 1 if p.get("starter") else 0,
                "minutes": parse_minutes(p.get("minutesPlayed")),
                **line,
            })
        out[seo] = {"team": team, "players": players}
    return out


SUB_RE = re.compile(r"subbing (in|out)|substitution|\bsub (in|out)\b|enters the game|"
                    r"goes to the bench", re.I)


def pbp_summary(pbp: dict) -> dict:
    evs = [e for p in (pbp or {}).get("periods") or [] for e in p.get("playbyplayStats") or []]
    texts = (" ".join(filter(None, (e.get("homeText"), e.get("visitorText"),
                                    e.get("eventDescription")))) for e in evs)
    return {
        "events": len(evs),
        "periods": len((pbp or {}).get("periods") or []),
        "has_subs": any(SUB_RE.search(t) for t in texts) if evs else False,
    }


def name_key(first: str, last: str) -> str:
    return re.sub(r"[^a-z0-9]", "", f"{first}{last}".lower())
