"""Ingest ncaa.com scoreboards, game details, box scores and raw PBP into SQLite."""
from __future__ import annotations

import collections
import datetime as dt
import gzip
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config, crosswalk, espn
from .fetch import Fetcher
from .ncaa import (NcaaClient, conf_name, conf_seo, game_status, name_key, parse_boxscore,
                   pbp_summary, possessions, team_key, tipoff_utc, to_int)


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class RunState:
    allow_new_teams: bool = False
    unmatched: dict = field(default_factory=dict)          # slug -> sample game
    auto_added: list = field(default_factory=list)
    new_teams: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    finals: collections.Counter = field(default_factory=collections.Counter)    # per div
    with_box: collections.Counter = field(default_factory=collections.Counter)  # per div
    pbp_bundles: dict = field(default_factory=lambda: collections.defaultdict(list))
    touched_seasons: set = field(default_factory=set)
    games_seen: int = 0
    u_finals: int = 0     # unique final games (crossovers counted once)
    u_box: int = 0


# --------------------------------------------------------------------------- #
def ensure_season(conn, sid: int):
    a, b = config.season_bounds(sid)
    conn.execute("INSERT OR IGNORE INTO seasons (season_id, start_date, end_date) VALUES (?,?,?)",
                 (sid, a.isoformat(), b.isoformat()))


def ingest_date(conn: sqlite3.Connection, client: NcaaClient, d: dt.date, st: RunState,
                details: bool = True) -> None:
    """Scoreboards for all divisions on one date, then every game on them."""
    listed: dict[str, dict] = {}
    complete = True
    for div in config.DIVISIONS:
        status, games = client.scoreboard(div, d)
        if status not in (200, 404):
            complete = False
            st.errors.append(f"{d} {div} scoreboard HTTP {status}")
            continue
        for g in games:
            rec = listed.setdefault(str(g["gameID"]), {"g": g, "divs": []})
            if div not in rec["divs"]:
                rec["divs"].append(div)

    seen_ids = []
    for gid, rec in listed.items():
        try:
            if ingest_game(conn, client, d, gid, rec["g"], rec["divs"], st, details):
                seen_ids.append(gid)
        except Exception as e:  # keep the run going; report it
            st.errors.append(f"{d} game {gid}: {type(e).__name__}: {e}")
    st.games_seen += len(listed)

    # Games we had on this date that no scoreboard lists any more (dropped / moved
    # without a new date) are marked missing, so they don't linger as 'scheduled'.
    if complete:
        q = "UPDATE games SET status='missing', updated_at=? WHERE date=? AND source='ncaa' " \
            "AND status IN ('scheduled','live')"
        args = [now_utc(), d.isoformat()]
        if seen_ids:
            q += f" AND source_game_id NOT IN ({','.join('?' * len(seen_ids))})"
            args += seen_ids
        conn.execute(q, args)
    conn.commit()


