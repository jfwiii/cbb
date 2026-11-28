"""Watch Score and style tags (DESIGN.md, "Watch Score").

    W = 100 * (0.35 * C * Q^1.5 + 0.35 * X + 0.30 * S)

C: closeness, 1 - 2|p - 0.5| from the pregame win probability.
Q: quality, geometric mean of the two teams' AdjEM percentiles within the division.
X: style extremes, the largest |z| among the matchup's style stats (0 at 1 sd, 1 at 3 sd),
   plus 0.15 for a notable pairing (strength vs strength or a mismatch).
S: stakes. Phase 2 measures the swing in conference regular-season title odds between a
   win and a loss (Monte Carlo of the remaining conference schedule); NCAA-bid and seeding
   stakes join in Phase 4 with the full season simulation. Nonconference games score 0.

Only games between two teams in the same division get a score; no team is boosted.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import math
import sqlite3

import numpy as np

from . import config
from .ratings import MODEL_VERSION, load_fit, predict

W_CQ, W_X, W_S, GAMMA = 0.35, 0.35, 0.30, 1.5
PAIRING_BONUS = 0.15
S_FULL_SWING = 0.25      # a 25-point swing in title odds scores S = 1
SIMS = 4000
FACTOR_NAME = {"efg": "shooting", "tov": "turnovers", "orb": "the glass", "ftr": "the line"}


def _pct(values: dict, higher_better=True) -> dict:
    """Percentile in [0, 1] (1 = best) within the given population."""
    items = sorted(values.items(), key=lambda kv: kv[1], reverse=not higher_better)
    n = len(items)
    return {k: (i / (n - 1) if n > 1 else 0.5) for i, (k, _) in enumerate(items)}


def _z(values: dict) -> dict:
    a = np.array(list(values.values()))
    mu, sd = a.mean(), a.std() or 1.0
    return {k: (v - mu) / sd for k, v in values.items()}


def style_profiles(fit: dict, fac: dict) -> dict:
    """Per-team style percentiles (goodness, 1 = best) and z-scores used by X and tags."""
    teams = [t for t in fit["teams"] if t in fac and fac[t]["adjusted"]["off"]]
    if len(teams) < 10:
        return {}
    get = lambda side, f: {t: fac[t]["adjusted"][side][f] for t in teams}  # noqa: E731
    raw = {
        "tempo": {t: fit["teams"][t]["adj_t"] for t in teams},
        "adj_d": {t: fit["teams"][t]["adj_d"] for t in teams},
        "off_efg": get("off", "efg"), "def_efg": get("def", "efg"),
        "off_tov": get("off", "tov"), "def_tov": get("def", "tov"),
        "off_orb": get("off", "orb"), "def_orb": get("def", "orb"),
        "off_ftr": get("off", "ftr"), "def_ftr": get("def", "ftr"),
        "tpar": get("off", "tpar"),
    }
    good = {  # goodness percentiles
        "off_efg": _pct(raw["off_efg"]), "def_efg": _pct(raw["def_efg"], False),
        "off_tov": _pct(raw["off_tov"], False), "def_tov": _pct(raw["def_tov"]),
        "off_orb": _pct(raw["off_orb"]), "def_orb": _pct(raw["def_orb"], False),
        "off_ftr": _pct(raw["off_ftr"]), "def_ftr": _pct(raw["def_ftr"], False),
        "tempo": _pct(raw["tempo"]),   # "top" = fastest
    }
    zs = {k: _z(raw[k]) for k in ("tempo", "off_efg", "def_efg", "def_tov", "off_orb", "tpar",
                                  "adj_d")}
    em_pct = _pct({t: fit["teams"][t]["adj_em"] for t in fit["teams"]})
    return {"good": good, "z": zs, "em_pct": em_pct}


def tags_for(a: int, b: int, prof: dict, names: dict) -> tuple[list[dict], bool]:
    g = prof["good"]
    top = lambda k, t: g[k].get(t, 0.5) >= 0.9      # noqa: E731
    bot = lambda k, t: g[k].get(t, 0.5) <= 0.1      # noqa: E731
    tags = []
    both = lambda k, fn: fn(k, a) and fn(k, b)       # noqa: E731
    if both("tempo", top):
        tags.append({"kind": "shared", "name": "Track Meet", "factor": "tempo"})
    if both("tempo", bot):
        tags.append({"kind": "shared", "name": "Rock Fight", "factor": "tempo"})
    if both("off_efg", top):
        tags.append({"kind": "shared", "name": "Shooting Gallery", "factor": "shooting"})
    if both("off_orb", top):
        tags.append({"kind": "shared", "name": "Glass War", "factor": "the glass"})
    if both("def_tov", top):
        tags.append({"kind": "shared", "name": "Turnover Chaos", "factor": "turnovers"})
    pairing = False
    for o, d in ((a, b), (b, a)):
        for f in ("efg", "tov", "orb", "ftr"):
            if top("off_" + f, o) and top("def_" + f, d):
                tags.append({"kind": "strength", "factor": FACTOR_NAME[f],
                             "name": f"Unstoppable vs. Immovable: {FACTOR_NAME[f]}",
                             "teams": [names.get(o), names.get(d)]})
                pairing = True
        mismatch = [("efg", "Open Season"), ("orb", "Second-Chance Feast"),
                    ("ftr", "Parade to the Line")]
        for f, label in mismatch:
            if top("off_" + f, o) and bot("def_" + f, d):
                tags.append({"kind": "mismatch", "name": label, "factor": FACTOR_NAME[f],
                             "teams": [names.get(o), names.get(d)]})
                pairing = True
        if bot("off_tov", o) and top("def_tov", d):
            tags.append({"kind": "mismatch", "name": "Pickpocket Alert", "factor": "turnovers",
                         "teams": [names.get(o), names.get(d)]})
            pairing = True
    if (top("tempo", a) and bot("tempo", b)) or (top("tempo", b) and bot("tempo", a)):
        tags.append({"kind": "mismatch", "name": "Tempo Tug-of-War", "factor": "tempo"})
        pairing = True
    # dedupe by name+teams
    seen, out = set(), []
    for t in tags:
        key = (t["name"], tuple(t.get("teams") or ()))
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out, pairing


def x_component(a: int, b: int, prof: dict, pairing: bool) -> float:
    zmax = max((abs(z.get(t, 0.0)) for z in prof["z"].values() for t in (a, b)), default=0.0)
    x = min(1.0, max(0.0, (zmax - 1) / 2))
    return min(1.0, x + (PAIRING_BONUS if pairing else 0.0))


# --------------------------------------------------------------------------- #
# Stakes: conference regular-season title odds swing
# --------------------------------------------------------------------------- #
def title_stakes(conn, sid: int, div: str, fit: dict, horizon: set[int], rng) -> dict[int, float]:
    """{game_id: max over the two teams of |P(title | win) - P(title | loss)|} for upcoming
    conference games in `horizon`."""
    members = collections.defaultdict(list)
    for r in conn.execute("SELECT team_id, conf_id FROM team_seasons WHERE season_id=? AND "
                          "division=? AND conf_id IS NOT NULL", (sid, div)):
        if "independent" not in r["conf_id"]:
            members[r["conf_id"]].append(r["team_id"])
    conf_of = {t: c for c, ts in members.items() for t in ts}
    wins = collections.Counter()
    remaining = collections.defaultdict(list)
    for g in conn.execute(
            """SELECT game_id, home_id, away_id, status, home_score, away_score,
                      COALESCE(neutral,0) neutral FROM games
               WHERE season_id=? AND is_conference=1 AND status IN ('final','scheduled')""",
            (sid,)):
        c = conf_of.get(g["home_id"])
        if c is None or conf_of.get(g["away_id"]) != c:
            continue
        if g["status"] == "final":
            wins[g["home_id" if g["home_score"] > g["away_score"] else "away_id"]] += 1
        else:
            remaining[c].append(g)
    played = collections.Counter()
    for c, games in remaining.items():
        for g in games:
            played[g["home_id"]] += 1
            played[g["away_id"]] += 1
    finals_per_team = collections.Counter()
    for g in conn.execute("SELECT home_id, away_id FROM games WHERE season_id=? AND "
                          "is_conference=1 AND status='final'", (sid,)):
        finals_per_team[g["home_id"]] += 1
        finals_per_team[g["away_id"]] += 1
    out = {}
    for c, games in remaining.items():
        if not any(g["game_id"] in horizon for g in games):
            continue
        teams = members[c]
        # Only trust the simulation once the known conference schedule is at least a full
        # round robin (otherwise one listed game looks like it decides the title).
        known = np.mean([played[t] + finals_per_team[t] for t in teams])
        if len(teams) < 3 or known < len(teams) - 1:
            continue
        ti = {t: i for i, t in enumerate(teams)}
        probs = []
        for g in games:
            p = predict(fit, g["home_id"], g["away_id"], bool(g["neutral"]))
            probs.append(p["home_win_prob"] if p else 0.5)
        probs = np.array(probs)
        home_wins = rng.random((SIMS, len(games))) < probs           # (S, G)
        W = np.tile(np.array([wins[t] for t in teams], dtype=float), (SIMS, 1))
        for j, g in enumerate(games):
            W[:, ti[g["home_id"]]] += home_wins[:, j]
            W[:, ti[g["away_id"]]] += ~home_wins[:, j]
        best = W.max(axis=1, keepdims=True)
        share = (W == best) / (W == best).sum(axis=1, keepdims=True)  # ties split the title
        for j, g in enumerate(games):
            if g["game_id"] not in horizon:
                continue
            hw = home_wins[:, j]
            if hw.all() or (~hw).all():
                continue
            deltas = []
            for t, won in ((g["home_id"], hw), (g["away_id"], ~hw)):
                i = ti[t]
                deltas.append(abs(share[won, i].mean() - share[~won, i].mean()))
            out[g["game_id"]] = max(deltas)
    return out


# --------------------------------------------------------------------------- #
def run(conn: sqlite3.Connection, sid: int, factor_result: dict, days_back: int = 3,
        days_ahead: int = 7, log=print) -> int:
    today = config.today_et()
    lo, hi = (today - dt.timedelta(days=days_back)).isoformat(), \
        (today + dt.timedelta(days=days_ahead)).isoformat()
    names = dict(conn.execute("SELECT team_id, name FROM teams").fetchall())
    rng = np.random.default_rng(20261128)
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    n = 0
    for div in config.DIVISIONS:
        ds = conn.execute("SELECT MAX(date) FROM model_meta WHERE season_id=? AND division=? "
                          "AND model_version=?", (sid, div, MODEL_VERSION)).fetchone()[0]
        if not ds or div not in factor_result:
            continue
        fit = load_fit(conn, sid, div, ds)
        prof = style_profiles(fit, factor_result[div]["teams"])
        if not prof:
            continue
        games = conn.execute(
            """SELECT g.game_id, g.home_id, g.away_id, g.is_conference, p.home_win_prob
               FROM games g
               JOIN team_seasons th ON th.team_id=g.home_id AND th.season_id=g.season_id
               JOIN team_seasons ta ON ta.team_id=g.away_id AND ta.season_id=g.season_id
               LEFT JOIN predictions p ON p.game_id=g.game_id AND p.model_version=?
               WHERE g.season_id=? AND th.division=? AND ta.division=? AND g.date BETWEEN ? AND ?
                 AND g.status NOT IN ('missing','canceled')""",
            (MODEL_VERSION, sid, div, div, lo, hi)).fetchall()
        stakes = title_stakes(conn, sid, div, fit, {g["game_id"] for g in games}, rng)
        for g in games:
            a, b = g["home_id"], g["away_id"]
            if a not in fit["teams"] or b not in fit["teams"] or g["home_win_prob"] is None:
                continue
            p = g["home_win_prob"]
            c = 1 - 2 * abs(p - 0.5)
            q = math.sqrt(prof["em_pct"].get(a, 0) * prof["em_pct"].get(b, 0))
            tags, pairing = tags_for(a, b, prof, names)
            x = x_component(a, b, prof, pairing)
            s_raw = stakes.get(g["game_id"], 0.0)
            s = min(1.0, s_raw / S_FULL_SWING)
            score = 100 * (W_CQ * c * q ** GAMMA + W_X * x + W_S * s)
            comps = {"close_good": round(c * q ** GAMMA, 3), "c": round(c, 3), "q": round(q, 3),
                     "style": round(x, 3), "stakes": round(s, 3), "title_swing": round(s_raw, 3)}
            conn.execute("INSERT OR REPLACE INTO watch_scores (game_id, score, components, tags, "
                         "computed_at) VALUES (?,?,?,?,?)",
                         (g["game_id"], round(score, 1), json.dumps(comps), json.dumps(tags), now))
            n += 1
    conn.commit()
    log(f"watch scores: {n} games ({lo} .. {hi})")
    return n
