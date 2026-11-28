"""Four factors: raw splits, schedule-adjusted values, per-division regression weights, and
the "where the margin comes from" contributions (DESIGN.md, "Four factors",
"Schedule-adjusted factors", "Team page").

Factor definitions (offense; defense = the same stats for opponents):
  eFG% = (FGM + 0.5*3PM) / FGA      TOV% = TOV / Poss
  ORB% = OREB / (OREB + opp DREB)   FTr  = FTM / FGA
"""
from __future__ import annotations

import collections
import json
import sqlite3

import numpy as np

from . import config
from .ratings import MODEL_VERSION, P, _solve

FACTORS = ("efg", "tov", "orb", "ftr")
# Extra stats shown on team pages (shooting and style strip)
EXTRA = ("tpar", "tp", "twop", "ft", "ast", "blk", "stl")


def _rates(o: dict, d: dict) -> dict:
    """Factor values for one side given its counting stats `o` and the opponent's `d`."""
    def r(x, y):
        return 100 * x / y if y else None
    return {
        "efg": r(o["fgm"] + 0.5 * o["tpm"], o["fga"]),
        "tov": r(o["tov"], o["poss"]),
        "orb": r(o["oreb"], o["oreb"] + d["dreb"]),
        "ftr": r(o["ftm"], o["fga"]),
        "tpar": r(o["tpa"], o["fga"]),
        "tp": r(o["tpm"], o["tpa"]),
        "twop": r(o["fgm"] - o["tpm"], o["fga"] - o["tpa"]),
        "ft": r(o["ftm"], o["fta"]),
        "ast": r(o["ast"], o["fgm"]),
        "blk": r(o["blk"], d["fga"] - d["tpa"]),   # blocks / opponent 2PA
        "stl": r(o["stl"], d["poss"]),             # steals / opponent possessions
        "eff": r(o["pts"], (o["poss"] + d["poss"]) / 2),
    }


STAT_KEYS = ("fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk",
             "tov", "pts", "poss")


def team_games(conn, sid: int, div: str) -> list[dict]:
    """In-division, validated games as (team, opp) rows with both sides' counting stats."""
    q = f"""
    SELECT g.game_id, g.date, g.home_id, g.is_conference, COALESCE(g.neutral,0) neutral,
           b.team_id, b.opp_id, {', '.join('b.' + k for k in STAT_KEYS)},
           {', '.join('o.' + k + ' AS o_' + k for k in STAT_KEYS)}
    FROM team_game_box b
    JOIN team_game_box o ON o.game_id = b.game_id AND o.team_id = b.opp_id
    JOIN games g ON g.game_id = b.game_id
    JOIN game_quality q ON q.game_id = g.game_id AND q.ok_for_ratings = 1
    JOIN team_seasons tb ON tb.team_id = b.team_id AND tb.season_id = g.season_id
    JOIN team_seasons tobj ON tobj.team_id = b.opp_id AND tobj.season_id = g.season_id
    WHERE g.season_id = ? AND tb.division = ? AND tobj.division = ?
    ORDER BY g.date, g.game_id"""
    rows = []
    for r in conn.execute(q, (sid, div, div)):
        rows.append({"game_id": r["game_id"], "date": r["date"], "team": r["team_id"],
                     "opp": r["opp_id"], "conf": r["is_conference"],
                     "venue": 0 if r["neutral"] else (1 if r["home_id"] == r["team_id"] else -1),
                     "o": {k: r[k] or 0 for k in STAT_KEYS},
                     "d": {k: r["o_" + k] or 0 for k in STAT_KEYS}})
    return rows


def aggregate(rows: list[dict]) -> dict:
    o, d = collections.Counter(), collections.Counter()
    for r in rows:
        o.update(r["o"])
        d.update(r["d"])
    off = _rates(o, d)
    de = _rates(d, o)
    return {"g": len(rows), "off": off, "def": de,
            "tempo": None if not rows else round(
                sum((r["o"]["poss"] + r["d"]["poss"]) / 2 for r in rows) / len(rows), 1)}