def ingest_game(conn, client: NcaaClient, d: dt.date, gid: str, g: dict, divs: list,
                st: RunState, details: bool) -> bool:
    status = game_status(g)
    ids = {}
    for side in ("home", "away"):
        names = g[side]["names"]
        tid, how = crosswalk.resolve_ncaa(conn, names, conf_seo(g[side]), st.allow_new_teams)
        key = team_key(names)
        if tid is None:
            st.unmatched.setdefault(key, f'{d} {g.get("title", gid)}')
        elif how == "auto-non-ncaa":
            st.auto_added.append(key)
        elif how == "new":
            st.new_teams.append(key)
        ids[side] = tid
    if None in ids.values():
        return False

    sid = config.season_for(d)
    ensure_season(conn, sid)
    st.touched_seasons.add(sid)
    hs, as_ = to_int(g["home"].get("score")), to_int(g["away"].get("score"))
    if status not in ("final", "live"):
        hs = as_ = None
    game_type = "ncaa" if g.get("bracketId") else "regular"
    conn.execute(
        """INSERT INTO games (season_id, date, tipoff_utc, home_id, away_id, game_type, status,
             home_score, away_score, broadcast, listed_in, source, source_game_id, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,'ncaa',?,?)
           ON CONFLICT(source, source_game_id) DO UPDATE SET
             season_id=excluded.season_id, date=excluded.date, tipoff_utc=excluded.tipoff_utc,
             home_id=excluded.home_id, away_id=excluded.away_id, game_type=excluded.game_type,
             status=excluded.status, home_score=excluded.home_score,
             away_score=excluded.away_score,
             broadcast=COALESCE(excluded.broadcast, games.broadcast),
             listed_in=excluded.listed_in, updated_at=excluded.updated_at""",
        (sid, d.isoformat(), tipoff_utc(g), ids["home"], ids["away"], game_type, status,
         hs, as_, (g.get("network") or None), "|".join(sorted(divs)), gid, now_utc()))
    game_id = conn.execute("SELECT game_id FROM games WHERE source='ncaa' AND source_game_id=?",
                           (gid,)).fetchone()[0]

    if status != "final" or not details:
        return True

    st.u_finals += 1
    for div in divs:
        st.finals[div] += 1

    # Sides, not slugs, link the scoreboard to game details and boxes (a few non-NCAA
    # opponents have no slug on ncaa.com).
    seo_to_id = {"home": ids["home"], "away": ids["away"]}
    conf_by_seo = {"home": (conf_seo(g["home"]), conf_name(g["home"])),
                   "away": (conf_seo(g["away"]), conf_name(g["away"]))}

    # --- game details: per-team division, linescores (OT), venue ---------------
    num_ot = None
    cs, contest = client.contest(gid)
    if contest:
        ls = contest.get("linescores") or []
        if ls:
            num_ot = max(0, len(ls) - 2)
        loc = contest.get("location") or {}
        venue = ", ".join(x for x in (loc.get("venue"), loc.get("city"), loc.get("stateUsps")) if x)
        conn.execute("UPDATE games SET venue=?, num_ot=COALESCE(?, num_ot) WHERE game_id=?",
                     (venue or None, num_ot, game_id))
        for t in contest.get("teams") or []:
            side = "home" if t.get("isHome") else "away"
            tid = seo_to_id[side]
            div = f"d{t['division']}" if t.get("division") else "non-ncaa"
            cseo, cname = conf_by_seo[side]
            if div != "non-ncaa" and cseo:
                conn.execute(
                    """INSERT INTO conferences (conf_id, season_id, division, name)
                       VALUES (?,?,?,?) ON CONFLICT(conf_id, season_id) DO UPDATE SET
                       name=COALESCE(conferences.name, excluded.name)""",
                    (cseo, sid, div, cname or None))
            conn.execute(
                """INSERT INTO team_seasons (team_id, season_id, division, conf_id)
                   VALUES (?,?,?,?) ON CONFLICT(team_id, season_id) DO UPDATE SET
                   division=excluded.division, conf_id=COALESCE(excluded.conf_id, team_seasons.conf_id)""",
                (tid, sid, div, cseo or None))
            if div != "non-ncaa":
                conn.execute("UPDATE teams SET kind='ncaa' WHERE team_id=? AND kind!='ncaa'", (tid,))
            if t.get("teamId"):
                conn.execute("INSERT OR IGNORE INTO team_aliases (source, source_key, team_id, origin) "
                             "VALUES ('ncaa_id', ?, ?, 'contest')", (str(t["teamId"]), tid))
            if t.get("color"):
                conn.execute("UPDATE teams SET color=? WHERE team_id=? AND color IS NULL",
                             (t["color"], tid))
    else:
        st.errors.append(f"{d} game {gid}: contest HTTP {cs}")

    # --- box score --------------------------------------------------------------
    bs, box = client.boxscore(gid)
    parsed = parse_boxscore(box) if bs == 200 else {}
    if bs != 200:
        st.errors.append(f"{d} game {gid}: boxscore HTTP {bs}")
    minutes = 40 + 5 * (num_ot or 0)
    conn.execute("DELETE FROM team_game_box WHERE game_id=?", (game_id,))
    conn.execute("DELETE FROM player_game_box WHERE game_id=?", (game_id,))
    team_lines = {}
    player_ok = []
    for seo, tid in seo_to_id.items():
        p = parsed.get(seo)
        if not p or not p["team"]:
            continue
        b = p["team"]
        team_lines[tid] = b
        opp = ids["away"] if tid == ids["home"] else ids["home"]
        conn.execute(
            """INSERT INTO team_game_box (game_id, team_id, opp_id, fgm, fga, tpm, tpa, ftm, fta,
                 oreb, dreb, ast, stl, blk, tov, pf, pts, minutes, poss)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (game_id, tid, opp, b["fgm"], b["fga"], b["tpm"], b["tpa"], b["ftm"], b["fta"],
             b["oreb"], b["dreb"], b["ast"], b["stl"], b["blk"], b["tov"], b["pf"], b["pts"],
             minutes, possessions(b)))
        psum = 0
        for pl in p["players"]:
            nk = name_key(pl["first"], pl["last"])
            if not nk:
                continue
            row = conn.execute("SELECT player_id FROM player_seasons WHERE team_id=? AND "
                               "season_id=? AND name_key=?", (tid, sid, nk)).fetchone()
            if row:
                pid = row[0]
                conn.execute(
                    """UPDATE player_seasons SET jersey=COALESCE(NULLIF(?,''), jersey),
                       class=COALESCE(NULLIF(?,''), class), position=COALESCE(NULLIF(?,''), position)
                       WHERE player_id=? AND team_id=? AND season_id=?""",
                    (pl["jersey"], pl["class"], pl["position"], pid, tid, sid))
            else:
                pid = conn.execute("INSERT INTO players (name) VALUES (?)",
                                   (f'{pl["first"]} {pl["last"]}'.strip(),)).lastrowid
                conn.execute(
                    "INSERT INTO player_seasons (player_id, team_id, season_id, name_key, jersey, "
                    "class, position) VALUES (?,?,?,?,?,?,?)",
                    (pid, tid, sid, nk, pl["jersey"] or None, pl["class"] or None,
                     pl["position"] or None))
            conn.execute(
                """INSERT OR REPLACE INTO player_game_box (game_id, player_id, team_id, starter,
                     minutes, fgm, fga, tpm, tpa, ftm, fta, oreb, dreb, ast, stl, blk, tov, pf, pts)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (game_id, pid, tid, pl["starter"], pl["minutes"], pl["fgm"], pl["fga"], pl["tpm"],
                 pl["tpa"], pl["ftm"], pl["fta"], pl["oreb"], pl["dreb"], pl["ast"], pl["stl"],
                 pl["blk"], pl["tov"], pl["pf"], pl["pts"]))
            psum += pl["pts"] or 0
        player_ok.append(bool(p["players"]) and psum == b["pts"])

    has_box = len(team_lines) == 2
    if has_box:
        st.u_box += 1
        for div in divs:
            st.with_box[div] += 1

    # --- raw play-by-play (archived, parsed in Phase 5) ----------------------------
    ps, pbp, raw = client.pbp(gid)
    summ = pbp_summary(pbp) if ps == 200 else {"events": 0, "periods": 0, "has_subs": False}
    if ps == 200 and raw and summ["events"]:
        if num_ot is None and summ["periods"]:
            num_ot = max(0, summ["periods"] - 2)
            conn.execute("UPDATE games SET num_ot=? WHERE game_id=?", (num_ot, game_id))
        st.pbp_bundles[d.isoformat()].append((sid, gid, game_id, raw))
        conn.execute(
            """INSERT INTO pbp_raw_index (game_id, source, path, fetched_at, bytes, sha256, events)
               VALUES (?,?,?,?,?,?,?) ON CONFLICT(game_id) DO UPDATE SET path=excluded.path,
               fetched_at=excluded.fetched_at, bytes=excluded.bytes, sha256=excluded.sha256,
               events=excluded.events""",
            (game_id, "ncaa", f"release:pbp-{sid}/pbp-{d.isoformat()}.jsonl.gz", now_utc(),
             len(raw), hashlib.sha256(raw).hexdigest(), summ["events"]))
    elif ps != 200:
        st.errors.append(f"{d} game {gid}: play-by-play HTTP {ps}")

    # --- validation -----------------------------------------------------------------
    issues = []
    totals = has_box and team_lines[ids["home"]]["pts"] == hs and team_lines[ids["away"]]["pts"] == as_
    if not has_box:
        issues.append("missing team box")
    elif not totals:
        issues.append("box totals != final score")
    poss_ok = None
    if has_box:
        pv = [possessions(b) for b in team_lines.values()]
        if None not in pv:
            per40 = (sum(pv) / 2) * 40 / minutes
            poss_ok = 50 <= per40 <= 95
            if not poss_ok:
                issues.append(f"possessions/40 = {per40:.1f}")
    pcomplete = has_box and len(player_ok) == 2 and all(player_ok)
    if has_box and not pcomplete:
        issues.append("player box incomplete")
    if not summ["events"]:
        issues.append("no play-by-play")
    conn.execute(
        """INSERT INTO game_quality (game_id, has_box, box_totals_match, player_box_complete,
             poss_ok, has_pbp, pbp_has_subs, issues) VALUES (?,?,?,?,?,?,?,?)
           ON CONFLICT(game_id) DO UPDATE SET has_box=excluded.has_box,
             box_totals_match=excluded.box_totals_match,
             player_box_complete=excluded.player_box_complete, poss_ok=excluded.poss_ok,
             has_pbp=excluded.has_pbp, pbp_has_subs=excluded.pbp_has_subs,
             issues=excluded.issues""",
        (game_id, int(has_box), int(bool(totals)), int(pcomplete),
         None if poss_ok is None else int(poss_ok), int(summ["events"] > 0),
         int(summ["has_subs"]), "; ".join(issues) or None))
    return True


