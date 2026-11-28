"""Phase 2 site JSON (DESIGN.md, "Build approach"): everything the static site loads.

  ratings/{div}.json           current ratings table (+ 30-day AdjEM trend per team)
  ratings/{div}-history.json   ratings through every fitted date (as-of date picker)
  factors/{div}.json           four factors: season / last10 / conference raw, season adjusted,
                               ranks, percentiles; division regression weights
  teams/{slug}.json            team page: header, factors, contributions, schedule, players
  tonight/{date}.json          games for a date ranked by Watch Score (today-3 .. today+7)
  teams/index.json             slug -> name/division (route generation, search)
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import sqlite3
from pathlib import Path

from . import config
from .factors import FACTORS
from .players import season_players
from .ratings import MODEL_VERSION

LOGO = "https://www.ncaa.com/sites/default/files/images/logos/schools/bgl/{slug}.svg"
SMALL_SAMPLE = 5
LEADER_MIN_PCT = 40.0


def _w(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), default=_round))


def _round(x):
    raise TypeError(type(x))


def r1(x):
    return None if x is None else round(float(x), 1)


def r3(x):
    return None if x is None else round(float(x), 3)


def rank_pct(values: dict, higher_better: bool) -> dict:
    """{key: (rank, percentile 0-100 where 100 = best)} over non-null values."""
    items = [(k, v) for k, v in values.items() if v is not None]
    items.sort(key=lambda kv: kv[1], reverse=higher_better)
    n = len(items)
    out = {}
    for i, (k, _) in enumerate(items):
        out[k] = (i + 1, round(100 * (1 - i / (n - 1)), 1) if n > 1 else 50.0)
    return out


# Which direction is "good" for each stat (offense / defense)
GOOD_OFF = {"efg": True, "tov": False, "orb": True, "ftr": True, "eff": True, "tpar": None,
            "tp": True, "twop": True, "ft": True, "ast": True, "blk": True, "stl": True}
GOOD_DEF = {"efg": False, "tov": True, "orb": False, "ftr": False, "eff": False}


def build(conn: sqlite3.Connection, site: Path, sid: int, factor_result: dict) -> list[str]:
    files = []
    today = config.today_et()
    teams = {r["team_id"]: dict(r) for r in conn.execute(
        """SELECT t.team_id, t.slug, t.name, t.color, t.kind, ts.division, ts.conf_id,
                  c.name conf_name FROM teams t
           LEFT JOIN team_seasons ts ON ts.team_id=t.team_id AND ts.season_id=?
           LEFT JOIN conferences c ON c.conf_id=ts.conf_id AND c.season_id=ts.season_id""",
        (sid,))}
    rec = collections.defaultdict(lambda: [0, 0])
    conf_rec = collections.defaultdict(lambda: [0, 0])
    for g in conn.execute("SELECT home_id, away_id, home_score, away_score, is_conference "
                          "FROM games WHERE season_id=? AND status='final'", (sid,)):
        if g["home_score"] is None or g["home_score"] == g["away_score"]:
            continue
        w, l = (g["home_id"], g["away_id"]) if g["home_score"] > g["away_score"] else \
            (g["away_id"], g["home_id"])
        rec[w][0] += 1
        rec[l][1] += 1
        if g["is_conference"]:
            conf_rec[w][0] += 1
            conf_rec[l][1] += 1

    _w(site / "teams/index.json", sorted(
        [{"slug": t["slug"], "name": t["name"], "div": t["division"], "conf": t["conf_id"]}
         for t in teams.values() if t["division"] in config.DIVISIONS],
        key=lambda x: x["name"]))
    files.append("teams/index.json")

    players = season_players(conn, sid)
    meta_all = {}
    for div in config.DIVISIONS:
        meta = conn.execute("SELECT * FROM model_meta WHERE season_id=? AND division=? AND "
                            "model_version=? ORDER BY date DESC LIMIT 1",
                            (sid, div, MODEL_VERSION)).fetchone()
        if not meta:
            continue
        ds = meta["date"]
        meta_all[div] = meta
        fit_dates = [r[0] for r in conn.execute(
            "SELECT date FROM model_meta WHERE season_id=? AND division=? AND model_version=? "
            "ORDER BY date", (sid, div, MODEL_VERSION))]
        cur = {r["team_id"]: dict(r) for r in conn.execute(
            """SELECT r.* FROM ratings_daily r JOIN team_seasons ts ON ts.team_id=r.team_id
               AND ts.season_id=? AND ts.division=? WHERE r.date=? AND r.model_version=?""",
            (sid, div, ds, MODEL_VERSION))}
        hist = collections.defaultdict(dict)
        for r in conn.execute(
                f"""SELECT r.team_id, r.date, r.adj_em, r.adj_o, r.adj_d, r.adj_t, r.rank_em
                    FROM ratings_daily r WHERE r.model_version=? AND r.date IN
                    ({','.join('?' * len(fit_dates))})""", (MODEL_VERSION, *fit_dates)):
            if r["team_id"] in cur:
                hist[r["team_id"]][r["date"]] = r
        cutoff = (dt.date.fromisoformat(ds) - dt.timedelta(days=30)).isoformat()
        trend_dates = [d for d in fit_dates if d >= cutoff]

        # luck / sos ranks
        luck_rk = rank_pct({t: v["luck"] for t, v in cur.items()}, True)
        sos_rk = rank_pct({t: v["sos"] for t, v in cur.items()}, True)
        ncsos_rk = rank_pct({t: v["ncsos"] for t, v in cur.items()}, True)
        rows = []
        for t, v in sorted(cur.items(), key=lambda kv: kv[1]["rank_em"]):
            tm = teams[t]
            rows.append({
                "id": t, "slug": tm["slug"], "name": tm["name"], "conf": tm["conf_id"],
                "w": rec[t][0], "l": rec[t][1], "cw": conf_rec[t][0], "cl": conf_rec[t][1],
                "rk": v["rank_em"], "em": r1(v["adj_em"]), "o": r1(v["adj_o"]),
                "o_rk": v["rank_o"], "d": r1(v["adj_d"]), "d_rk": v["rank_d"],
                "t": r1(v["adj_t"]), "t_rk": v["rank_t"],
                "luck": r3(v["luck"]), "luck_rk": luck_rk.get(t, (None,))[0],
                "sos": r1(v["sos"]), "sos_rk": sos_rk.get(t, (None,))[0],
                "ncsos": r1(v["ncsos"]), "ncsos_rk": ncsos_rk.get(t, (None,))[0],
                "se": r1(v["se_em"]), "rk_lo": v["rank_lo"], "rk_hi": v["rank_hi"],
                "g": v["games"], "small": v["games"] < SMALL_SAMPLE,
                "trend": [r1(hist[t][d]["adj_em"]) if d in hist[t] else None for d in trend_dates],
            })
        _w(site / f"ratings/{div}.json", {
            "season": sid, "div": div, "asof": ds, "model_version": MODEL_VERSION, "beta": True,
            "mu": r1(meta["mu"]), "home": r1(meta["h"]), "sigma": r1(meta["sigma"]),
            "n_games": meta["n_games"], "trend_dates": trend_dates, "teams": rows})
        _w(site / f"ratings/{div}-history.json", {
            "dates": fit_dates,
            "teams": {str(t): {k: [r1(hist[t][d][k]) if d in hist[t] else None
                                   for d in fit_dates] for k in ("adj_em", "adj_o", "adj_d", "adj_t")}
                      for t in cur}})
        files += [f"ratings/{div}.json", f"ratings/{div}-history.json"]

        # ---- four factors ------------------------------------------------------
        fr = factor_result.get(div)
        if not fr:
            continue
        ft = fr["teams"]
        ranks = {}
        for split in ("season", "last10", "conference"):
            for side, good in (("off", GOOD_OFF), ("def", GOOD_DEF)):
                for f, hb in good.items():
                    if hb is None:
                        continue
                    ranks[(split, side, f)] = rank_pct(
                        {t: ft[t]["splits"][split][side].get(f) for t in ft
                         if ft[t]["splits"][split]["g"]}, hb if side == "off" else hb)
        for side, good in (("off", GOOD_OFF), ("def", GOOD_DEF)):
            for f in FACTORS:
                hb = good[f]
                ranks[("adjusted", side, f)] = rank_pct(
                    {t: ft[t]["adjusted"][side].get(f) for t in ft}, hb)
        tempo_rk = rank_pct({t: ft[t]["splits"]["season"]["tempo"] for t in ft}, True)

        def cell(split, side, f, t, val):
            rk = ranks.get((split, side, f), {}).get(t)
            return [r1(val), rk[0] if rk else None, rk[1] if rk else None]

        frows = []
        for t in sorted(ft, key=lambda x: teams[x]["name"]):
            out = {"id": t, "slug": teams[t]["slug"], "name": teams[t]["name"],
                   "conf": teams[t]["conf_id"]}
            for split in ("season", "last10", "conference"):
                s = ft[t]["splits"][split]
                out[split] = {"g": s["g"], "tempo": r1(s["tempo"]),
                              "off": {f: cell(split, "off", f, t, s["off"].get(f))
                                      for f in FACTORS + ("eff",)},
                              "def": {f: cell(split, "def", f, t, s["def"].get(f))
                                      for f in FACTORS + ("eff",)}}
            a = ft[t]["adjusted"]
            out["adjusted"] = {
                "off": {f: cell("adjusted", "off", f, t, a["off"].get(f)) for f in FACTORS},
                "def": {f: cell("adjusted", "def", f, t, a["def"].get(f)) for f in FACTORS}}
            if t in cur:
                out["adjusted"]["off"]["eff"] = [r1(cur[t]["adj_o"]), cur[t]["rank_o"], None]
                out["adjusted"]["def"]["eff"] = [r1(cur[t]["adj_d"]), cur[t]["rank_d"], None]
            frows.append(out)
        n_eff = len(cur)
        for out in frows:  # percentile for adjusted efficiency from rank
            for side in ("off", "def"):
                e = out["adjusted"][side].get("eff")
                if e and e[1]:
                    e[2] = round(100 * (1 - (e[1] - 1) / max(n_eff - 1, 1)), 1)
        _w(site / f"factors/{div}.json", {
            "season": sid, "div": div, "asof": fr["date"], "weights": fr["weights"],
            "avg": {k: r1(v) for k, v in fr["avg"].items()}, "teams": frows})
        files.append(f"factors/{div}.json")

        # ---- team pages ----------------------------------------------------------
        frow = {r["id"]: r for r in frows}
        div_players = [p for t in cur for p in players.get(t, [])
                       if p["min_pct"] >= LEADER_MIN_PCT]
        pkeys = {"ortg": True, "usg": None, "shots": None, "floor": True, "efg": True, "ts": True,
                 "orb_pct": True, "drb_pct": True, "ast_pct": True, "tov_pct": False,
                 "blk_pct": True, "stl_pct": True, "fc40": False, "ftr": True, "ft_pct": True,
                 "twop_pct": True, "tp_pct": True, "tpar": None, "min_pct": None}
        ppct = {k: rank_pct({p["player_id"]: p[k] for p in div_players}, hb)
                for k, hb in pkeys.items() if hb is not None}
        for t in cur:
            team_json(conn, site, sid, div, t, teams, rec, conf_rec, cur, frow.get(t), ft.get(t),
                      fr, players.get(t, []), ppct, today)
        files.append(f"teams/<slug>.json ({len(cur)} {div} teams)")

    # ---- tonight -----------------------------------------------------------------
    for k in range(-3, 8):
        d = today + dt.timedelta(days=k)
        _w(site / f"tonight/{d.isoformat()}.json", tonight(conn, sid, d, teams, rec))
    files.append("tonight/<date>.json (today-3 .. today+7)")
    return files


def team_json(conn, site, sid, div, t, teams, rec, conf_rec, cur, frow, ft, fr, plist, ppct,
              today):
    tm = teams[t]
    v = cur[t]
    sched = []
    for g in conn.execute(
            """SELECT g.*, p.home_pts, p.away_pts, p.home_win_prob, w.score watch,
                      w.components wcomp, w.tags wtags, q.ok_for_ratings
               FROM games g LEFT JOIN predictions p ON p.game_id=g.game_id AND p.model_version=?
               LEFT JOIN watch_scores w ON w.game_id=g.game_id
               LEFT JOIN game_quality q ON q.game_id=g.game_id
               WHERE g.season_id=? AND (g.home_id=? OR g.away_id=?)
                 AND g.status NOT IN ('missing') ORDER BY g.date, g.tipoff_utc""",
            (MODEL_VERSION, sid, t, t)):
        home = g["home_id"] == t
        opp = g["away_id"] if home else g["home_id"]
        o = teams.get(opp, {})
        orow = cur.get(opp)
        pm = None if g["home_pts"] is None else (g["home_pts"] - g["away_pts"]) * (1 if home else -1)
        am = None if g["home_score"] is None or g["status"] != "final" else \
            (g["home_score"] - g["away_score"]) * (1 if home else -1)
        sched.append({
            "id": g["game_id"], "date": g["date"], "tip": g["tipoff_utc"], "status": g["status"],
            "site": "N" if g["neutral"] else ("H" if home else "A"),
            "opp": {"slug": o.get("slug"), "name": o.get("name"), "div": o.get("division"),
                    "rk": orow["rank_em"] if orow else None},
            "pts": g["home_score"] if home else g["away_score"],
            "opp_pts": g["away_score"] if home else g["home_score"],
            "pred_margin": r1(pm), "margin": am,
            "win_prob": r3(None if g["home_win_prob"] is None else
                           (g["home_win_prob"] if home else 1 - g["home_win_prob"])),
            "conf_game": g["is_conference"], "crossover": g["crossover"], "tv": g["broadcast"],
            "watch": g["watch"], "ok": g["ok_for_ratings"],
        })
    # game-level four factors for finals
    gf = {}
    for r in conn.execute(
            """SELECT b.game_id, b.fgm, b.fga, b.tpm, b.ftm, b.oreb, b.tov, b.poss,
                      o.fgm ofgm, o.fga ofga, o.tpm otpm, o.ftm oftm, o.oreb ooreb, o.dreb odreb,
                      b.dreb, o.tov otov, o.poss oposs
               FROM team_game_box b JOIN team_game_box o ON o.game_id=b.game_id AND o.team_id=b.opp_id
               JOIN games g ON g.game_id=b.game_id WHERE g.season_id=? AND b.team_id=?""", (sid, t)):
        def pct(a, b):
            return r1(100 * a / b) if b else None
        gf[r["game_id"]] = {
            "off": {"efg": pct(r["fgm"] + 0.5 * r["tpm"], r["fga"]), "tov": pct(r["tov"], r["poss"]),
                    "orb": pct(r["oreb"], r["oreb"] + r["odreb"]), "ftr": pct(r["ftm"], r["fga"])},
            "def": {"efg": pct(r["ofgm"] + 0.5 * r["otpm"], r["ofga"]), "tov": pct(r["otov"], r["oposs"]),
                    "orb": pct(r["ooreb"], r["ooreb"] + r["dreb"]), "ftr": pct(r["oftm"], r["ofga"])}}
    for s in sched:
        s["factors"] = gf.get(s["id"])

    pl = []
    for p in plist:
        row = {"name": p["name"], "cls": p["class"], "pos": p["position"], "no": p["jersey"],
               "ht": p["height"], "g": p["g"], "gs": p["gs"], "min": r1(p["min"])}
        for k in ("min_pct", "ortg", "usg", "shots", "floor", "efg", "ts", "orb_pct", "drb_pct",
                  "ast_pct", "tov_pct", "blk_pct", "stl_pct", "fc40", "ftr", "ft_pct", "twop_pct",
                  "tp_pct", "tpar"):
            pc = ppct.get(k, {}).get(p["player_id"])
            row[k] = [r1(p[k]), pc[1] if pc else None]
        for k in ("pts", "fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "ast", "stl",
                  "blk", "tov", "pf"):
            row[k] = p[k]
        pl.append(row)

    _w(site / f"teams/{tm['slug']}.json", {
        "season": sid, "asof": v["date"], "model_version": MODEL_VERSION, "beta": True,
        "team": {"id": t, "slug": tm["slug"], "name": tm["name"], "div": div,
                 "conf": tm["conf_id"], "conf_name": tm["conf_name"], "color": tm["color"],
                 "logo": LOGO.format(slug=tm["slug"])},
        "record": {"w": rec[t][0], "l": rec[t][1], "cw": conf_rec[t][0], "cl": conf_rec[t][1]},
        "ratings": {"rk": v["rank_em"], "em": r1(v["adj_em"]), "o": r1(v["adj_o"]),
                    "o_rk": v["rank_o"], "d": r1(v["adj_d"]), "d_rk": v["rank_d"],
                    "t": r1(v["adj_t"]), "t_rk": v["rank_t"], "se": r1(v["se_em"]),
                    "rk_lo": v["rank_lo"], "rk_hi": v["rank_hi"], "g": v["games"],
                    "luck": r3(v["luck"]), "sos": r1(v["sos"]), "ncsos": r1(v["ncsos"]),
                    "n_teams": len(cur)},
        "trend": [[r["date"], r1(r["adj_em"]), r["rank_em"]] for r in conn.execute(
            "SELECT date, adj_em, rank_em FROM ratings_daily WHERE team_id=? AND model_version=? "
            "AND date IN (SELECT date FROM model_meta WHERE season_id=? AND division=?) "
            "ORDER BY date", (t, MODEL_VERSION, sid, div))],
        "factors": frow,
        "contrib": {k: r1(x) for k, x in (ft or {}).get("contrib", {}).items()},
        "weights": fr["weights"],
        "schedule": sched,
        "players": pl,
    })


def tonight(conn, sid, d: dt.date, teams, rec) -> dict:
    games = []
    for g in conn.execute(
            """SELECT g.*, w.score, w.components, w.tags, p.home_pts, p.away_pts, p.home_win_prob,
                      p.poss, rh.rank_em h_rk, ra.rank_em a_rk
               FROM games g LEFT JOIN watch_scores w ON w.game_id=g.game_id
               LEFT JOIN predictions p ON p.game_id=g.game_id AND p.model_version=?
               LEFT JOIN ratings_daily rh ON rh.team_id=g.home_id AND rh.model_version=?
                 AND rh.date=p.ratings_date
               LEFT JOIN ratings_daily ra ON ra.team_id=g.away_id AND ra.model_version=?
                 AND ra.date=p.ratings_date
               WHERE g.date=? AND g.status NOT IN ('missing','canceled')""",
            (MODEL_VERSION, MODEL_VERSION, MODEL_VERSION, d.isoformat())):
        h, a = teams.get(g["home_id"], {}), teams.get(g["away_id"], {})

        def side(tm, tid, pts, rk, pred):
            return {"slug": tm.get("slug"), "name": tm.get("name"), "div": tm.get("division"),
                    "conf": tm.get("conf_id"), "w": rec[tid][0], "l": rec[tid][1], "rk": rk,
                    "pts": pts, "pred": r1(pred)}
        games.append({
            "id": g["game_id"], "tip": g["tipoff_utc"], "status": g["status"],
            "neutral": g["neutral"], "tv": g["broadcast"], "conf_game": g["is_conference"],
            "crossover": g["crossover"], "type": g["game_type"], "venue": g["venue"],
            "home": side(h, g["home_id"], g["home_score"], g["h_rk"], g["home_pts"]),
            "away": side(a, g["away_id"], g["away_score"], g["a_rk"], g["away_pts"]),
            "home_win_prob": r3(g["home_win_prob"]), "poss": r1(g["poss"]),
            "watch": g["score"], "components": json.loads(g["components"]) if g["components"] else None,
            "tags": json.loads(g["tags"]) if g["tags"] else [],
        })
    games.sort(key=lambda x: (x["watch"] is None, -(x["watch"] or 0), x["tip"] or ""))
    return {"date": d.isoformat(), "model_version": MODEL_VERSION, "beta": True, "games": games}
