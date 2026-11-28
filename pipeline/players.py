"""Player box-score advanced stats (DESIGN.md, "Stat names" > Player).

On-floor rates use the standard box-score approximation: the player's share of team
minutes stands in for the time he was on the floor. Team and opponent totals cover every
team game with a box score. ORtg and Floor% follow Dean Oliver's points-produced /
individual-possessions method as published by Basketball-Reference.
"""
from __future__ import annotations

import collections
import sqlite3

STATS = ("fgm", "fga", "tpm", "tpa", "ftm", "fta", "oreb", "dreb", "ast", "stl", "blk", "tov",
         "pf", "pts")


def _d(a, b):
    return a / b if b else 0.0


def oliver(p: dict, t: dict, o: dict) -> tuple[float | None, float | None]:
    """(ORtg, Floor%) for player totals p, team totals t, opponent totals o."""
    MP, TmMP = p["min"], t["min"]
    if not MP or not TmMP or not t["fgm"]:
        return None, None
    FG, FGA, TP, FT, FTA = p["fgm"], p["fga"], p["tpm"], p["ftm"], p["fta"]
    AST, ORB, TOV, PTS = p["ast"], p["oreb"], p["tov"], p["pts"]
    share = MP / (TmMP / 5)
    denom = (t["fgm"] / TmMP) * MP * 5 - FG
    qAST = share * (1.14 * _d(t["ast"] - AST, t["fgm"])) + \
        (_d((t["ast"] / TmMP) * MP * 5 - AST, denom) if denom > 0 else 0) * (1 - share)
    ppfga = _d(PTS - FT, 2 * FGA)
    FG_Part = FG * (1 - 0.5 * ppfga * qAST) if FGA else 0
    AST_Part = 0.5 * _d((t["pts"] - t["ftm"]) - (PTS - FT), 2 * (t["fga"] - FGA)) * AST
    FT_Part = (1 - (1 - _d(FT, FTA)) ** 2) * 0.4 * FTA if FTA else 0
    TmScPoss = t["fgm"] + (1 - (1 - _d(t["ftm"], t["fta"])) ** 2) * t["fta"] * 0.4
    opp_drb = o["dreb"]
    TmORBp = _d(t["oreb"], t["oreb"] + opp_drb)
    TmPlayp = _d(TmScPoss, t["fga"] + t["fta"] * 0.4 + t["tov"])
    wden = (1 - TmORBp) * TmPlayp + TmORBp * (1 - TmPlayp)
    TmORBW = _d((1 - TmORBp) * TmPlayp, wden)
    ORB_Part = ORB * TmORBW * TmPlayp
    adj = 1 - _d(t["oreb"], TmScPoss) * TmORBW * TmPlayp
    ScPoss = (FG_Part + AST_Part + FT_Part) * adj + ORB_Part
    FGxPoss = (FGA - FG) * (1 - 1.07 * TmORBp)
    FTxPoss = ((1 - _d(FT, FTA)) ** 2) * 0.4 * FTA if FTA else 0
    TotPoss = ScPoss + FGxPoss + FTxPoss + TOV
    if TotPoss <= 0:
        return None, None
    PP_FG = 2 * (FG + 0.5 * TP) * (1 - 0.5 * ppfga * qAST) if FGA else 0
    tm_other_fg = t["fgm"] - FG
    PP_AST = 2 * _d(tm_other_fg + 0.5 * (t["tpm"] - TP), tm_other_fg) * 0.5 * \
        _d((t["pts"] - t["ftm"]) - (PTS - FT), 2 * (t["fga"] - FGA)) * AST
    PP_ORB = ORB * TmORBW * TmPlayp * _d(t["pts"], TmScPoss)
    PProd = (PP_FG + PP_AST + FT) * adj + PP_ORB
    return 100 * PProd / TotPoss, 100 * ScPoss / TotPoss


