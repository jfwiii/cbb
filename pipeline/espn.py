"""ESPN site API (D1 cross-check / enrichment: neutral site, broadcast)."""
from __future__ import annotations

import datetime as dt

from . import config
from .crosswalk import norm_names
from .fetch import Fetcher
from .ncaa import to_int


def scoreboard(f: Fetcher, d: dt.date) -> list[dict] | None:
    status, body, _ = f.get_json(config.ESPN_SCOREBOARD.format(ymd=f"{d:%Y%m%d}"))
    if status != 200 or not body:
        return None
    out = []
    for ev in body.get("events", []):
        comp = (ev.get("competitions") or [{}])[0]
        teams = []
        for c in comp.get("competitors", []):
            t = c.get("team", {})
            teams.append({
                "espn_id": str(t.get("id")),
                "home": c.get("homeAway") == "home",
                "score": to_int(c.get("score")),
                "display": t.get("displayName", ""),
                "names": norm_names(t.get("location", ""), t.get("shortDisplayName", ""),
                                    t.get("displayName", "").removesuffix(" " + t.get("name", ""))),
            })
        if len(teams) != 2:
            continue
        nets = []
        for b in comp.get("broadcasts") or []:
            nets += b.get("names") or []
        out.append({
            "espn_id": str(ev.get("id")),
            "final": comp.get("status", {}).get("type", {}).get("state") == "post",
            "neutral": bool(comp.get("neutralSite")),
            "broadcast": ", ".join(dict.fromkeys(nets)) or None,
            "teams": teams,
        })
    return out