# --------------------------------------------------------------------------- #
def finalize(conn: sqlite3.Connection, seasons: set[int]) -> None:
    """Derived flags that need every team's season row: crossover, is_conference,
    duplicates, ok_for_ratings."""
    for sid in seasons:
        conn.execute(
            """UPDATE games SET
                 crossover = (SELECT CASE WHEN h.division IS NULL OR a.division IS NULL THEN NULL
                                          WHEN h.division != a.division OR h.division='non-ncaa'
                                          THEN 1 ELSE 0 END
                              FROM team_seasons h, team_seasons a
                              WHERE h.team_id=games.home_id AND h.season_id=games.season_id
                                AND a.team_id=games.away_id AND a.season_id=games.season_id),
                 is_conference = (SELECT CASE WHEN h.conf_id IS NOT NULL AND h.conf_id=a.conf_id
                                            AND h.division!='non-ncaa'
                                            AND h.conf_id NOT LIKE '%independent%'
                                            THEN 1 ELSE 0 END
                              FROM team_seasons h, team_seasons a
                              WHERE h.team_id=games.home_id AND h.season_id=games.season_id
                                AND a.team_id=games.away_id AND a.season_id=games.season_id)
               WHERE season_id=?""", (sid,))
        conn.execute(
            """UPDATE game_quality SET is_duplicate = EXISTS (
                 SELECT 1 FROM games g1, games g2
                 WHERE g1.game_id=game_quality.game_id AND g2.game_id!=g1.game_id
                   AND g2.date=g1.date AND g2.status='final'
                   AND MIN(g2.home_id,g2.away_id)=MIN(g1.home_id,g1.away_id)
                   AND MAX(g2.home_id,g2.away_id)=MAX(g1.home_id,g1.away_id))
               WHERE game_id IN (SELECT game_id FROM games WHERE season_id=?)""", (sid,))
        conn.execute(
            """UPDATE game_quality SET ok_for_ratings =
                 (has_box=1 AND box_totals_match=1 AND COALESCE(poss_ok,0)=1
                  AND COALESCE(is_duplicate,0)=0)
               WHERE game_id IN (SELECT game_id FROM games WHERE season_id=?)""", (sid,))
    conn.commit()


