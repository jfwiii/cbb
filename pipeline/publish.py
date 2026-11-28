"""Export site JSON + bulk CSV from the database (DESIGN.md, "Build approach", "Downloads").

Everything is rebuilt from scratch each run into <out>/site, which the workflow
force-pushes to the gh-pages branch.
"""
from __future__ import annotations

import collections
import csv
import datetime as dt
import json
import shutil
import sqlite3
from pathlib import Path

from . import config
from .glossary import GLOSSARY

LOGO = "https://www.ncaa.com/sites/default/files/images/logos/schools/bgl/{slug}.svg"


def _div(x, y):
    return round(100 * x / y, 1) if y else None


def _w(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, separators=(",", ":"), default=str))


def _csv(path: Path, header: list, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def team_rows(conn, sid):
    return {r["team_id"]: dict(r) for r in conn.execute(
        """SELECT t.team_id, t.slug, t.name, t.color, t.kind, ts.division, ts.conf_id,
                  c.name AS conf_name
           FROM teams t LEFT JOIN team_seasons ts ON ts.team_id=t.team_id AND ts.season_id=?
           LEFT JOIN conferences c ON c.conf_id=ts.conf_id AND c.season_id=ts.season_id""",
        (sid,))}


def records(conn, sid):
    rec = collections.defaultdict(lambda: [0, 0])
    for g in conn.execute("SELECT home_id, away_id, home_score, away_score FROM games "
                          "WHERE season_id=? AND status='final'", (sid,)):
        if g["home_score"] is None or g["home_score"] == g["away_score"]:
            continue
        hw = g["home_score"] > g["away_score"]
        rec[g["home_id"]][0 if hw else 1] += 1
        rec[g["away_id"]][1 if hw else 0] += 1
    return rec


def team_season_stats(conn, sid):
    """Raw (unadjusted) season totals and four factors from team boxes."""
    tot = collections.defaultdict(lambda: collections.Counter())
    q = """SELECT b.*, o.fgm o_fgm, o.fga o_fga, o.tpm o_tpm, o.tpa o_tpa, o.ftm o_ftm,
                  o.fta o_fta, o.oreb o_oreb, o.dreb o_dreb, o.tov o_tov, o.pts o_pts,
                  o.poss o_poss
           FROM team_game_box b JOIN team_game_box o ON o.game_id=b.game_id AND o.team_id=b.opp_id
           JOIN games g ON g.game_id=b.game_id WHERE g.season_id=?"""
    for r in conn.execute(q, (sid,)):
        t = tot[r["team_id"]]
        t["g"] += 1
        t["min"] += r["minutes"] or 40
        for k in r.keys():
            if k not in ("game_id", "team_id", "opp_id", "minutes") and r[k] is not None:
                t[k] += r[k]
    out = {}
    for tid, t in tot.items():
        poss = (t["poss"] + t["o_poss"]) / 2
        out[tid] = {
            "g_box": t["g"],
            "ortg": _div(t["pts"], poss), "drtg": _div(t["o_pts"], poss),
            "tempo": round(poss * 40 / t["min"], 1) if t["min"] else None,
            "efg": _div(t["fgm"] + 0.5 * t["tpm"], t["fga"]),
            "tov": _div(t["tov"], t["poss"]),
            "orb": _div(t["oreb"], t["oreb"] + t["o_dreb"]),
            "ftr": _div(t["ftm"], t["fga"]),
            "o_efg": _div(t["o_fgm"] + 0.5 * t["o_tpm"], t["o_fga"]),
            "o_tov": _div(t["o_tov"], t["o_poss"]),
            "o_orb": _div(t["o_oreb"], t["o_oreb"] + t["dreb"]),
            "o_ftr": _div(t["o_ftm"], t["o_fga"]),
            "tpar": _div(t["tpa"], t["fga"]),
            "tp_pct": _div(t["tpm"], t["tpa"]),
            "twop_pct": _div(t["fgm"] - t["tpm"], t["fga"] - t["tpa"]),
            "ft_pct": _div(t["ftm"], t["fta"]),
            "ast": _div(t["ast"], t["fgm"]),
        }
    return out


def publish(conn: sqlite3.Connection, out: Path, factor_result: dict | None = None) -> Path:
    site = out / "site"
    if site.exists():
        shutil.rmtree(site)
    site.mkdir(parents=True)
    (site / ".nojekyll").write_text("")
    generated = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    seasons = [r[0] for r in conn.execute("SELECT DISTINCT season_id FROM games ORDER BY 1")]
    files = []

    for sid in seasons:
        teams = team_rows(conn, sid)
        rec = records(conn, sid)
        stats = team_season_stats(conn, sid)

        # ---- teams.json -----------------------------------------------------
        tlist = []
        for tid, t in teams.items():
            if tid not in rec and tid not in stats:
                continue
            tlist.append({"id": tid, "slug": t["slug"], "name": t["name"],
                          "div": t["division"], "conf": t["conf_id"], "color": t["color"],
                          "logo": LOGO.format(slug=t["slug"]) if t["kind"] == "ncaa" else None,
                          "w": rec[tid][0], "l": rec[tid][1]})
        _w(site / f"seasons/{sid}/teams.json", sorted(tlist, key=lambda x: x["name"]))
        files.append(f"seasons/{sid}/teams.json")

        # ---- per-date game JSON ---------------------------------------------
        boxes = collections.defaultdict(dict)
        for b in conn.execute("SELECT b.* FROM team_game_box b JOIN games g USING (game_id) "
                              "WHERE g.season_id=?", (sid,)):
            boxes[b["game_id"]][b["team_id"]] = {k: b[k] for k in b.keys()
                                                 if k not in ("game_id", "team_id", "opp_id")}
        by_date = collections.defaultdict(list)
        for g in conn.execute("SELECT g.*, q.ok_for_ratings, q.issues FROM games g LEFT JOIN "
                              "game_quality q USING (game_id) WHERE g.season_id=? AND "
                              "g.status NOT IN ('missing') ORDER BY tipoff_utc", (sid,)):
            h, a = teams.get(g["home_id"], {}), teams.get(g["away_id"], {})
            by_date[g["date"]].append({
                "id": g["game_id"], "tip": g["tipoff_utc"], "status": g["status"],
                "home": {"id": g["home_id"], "slug": h.get("slug"), "name": h.get("name"),
                         "div": h.get("division"), "pts": g["home_score"],
                         "box": boxes[g["game_id"]].get(g["home_id"])},
                "away": {"id": g["away_id"], "slug": a.get("slug"), "name": a.get("name"),
                         "div": a.get("division"), "pts": g["away_score"],
                         "box": boxes[g["game_id"]].get(g["away_id"])},
                "neutral": g["neutral"], "ot": g["num_ot"], "type": g["game_type"],
                "conf_game": g["is_conference"], "crossover": g["crossover"],
                "tv": g["broadcast"], "venue": g["venue"], "ok": g["ok_for_ratings"],
            })
        for day, games in by_date.items():
            _w(site / f"games/{day}.json", {"date": day, "games": games})
        files.append(f"games/<date>.json ({len(by_date)} dates)")

        # ---- bulk CSV per division -------------------------------------------
        for div in config.DIVISIONS:
            members = {tid for tid, t in teams.items() if t["division"] == div}
            base = site / f"csv/{sid}/{div}"
            hdr = ["Team", "Slug", "Conf", "G", "W", "L", "ORtg", "DRtg", "Tempo", "eFG%", "TOV%",
                   "ORB%", "FTr", "Opp eFG%", "Opp TOV%", "Opp ORB%", "Opp FTr", "3PAr", "3P%",
                   "2P%", "FT%", "AST%"]
            rows = []
            for tid in sorted(members, key=lambda x: teams[x]["name"]):
                s = stats.get(tid, {})
                rows.append([teams[tid]["name"], teams[tid]["slug"], teams[tid]["conf_id"] or "",
                             s.get("g_box", 0), rec[tid][0], rec[tid][1],
                             *(s.get(k, "") for k in ("ortg", "drtg", "tempo", "efg", "tov", "orb",
                                                      "ftr", "o_efg", "o_tov", "o_orb", "o_ftr",
                                                      "tpar", "tp_pct", "twop_pct", "ft_pct", "ast"))])
            _csv(base / "teams.csv", hdr, rows)

            rows = []
            for r in conn.execute(
                    """SELECT g.date, g.game_id, g.home_id, g.neutral, g.crossover, g.home_score,
                              g.away_score, q.ok_for_ratings, b.* FROM team_game_box b
                       JOIN games g USING (game_id) LEFT JOIN game_quality q USING (game_id)
                       WHERE g.season_id=? ORDER BY g.date, g.game_id""", (sid,)):
                if r["team_id"] not in members:
                    continue
                home = r["team_id"] == r["home_id"]
                site_code = "N" if r["neutral"] else ("H" if home else "A")
                pts, opp = (r["home_score"], r["away_score"]) if home else (r["away_score"], r["home_score"])
                rows.append([r["date"], r["game_id"], teams[r["team_id"]]["name"],
                             teams.get(r["opp_id"], {}).get("name"), site_code,
                             "W" if (pts or 0) > (opp or 0) else "L", pts, opp,
                             r["fgm"], r["fga"], r["tpm"], r["tpa"], r["ftm"], r["fta"], r["oreb"],
                             r["dreb"], r["ast"], r["stl"], r["blk"], r["tov"], r["pf"],
                             r["minutes"], None if r["poss"] is None else round(r["poss"], 1),
                             r["crossover"], r["ok_for_ratings"]])
            _csv(base / "games.csv",
                 ["Date", "GameID", "Team", "Opp", "Site", "Result", "PTS", "Opp PTS", "FGM", "FGA",
                  "3PM", "3PA", "FTM", "FTA", "OREB", "DREB", "AST", "STL", "BLK", "TOV", "PF",
                  "MIN", "Poss", "Crossover", "QualityOK"], rows)

            rows = []
            for g in conn.execute("SELECT * FROM games WHERE season_id=? AND status!='missing' "
                                  "ORDER BY date, tipoff_utc", (sid,)):
                if g["home_id"] not in members and g["away_id"] not in members:
                    continue
                rows.append([g["date"], g["tipoff_utc"], g["game_id"],
                             teams.get(g["away_id"], {}).get("name"),
                             teams.get(g["home_id"], {}).get("name"), g["neutral"], g["status"],
                             g["away_score"], g["home_score"], g["num_ot"], g["broadcast"],
                             g["game_type"], g["is_conference"], g["crossover"]])
            _csv(base / "schedule.csv",
                 ["Date", "TipoffUTC", "GameID", "Away", "Home", "Neutral", "Status", "Away PTS",
                  "Home PTS", "OT", "TV", "Type", "ConfGame", "Crossover"], rows)

            rows = []
            for p in conn.execute(
                    """SELECT ps.player_id, p.name, ps.team_id, ps.class, ps.position, ps.jersey,
                              COUNT(*) g, SUM(pg.starter) gs, SUM(pg.minutes) mins,
                              SUM(pg.fgm) fgm, SUM(pg.fga) fga, SUM(pg.tpm) tpm, SUM(pg.tpa) tpa,
                              SUM(pg.ftm) ftm, SUM(pg.fta) fta, SUM(pg.oreb) oreb, SUM(pg.dreb) dreb,
                              SUM(pg.ast) ast, SUM(pg.stl) stl, SUM(pg.blk) blk, SUM(pg.tov) tov,
                              SUM(pg.pf) pf, SUM(pg.pts) pts
                       FROM player_game_box pg JOIN games g USING (game_id)
                       JOIN player_seasons ps ON ps.player_id=pg.player_id AND ps.team_id=pg.team_id
                         AND ps.season_id=g.season_id
                       JOIN players p ON p.player_id=pg.player_id
                       WHERE g.season_id=? GROUP BY ps.player_id, ps.team_id""", (sid,)):
                if p["team_id"] not in members:
                    continue
                fga, fta = p["fga"] or 0, p["fta"] or 0
                rows.append([p["name"], teams[p["team_id"]]["name"], p["class"] or "",
                             p["position"] or "", p["jersey"] or "", p["g"], p["gs"],
                             None if p["mins"] is None else round(p["mins"], 1),
                             p["pts"], p["fgm"], p["fga"], p["tpm"], p["tpa"], p["ftm"], p["fta"],
                             p["oreb"], p["dreb"], p["ast"], p["stl"], p["blk"], p["tov"], p["pf"],
                             _div((p["fgm"] or 0) + 0.5 * (p["tpm"] or 0), fga),
                             _div(p["pts"] or 0, 2 * (fga + 0.44 * fta))])
            _csv(base / "players.csv",
                 ["Player", "Team", "Cls", "Pos", "No", "G", "GS", "MIN", "PTS", "FGM", "FGA", "3PM",
                  "3PA", "FTM", "FTA", "OREB", "DREB", "AST", "STL", "BLK", "TOV", "PF", "eFG%",
                  "TS%"], rows)
            files += [f"csv/{sid}/{div}/{n}.csv" for n in ("teams", "games", "schedule", "players")]

    if factor_result is not None and seasons:
        from .site_data import build
        files += build(conn, site, max(seasons), factor_result)

    _csv(site / "csv/glossary.csv", ["Abbrev", "Name", "Definition"], GLOSSARY)
    files.append("csv/glossary.csv")

    _w(site / "status.json", status(conn, generated))
    files.append("status.json")
    _w(site / "index.json", {"generated_at": generated, "seasons": seasons, "files": files})
    return site


def status(conn: sqlite3.Connection, generated: str) -> dict:
    runs = [dict(r) for r in conn.execute("SELECT * FROM ingest_runs ORDER BY run_id DESC LIMIT 10")]
    for r in runs:
        for k in ("coverage", "unmatched", "errors", "http"):
            r[k] = json.loads(r[k]) if r.get(k) else None
        if r.get("errors"):
            r["error_count"] = len(r["errors"])
            r["errors"] = r["errors"][:25]
    sid = conn.execute("SELECT MAX(season_id) FROM games").fetchone()[0]
    season_cov = {}
    for div in config.DIVISIONS:
        row = conn.execute(
            """SELECT COUNT(*) n, SUM(q.has_box) b, SUM(q.has_pbp) p, SUM(q.ok_for_ratings) ok
               FROM games g JOIN game_quality q USING (game_id)
               WHERE g.season_id=? AND g.status='final' AND ('|'||g.listed_in||'|') LIKE ?""",
            (sid, f"%|{div}|%")).fetchone()
        season_cov[div] = {"finals": row["n"], "box_pct": _div(row["b"] or 0, row["n"]),
                           "pbp_pct": _div(row["p"] or 0, row["n"]),
                           "ratings_ok_pct": _div(row["ok"] or 0, row["n"])}
    issues = [dict(r) for r in conn.execute(
        """SELECT g.game_id, g.date, g.listed_in, th.name home, ta.name away, q.issues
           FROM game_quality q JOIN games g USING (game_id)
           JOIN teams th ON th.team_id=g.home_id JOIN teams ta ON ta.team_id=g.away_id
           WHERE g.season_id=? AND (q.ok_for_ratings=0 OR q.has_pbp=0)
           ORDER BY g.date DESC LIMIT 200""", (sid,))]
    return {"generated_at": generated, "season": sid, "season_coverage": season_cov,
            "open_issues": issues, "recent_runs": runs}