def weights(rows: list[dict]) -> dict:
    """Regress game offensive efficiency (pts/100) on the four factors (offense side)."""
    X, y = [], []
    for r in rows:
        v = _rates(r["o"], r["d"])
        if None in (v["efg"], v["tov"], v["orb"], v["ftr"], v["eff"]):
            continue
        X.append([1, v["efg"], v["tov"], v["orb"], v["ftr"]])
        y.append(v["eff"])
    if len(y) < 30:
        return {}
    coef, *_ = np.linalg.lstsq(np.array(X), np.array(y), rcond=None)
    return {f: float(c) for f, c in zip(("intercept",) + FACTORS, coef)}


def adjusted(rows: list[dict], teams: list[int]) -> dict:
    """Schedule-adjusted factor values via the same ridge model as the ratings:
    value = mu + Off[team] + Def[opp] + h*venue."""
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    out = {t: {"off": {}, "def": {}} for t in teams}
    for f in FACTORS + ("tpar",):
        ri, rv, y, w = [], [], [], []
        for r in rows:
            v = _rates(r["o"], r["d"])[f]
            if v is None or r["team"] not in idx or r["opp"] not in idx:
                continue
            ri.append([0, 1, 2 + idx[r["team"]], 2 + n + idx[r["opp"]]])
            rv.append([1, r["venue"], 1, 1])
            y.append(v)
            w.append(1.0)
        if len(y) < 20:
            continue
        coef, _, _ = _solve(2 + 2 * n, np.array(ri), np.array(rv, dtype=float), np.array(y),
                            np.array(w), np.arange(2, 2 + 2 * n), P.lam, np.zeros(2 + 2 * n))
        mu = coef[0]
        for t, i in idx.items():
            out[t]["off"][f] = float(mu + coef[2 + i])
            out[t]["def"][f] = float(mu + coef[2 + n + i])
    return out


def run(conn: sqlite3.Connection, sid: int, log=print) -> dict:
    """Compute factor tables for every division of a season and store them in
    team_factors_daily (date = latest fit date). Returns {div: {"weights":..., "teams":...}}."""
    result = {}
    for div in config.DIVISIONS:
        ds = conn.execute("SELECT MAX(date) FROM model_meta WHERE season_id=? AND division=? AND "
                          "model_version=?", (sid, div, MODEL_VERSION)).fetchone()[0]
        if not ds:
            continue
        rows = team_games(conn, sid, div)
        teams = sorted({r["team"] for r in rows})
        by_team = collections.defaultdict(list)
        for r in rows:
            by_team[r["team"]].append(r)
        w = weights(rows)
        adj = adjusted(rows, teams)
        avg = aggregate(rows)  # division average (offense == defense overall)
        conn.execute("UPDATE model_meta SET factor_weights=? WHERE season_id=? AND division=? "
                     "AND date=? AND model_version=?",
                     (json.dumps({"weights": w, "avg": avg["off"]}), sid, div, ds, MODEL_VERSION))
        teams_out = {}
        for t in teams:
            g = by_team[t]
            splits = {"season": aggregate(g), "last10": aggregate(g[-10:]),
                      "conference": aggregate([r for r in g if r["conf"]])}
            contrib = {}
            if w and adj[t]["off"]:
                for f in FACTORS:
                    base = avg["off"][f]
                    contrib["off_" + f] = (adj[t]["off"][f] - base) * w[f]
                    contrib["def_" + f] = -(adj[t]["def"][f] - base) * w[f]
            teams_out[t] = {"splits": splits, "adjusted": adj[t], "contrib": contrib}
            conn.execute("INSERT OR REPLACE INTO team_factors_daily (team_id, date, split, data) "
                         "VALUES (?,?,?,?)", (t, ds, "all", json.dumps(teams_out[t])))
        result[div] = {"date": ds, "weights": w, "avg": avg["off"], "teams": teams_out}
        if w:
            log(f"factors {sid} {div}: weights eFG {w['efg']:.2f} TOV {w['tov']:.2f} "
                f"ORB {w['orb']:.2f} FTr {w['ftr']:.2f} (pts/100 per point)")
    conn.commit()
    return result