def enrich_espn(conn: sqlite3.Connection, f: Fetcher, d: dt.date, st: RunState) -> None:
    """Pair ESPN D1 events with our games: by learned ESPN team aliases, else (finals) by a
    unique exact score on the same/adjacent date. Learns aliases; sets neutral + broadcast."""
    events = espn.scoreboard(f, d)
    if events is None:
        st.errors.append(f"{d} ESPN scoreboard unavailable")
        return
    days = [(d + dt.timedelta(days=k)).isoformat() for k in (0, -1, 1)]
    for ev in events:
        t0, t1 = ev["teams"]
        m0 = crosswalk.lookup(conn, "espn", t0["espn_id"])
        m1 = crosswalk.lookup(conn, "espn", t1["espn_id"])
        game = None
        if m0 and m1:
            for day in days:
                game = conn.execute(
                    "SELECT * FROM games WHERE date=? AND ((home_id=? AND away_id=?) OR "
                    "(home_id=? AND away_id=?)) AND status NOT IN ('missing','canceled')",
                    (day, m0, m1, m1, m0)).fetchone()
                if game:
                    break
        if game is None and ev["final"] and None not in (t0["score"], t1["score"]):
            key = sorted((t0["score"], t1["score"]))
            cands = conn.execute(
                f"SELECT * FROM games WHERE date IN (?,?,?) AND status='final' AND espn_id IS NULL "
                f"AND MIN(home_score,away_score)=? AND MAX(home_score,away_score)=?",
                (*days, *key)).fetchall()
            known = {m for m in (m0, m1) if m}
            cands = [c for c in cands if known <= {c["home_id"], c["away_id"]}]
            if len(cands) == 1 and t0["score"] != t1["score"]:
                game = cands[0]
                for t in (t0, t1):
                    tid = game["home_id"] if t["score"] == game["home_score"] else game["away_id"]
                    conn.execute("INSERT OR IGNORE INTO team_aliases (source, source_key, team_id, "
                                 "origin) VALUES ('espn', ?, ?, 'auto-score-pair')",
                                 (t["espn_id"], tid))
        if game is None:
            continue
        conn.execute("UPDATE games SET espn_id=?, neutral=?, broadcast=COALESCE(broadcast, ?) "
                     "WHERE game_id=?",
                     (ev["espn_id"], int(ev["neutral"]), ev["broadcast"], game["game_id"]))
    conn.commit()


def write_pbp_bundles(st: RunState, out_dir: Path) -> list[Path]:
    """One gzipped JSON-lines file per date: {"game_id", "source_game_id", "pbp"}."""
    paths = []
    for day, items in st.pbp_bundles.items():
        sid = items[0][0]
        p = out_dir / str(sid) / f"pbp-{day}.jsonl.gz"
        p.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(p, "wb") as fh:
            for _, gid, game_id, raw in sorted(items, key=lambda x: x[1]):
                fh.write(b'{"game_id":%d,"source":"ncaa","source_game_id":"%s","pbp":'
                         % (game_id, gid.encode()))
                fh.write(raw.strip())
                fh.write(b"}\n")
        paths.append(p)
    return paths


def coverage(st: RunState) -> dict:
    return {div: {"finals": st.finals[div], "with_box": st.with_box[div],
                  "pct": round(st.with_box[div] / st.finals[div], 4) if st.finals[div] else None}
            for div in config.DIVISIONS}