def season_players(conn: sqlite3.Connection, sid: int) -> dict[int, list[dict]]:
    """{team_id: [player rows with totals and advanced stats]} for every team in a season."""
    team = collections.defaultdict(collections.Counter)
    opp = collections.defaultdict(collections.Counter)
    q = f"""SELECT b.team_id, b.minutes, b.poss, {', '.join('b.' + k for k in STATS)},
                   {', '.join('o.' + k + ' AS o_' + k for k in STATS)}, o.poss AS o_poss
            FROM team_game_box b JOIN team_game_box o ON o.game_id=b.game_id AND o.team_id=b.opp_id
            JOIN games g ON g.game_id=b.game_id WHERE g.season_id=?"""
    for r in conn.execute(q, (sid,)):
        t, o = team[r["team_id"]], opp[r["team_id"]]
        t["min"] += 5 * (r["minutes"] or 40)
        t["g"] += 1
        t["poss"] += r["poss"] or 0
        o["poss"] += r["o_poss"] or 0
        for k in STATS:
            t[k] += r[k] or 0
            o[k] += r["o_" + k] or 0

    players = collections.defaultdict(list)
    q = f"""SELECT pg.team_id, pg.player_id, p.name, ps.class, ps.position, ps.jersey, ps.height,
                   COUNT(*) g, SUM(pg.starter) gs, SUM(COALESCE(pg.minutes,0)) min,
                   {', '.join(f'SUM(COALESCE(pg.{k},0)) {k}' for k in STATS)}
            FROM player_game_box pg JOIN games gm ON gm.game_id=pg.game_id
            JOIN players p ON p.player_id=pg.player_id
            JOIN player_seasons ps ON ps.player_id=pg.player_id AND ps.team_id=pg.team_id
              AND ps.season_id=gm.season_id
            WHERE gm.season_id=? GROUP BY pg.team_id, pg.player_id"""
    for r in conn.execute(q, (sid,)):
        p = dict(r)
        t, o = team[p["team_id"]], opp[p["team_id"]]
        MP, TmMP = p["min"], t["min"]
        f = (TmMP / 5) / MP if MP else 0     # on-floor scaling
        ortg, floor = oliver(p, t, o)
        fga, fta = p["fga"], p["fta"]
        p.update({
            "min_pct": 100 * _d(MP, TmMP / 5),
            "ortg": ortg, "floor": floor,
            "usg": 100 * f * _d(fga + 0.475 * fta + p["tov"], t["fga"] + 0.475 * t["fta"] + t["tov"]),
            "shots": 100 * f * _d(fga, t["fga"]),
            "efg": 100 * _d(p["fgm"] + 0.5 * p["tpm"], fga) if fga else None,
            "ts": 100 * _d(p["pts"], 2 * (fga + 0.44 * fta)) if (fga or fta) else None,
            "orb_pct": 100 * f * _d(p["oreb"], t["oreb"] + o["dreb"]),
            "drb_pct": 100 * f * _d(p["dreb"], t["dreb"] + o["oreb"]),
            "ast_pct": 100 * _d(p["ast"], _d(MP, TmMP / 5) * t["fgm"] - p["fgm"])
            if MP and _d(MP, TmMP / 5) * t["fgm"] - p["fgm"] > 0 else None,
            "tov_pct": 100 * _d(p["tov"], fga + 0.44 * fta + p["tov"]) if (fga or fta or p["tov"]) else None,
            "blk_pct": 100 * f * _d(p["blk"], o["fga"] - o["tpa"]),
            "stl_pct": 100 * f * _d(p["stl"], o["poss"]),
            "fc40": 40 * _d(p["pf"], MP) if MP else None,
            "ftr": 100 * _d(p["ftm"], fga) if fga else None,
            "tpar": 100 * _d(p["tpa"], fga) if fga else None,
            "ft_pct": 100 * _d(p["ftm"], fta) if fta else None,
            "twop_pct": 100 * _d(p["fgm"] - p["tpm"], fga - p["tpa"]) if fga - p["tpa"] else None,
            "tp_pct": 100 * _d(p["tpm"], p["tpa"]) if p["tpa"] else None,
        })
        players[p["team_id"]].append(p)
    for lst in players.values():
        lst.sort(key=lambda x: -x["min"])
    return players
