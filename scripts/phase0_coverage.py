#!/usr/bin/env python3
"""Phase 0 coverage check (DESIGN.md, "Phase 0 coverage check").

For each division (D1/D2/D3) and each requested week, pulls every game from the
ncaa.com scoreboard + game endpoints (via the henrygd/ncaa-api route pattern),
and reports the share of games with:

  * a final score
  * a box score for both teams
  * box-score team totals that add up to the final score
  * any play-by-play
  * play-by-play that includes substitution events

It also checks how often team names match between sources automatically
(ncaa.com vs. the ESPN site API for D1, which is the cross-check/fallback
source in DESIGN.md), how many opponents are non-NCAA or crossover, and
renders the D3 go/no-go (ships if box-score coverage >= 95%).

Stdlib only, so it runs unchanged on a GitHub Actions runner.

Usage:
  python3 scripts/phase0_coverage.py \
      --week 2026-11-16:2026-11-22 --week 2026-01-12:2026-01-18 \
      --out reports/phase0

  NCAA_API_BASE can point at a self-hosted henrygd/ncaa-api container.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import gzip
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

# ESPN's edge returns 403 for user agents containing a URL, so keep it plain.
USER_AGENT = "jfwiii-cbb-coverage-check/0.1"
DEFAULT_NCAA_BASE = os.environ.get("NCAA_API_BASE", "https://ncaa-api.henrygd.me")
ESPN_SCOREBOARD = (
    "https://site.api.espn.com/apis/site/v2/sports/basketball/"
    "mens-college-basketball/scoreboard?groups=50&limit=500&dates={ymd}"
)
DIVISIONS = ("d1", "d2", "d3")
D3_GO_THRESHOLD = 0.95  # DESIGN.md step 6
SUB_RE = re.compile(
    r"subbing (in|out)|substitution|\bsub (in|out)\b|enters the game|"
    r"goes to the bench|\bsub\b.*\bfor\b",
    re.I,
)


# --------------------------------------------------------------------------- #
# HTTP with per-host throttle, retries and an on-disk cache
# --------------------------------------------------------------------------- #
class Fetcher:
    def __init__(self, cache_dir: Path, rate: float, refresh: bool = False):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.min_interval = 1.0 / rate
        self.last_hit: dict[str, float] = {}
        self.refresh = refresh
        self.stats = collections.Counter()

    def _cache_path(self, url: str) -> Path:
        h = hashlib.sha1(url.encode()).hexdigest()
        return self.cache_dir / h[:2] / f"{h}.json.gz"

    def get_json(self, url: str, allow_404: bool = True):
        """Returns (status, parsed_json_or_None). Cached by URL."""
        path = self._cache_path(url)
        if path.exists() and not self.refresh:
            self.stats["cache_hit"] += 1
            with gzip.open(path, "rt") as f:
                rec = json.load(f)
            return rec["status"], rec["body"]

        host = urllib.parse.urlsplit(url).netloc
        status, body = None, None
        for attempt in range(5):
            wait = self.last_hit.get(host, 0) + self.min_interval - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self.last_hit[host] = time.monotonic()
            req = urllib.request.Request(
                url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
            )
            try:
                with urllib.request.urlopen(req, timeout=30) as r:
                    status = r.status
                    raw = r.read()
                self.stats[f"http_{status}"] += 1
                try:
                    body = json.loads(raw)
                except json.JSONDecodeError:
                    body = None
                    status = -1  # non-JSON response
                break
            except urllib.error.HTTPError as e:
                status = e.code
                self.stats[f"http_{status}"] += 1
                if status in (429, 500, 502, 503, 504):
                    time.sleep(2 ** attempt * 2)
                    continue
                break  # 4xx other than 429: final
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                self.stats["net_error"] += 1
                status = -2
                time.sleep(2 ** attempt * 2)
                continue

        # Only cache definitive answers, so transient failures get retried next run.
        if status == 200 or (status == 404 and allow_404):
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt") as f:
                json.dump({"url": url, "status": status, "body": body}, f)
        return status, body


import urllib.parse  # noqa: E402  (used in Fetcher)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def daterange(a: dt.date, b: dt.date):
    d = a
    while d <= b:
        yield d
        d += dt.timedelta(days=1)


def to_int(x):
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


STOP = {"university", "of", "the", "college", "at"}
# Abbreviations ncaa.com uses in short names ("Central Conn. St.", "Eastern Ill.").
ABBR = {
    "conn": "connecticut", "mich": "michigan", "ill": "illinois", "wash": "washington",
    "caro": "carolina", "tenn": "tennessee", "me": "maine", "ga": "georgia",
    "la": "louisiana", "fla": "florida", "ky": "kentucky", "ala": "alabama",
    "miss": "mississippi", "ark": "arkansas", "ariz": "arizona", "colo": "colorado",
    "ind": "indiana", "ky": "kentucky", "mo": "missouri", "neb": "nebraska",
    "okla": "oklahoma", "ore": "oregon", "pa": "pennsylvania", "tex": "texas",
    "va": "virginia", "wis": "wisconsin", "minn": "minnesota", "mass": "massachusetts",
    "so": "southern", "int'l": "international", "intl": "international",
    "a&m": "a and m", "cal": "california", "md": "maryland",
}


def norm_names(*names: str) -> set[str]:
    """Normalized variants of a team name, for automatic cross-source matching."""
    out = set()
    for n in names:
        if not n:
            continue
        s = n.lower()
        s = re.sub(r"\s*\([^)]*\)", "", s)  # drop "(GA)", "(PA)" qualifiers
        s = s.replace("&", " and ").replace("-", " ")
        s = re.sub(r"[.'’,]", "", s)
        toks = s.split()
        for variant in (toks, [ABBR.get(t, t) for t in toks]):
            v = " ".join(variant)
            v = re.sub(r"\bst$", "state", v)  # trailing "St." -> state
            v = re.sub(r"^st\b", "saint", v)  # leading "St." -> saint
            vt = [t for t in v.split() if t not in STOP and t != "u"]
            if vt:
                out.add(" ".join(vt))
                out.add("".join(vt))
    return out


# --------------------------------------------------------------------------- #
# Per-game evaluation
# --------------------------------------------------------------------------- #
def eval_box(box, home_score, away_score, home_seo, away_seo):
    r = {
        "box_any": False,
        "box_both": False,
        "box_players_both": False,
        "totals_match_score": False,
        "totals_internal_ok": False,
        "players_sum_ok": False,
    }
    if not isinstance(box, dict):
        return r
    tb = box.get("teamBoxscore") or []
    teams = {str(t.get("teamId")): t for t in (box.get("teams") or [])}
    sides = []
    for t in tb:
        ts = t.get("teamStats") or {}
        ps = t.get("playerStats") or []
        fga = to_int(ts.get("fieldGoalsAttempted"))
        if ts and fga:
            sides.append((t, ts, ps))
    r["box_any"] = bool(sides)
    r["box_both"] = len(sides) == 2
    if not r["box_both"]:
        return r
    r["box_players_both"] = all(len(ps) >= 5 for _, _, ps in sides)

    expected = {}
    for tid, t in teams.items():
        seo = t.get("seoname")
        if seo == home_seo:
            expected[tid] = home_score
        elif seo == away_seo:
            expected[tid] = away_score
        elif t.get("isHome") is True:
            expected[tid] = home_score
        elif t.get("isHome") is False:
            expected[tid] = away_score

    match = internal = psum = True
    for t, ts, ps in sides:
        fgm, tpm, ftm = (to_int(ts.get(k)) for k in
                         ("fieldGoalsMade", "threePointsMade", "freeThrowsMade"))
        pts = to_int(ts.get("points"))
        calc = None if None in (fgm, tpm, ftm) else 2 * fgm + tpm + ftm
        if pts is None:
            pts = calc
        exp = expected.get(str(t.get("teamId")))
        match &= pts is not None and exp is not None and pts == exp
        internal &= calc is not None and calc == pts
        player_pts = sum(to_int(p.get("points")) or 0 for p in ps)
        psum &= player_pts == pts
    r["totals_match_score"] = match
    r["totals_internal_ok"] = internal
    r["players_sum_ok"] = psum
    return r


def eval_pbp(pbp, home_score, away_score):
    r = {"pbp_any": False, "pbp_subs": False, "pbp_events": 0, "pbp_final_matches": False}
    if not isinstance(pbp, dict):
        return r
    evs = [e for p in (pbp.get("periods") or []) for e in (p.get("playbyplayStats") or [])]
    r["pbp_events"] = len(evs)
    r["pbp_any"] = len(evs) > 0
    if not evs:
        return r
    texts = (" ".join(filter(None, (e.get("homeText"), e.get("visitorText"),
                                    e.get("eventDescription")))) for e in evs)
    r["pbp_subs"] = any(SUB_RE.search(t) for t in texts)
    hs = max((to_int(e.get("homeScore")) or 0) for e in evs)
    vs = max((to_int(e.get("visitorScore")) or 0) for e in evs)
    r["pbp_final_matches"] = (hs, vs) == (home_score, away_score)
    return r


# --------------------------------------------------------------------------- #
# Main pull
# --------------------------------------------------------------------------- #
def pull_week(f: Fetcher, base: str, start: dt.date, end: dt.date, divisions, log):
    games: dict[str, dict] = {}
    sb_failures = []
    for d in daterange(start, end):
        for div in divisions:
            url = f"{base}/scoreboard/basketball-men/{div}/{d:%Y/%m/%d}/all-conf"
            status, body = f.get_json(url)
            if status != 200 or not body:
                if status != 404:  # 404 = no games that day
                    sb_failures.append((str(d), div, status))
                continue
            for g in body.get("games", []):
                g = g["game"]
                gid = g["gameID"]
                rec = games.setdefault(gid, {"game_id": gid, "date": str(d),
                                             "listed_in": [], "sb": g})
                if div not in rec["listed_in"]:
                    rec["listed_in"].append(div)
        log(f"  scoreboards {d}: {len(games)} unique games so far")

    for i, (gid, rec) in enumerate(sorted(games.items())):
        g = rec["sb"]
        hs, as_ = to_int(g["home"].get("score")), to_int(g["away"].get("score"))
        state = g.get("gameState")
        rec["state"] = state
        rec["final_score"] = state == "final" and hs is not None and as_ is not None
        rec["home"], rec["away"] = g["home"]["names"], g["away"]["names"]
        rec["home_conf"] = (g["home"].get("conferences") or [{}])[0].get("conferenceSeo", "")
        rec["away_conf"] = (g["away"].get("conferences") or [{}])[0].get("conferenceSeo", "")
        rec["home_score"], rec["away_score"] = hs, as_

        if state in ("final",):
            bs, box = f.get_json(f"{base}/game/{gid}/boxscore")
            ps, pbp = f.get_json(f"{base}/game/{gid}/play-by-play")
        else:
            bs = ps = None
            box = pbp = None
        rec["box_status"], rec["pbp_status"] = bs, ps
        rec.update(eval_box(box, hs, as_, g["home"]["names"]["seo"],
                            g["away"]["names"]["seo"]))
        rec.update(eval_pbp(pbp, hs, as_))
        del rec["sb"]
        if (i + 1) % 100 == 0:
            log(f"  games {i + 1}/{len(games)}")
    return games, sb_failures


def pull_espn(f: Fetcher, start: dt.date, end: dt.date):
    out = []
    for d in daterange(start, end):
        status, body = f.get_json(ESPN_SCOREBOARD.format(ymd=f"{d:%Y%m%d}"))
        if status != 200 or not body:
            continue
        for ev in body.get("events", []):
            comp = (ev.get("competitions") or [{}])[0]
            if (comp.get("status", {}).get("type", {}).get("state")) != "post":
                continue
            teams = []
            for c in comp.get("competitors", []):
                t = c.get("team", {})
                teams.append({
                    "id": t.get("id"),
                    "display": t.get("displayName", ""),
                    "names": norm_names(t.get("location", ""), t.get("shortDisplayName", ""),
                                        t.get("displayName", "").removesuffix(" " + t.get("name", "")),
                                        t.get("abbreviation", "")),
                    "score": to_int(c.get("score")),
                })
            if len(teams) == 2:
                out.append({"date": str(d), "id": ev.get("id"), "teams": teams})
    return out


def ncaa_names(n):
    return norm_names(n.get("short", ""), n.get("full", ""),
                      n.get("seo", "").replace("-", " "), n.get("char6", ""))


def crosswalk_d1(games: dict, espn: list):
    """Cross-check ncaa.com D1 against ESPN.

    Games are paired by date (±1 day) and either automatically-matched names or a
    unique exact score pair, so completeness isn't confused with name matching.
    Then, for paired games, measure how often each ncaa.com team name matches the
    corresponding ESPN team name automatically (the crosswalk question).
    """
    ncaa_d1 = [g for g in games.values() if "d1" in g["listed_in"] and g["final_score"]]
    by_date = collections.defaultdict(list)
    for g in ncaa_d1:
        by_date[g["date"]].append(g)

    def names_pair(g, e):
        a, b = ncaa_names(g["home"]), ncaa_names(g["away"])
        t0, t1 = e["teams"][0]["names"], e["teams"][1]["names"]
        return (a & t0 and b & t1) or (a & t1 and b & t0)

    def score_key(home, away):
        return tuple(sorted((home, away)))

    pairs, espn_unmatched, used = [], [], set()
    for e in espn:
        cands = []
        for dd in (0, -1, 1):
            day = str(dt.date.fromisoformat(e["date"]) + dt.timedelta(days=dd))
            cands += [g for g in by_date.get(day, []) if g["game_id"] not in used]
        hit = next((g for g in cands if names_pair(g, e)), None)
        if not hit:
            sk = score_key(e["teams"][0]["score"], e["teams"][1]["score"])
            same = [g for g in cands if score_key(g["home_score"], g["away_score"]) == sk]
            if len(same) == 1:
                hit = same[0]
        if hit:
            used.add(hit["game_id"])
            pairs.append((hit, e))
        else:
            espn_unmatched.append(e)

    # Per-team name matching on paired games (orientation by score, else by names).
    team_ok: dict[str, bool] = {}
    team_label: dict[str, str] = {}
    for g, e in pairs:
        e0, e1 = e["teams"]
        if g["home_score"] != g["away_score"] and e0["score"] != e1["score"]:
            home_e = e0 if e0["score"] == g["home_score"] else e1
        else:
            home_e = e0 if ncaa_names(g["home"]) & e0["names"] else e1
        away_e = e1 if home_e is e0 else e0
        for side, et in (("home", home_e), ("away", away_e)):
            seo = g[side]["seo"]
            ok = bool(ncaa_names(g[side]) & et["names"])
            team_ok[seo] = team_ok.get(seo, True) and ok
            team_label[seo] = f'{g[side].get("short") or seo} ↔ {et["display"]}'
    unmatched = sorted(team_label[s] for s, ok in team_ok.items() if not ok)
    return {
        "espn_final_games": len(espn),
        "ncaa_d1_final_games": len(ncaa_d1),
        "espn_games_found_in_ncaa": len(pairs),
        "ncaa_games_found_in_espn": len(used),
        "teams_total": len(team_ok),
        "teams_name_auto_matched": sum(team_ok.values()),
        "teams_unmatched": unmatched,
        "espn_unmatched_sample": [
            f'{e["date"]} {e["teams"][0]["display"]} vs {e["teams"][1]["display"]}'
            for e in espn_unmatched[:25]],
    }


# --------------------------------------------------------------------------- #
# Reporting
# --------------------------------------------------------------------------- #
METRICS = [
    ("final_score", "Final score"),
    ("box_both", "Box score, both teams"),
    ("box_players_both", "Player box, both teams"),
    ("totals_match_score", "Box totals = final score"),
    ("players_sum_ok", "Player pts sum = team pts"),
    ("pbp_any", "Any PBP"),
    ("pbp_subs", "PBP with substitutions"),
    ("pbp_final_matches", "PBP final = final score"),
]


def team_division_map(games: dict):
    """A team's division = the scoreboard it appears on most (crossovers list in both)."""
    c = collections.defaultdict(collections.Counter)
    for g in games.values():
        for s in ("home", "away"):
            conf = g[f"{s}_conf"]
            for div in g["listed_in"]:
                if conf:  # blank conference = non-NCAA opponent
                    c[g[s]["seo"]][div] += 1
    return {seo: cnt.most_common(1)[0][0] for seo, cnt in c.items()}


def summarize(games: dict, divisions):
    tdiv = team_division_map(games)
    rows = {}
    for div in divisions:
        gs = [g for g in games.values() if div in g["listed_in"]]
        n = len(gs)
        finals = [g for g in gs if g["state"] == "final"]
        row = {"games_listed": n, "games_final": len(finals)}
        for key, _ in METRICS:
            denom = n if key == "final_score" else len(finals)
            k = sum(1 for g in (gs if key == "final_score" else finals) if g.get(key))
            row[key] = (k, denom)
        non_ncaa = sum(1 for g in gs if not g["home_conf"] or not g["away_conf"])
        crossover = sum(1 for g in gs if len(g["listed_in"]) > 1)
        row["non_ncaa_opponent_games"] = non_ncaa
        row["crossover_games"] = crossover
        # Games where both teams are members of this division
        in_div = [g for g in finals if tdiv.get(g["home"]["seo"]) == div
                  and tdiv.get(g["away"]["seo"]) == div]
        row["in_division_box_both"] = (sum(g["box_both"] for g in in_div), len(in_div))
        row["in_division_players_both"] = (sum(g["box_players_both"] for g in in_div), len(in_div))
        row["in_division_players_sum_ok"] = (sum(g["players_sum_ok"] for g in in_div), len(in_div))
        row["in_division_pbp_subs"] = (sum(g["pbp_subs"] for g in in_div), len(in_div))
        teams = {g[s]["seo"] for g in gs for s in ("home", "away")
                 if tdiv.get(g[s]["seo"]) == div}
        row["division_teams_seen"] = len(teams)
        rows[div] = row
    return rows


def pct(t):
    k, n = t
    return "—" if n == 0 else f"{100 * k / n:.1f}% ({k}/{n})"


def render_md(results, args):
    L = []
    L.append("# Phase 0 coverage report\n")
    L.append(f"Generated {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC · "
             f"source `{args.ncaa_base}` (ncaa.com via henrygd/ncaa-api routes) · "
             f"runner `{args.runner_label}`\n")
    L.append("Denominators: *Final score* is over all games listed on that division's "
             "ncaa.com scoreboard; everything else is over games marked final. Crossover "
             "games appear under both divisions.\n")
    for wk in results["weeks"]:
        L.append(f"## Week {wk['start']} → {wk['end']}\n")
        divs = list(wk["summary"].keys())
        L.append("| Metric | " + " | ".join(d.upper() for d in divs) + " |")
        L.append("| --- |" + " --- |" * len(divs))
        L.append("| Games listed | " + " | ".join(str(wk["summary"][d]["games_listed"]) for d in divs) + " |")
        for key, label in METRICS:
            L.append(f"| {label} | " + " | ".join(pct(wk["summary"][d][key]) for d in divs) + " |")
        L.append("| Box both teams, in-division games only | " +
                 " | ".join(pct(wk["summary"][d]["in_division_box_both"]) for d in divs) + " |")
        for k, label in (("in_division_players_both", "Player box both teams, in-division only"),
                         ("in_division_players_sum_ok", "Player pts sum = team pts, in-division only"),
                         ("in_division_pbp_subs", "PBP with subs, in-division only")):
            L.append(f"| {label} | " + " | ".join(pct(wk["summary"][d][k]) for d in divs) + " |")
        L.append("| Crossover games | " + " | ".join(str(wk["summary"][d]["crossover_games"]) for d in divs) + " |")
        L.append("| Games vs. non-NCAA opponent | " + " | ".join(str(wk["summary"][d]["non_ncaa_opponent_games"]) for d in divs) + " |")
        L.append("| Division teams seen | " + " | ".join(str(wk["summary"][d]["division_teams_seen"]) for d in divs) + " |")
        L.append("")
        cw = wk.get("crosswalk_d1")
        if cw:
            L.append("**D1 cross-check vs. ESPN (`groups=50`)**\n")
            L.append(f"- ESPN final D1 games found on ncaa.com: "
                     f"{pct((cw['espn_games_found_in_ncaa'], cw['espn_final_games']))}")
            L.append(f"- ncaa.com D1-listed final games found on ESPN: "
                     f"{pct((cw['ncaa_games_found_in_espn'], cw['ncaa_d1_final_games']))} "
                     "(ncaa.com D1 scoreboard includes D1-vs-D2/D3/non-NCAA games ESPN may omit)")
            L.append(f"- Team names auto-matched to ESPN's name for the same team (paired games): "
                     f"{pct((cw['teams_name_auto_matched'], cw['teams_total']))}")
            if cw["teams_unmatched"]:
                L.append(f"- Unmatched names ({len(cw['teams_unmatched'])}): "
                         + ", ".join(cw["teams_unmatched"][:60]))
            if cw["espn_unmatched_sample"]:
                L.append("- ESPN games with no ncaa.com match (sample): "
                         + "; ".join(cw["espn_unmatched_sample"][:15]))
            L.append("")
        if wk["sb_failures"]:
            L.append(f"Scoreboard fetch failures: {wk['sb_failures']}\n")

    L.append("## D3 go/no-go\n")
    for line in results["decision"]:
        L.append(f"- {line}")
    L.append("")
    L.append(f"HTTP stats: {dict(results['http'])}\n")
    return "\n".join(L)


def decide(weeks):
    lines = []
    status = {}
    for div in DIVISIONS:
        vals = []
        for wk in weeks:
            if div in wk["summary"]:
                vals.append((wk["start"], wk["summary"][div]["box_both"]))
        if not vals:
            continue
        ok = all(n and k / n >= D3_GO_THRESHOLD for _, (k, n) in vals)
        status[div] = ok
        detail = ", ".join(f"{s}: {pct(t)}" for s, t in vals)
        lines.append(f"{div.upper()} box-score coverage {'≥' if ok else '<'} 95% in every week "
                     f"({detail}) → {'no fallback parser needed' if ok else 'needs PrestoSports/Sidearm fallback'}")
    if "d3" in status:
        lines.append(f"**D3: {'GO' if status['d3'] else 'NO-GO'}** "
                     "(rule: D3 ships if box-score coverage is at least 95%).")
    return lines


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", action="append", required=True,
                    help="START:END dates, inclusive (YYYY-MM-DD:YYYY-MM-DD). Repeatable.")
    ap.add_argument("--divisions", default="d1,d2,d3")
    ap.add_argument("--ncaa-base", default=DEFAULT_NCAA_BASE)
    ap.add_argument("--rate", type=float, default=2.0, help="requests/second per host (DESIGN: 2)")
    ap.add_argument("--cache", default=".cache/phase0")
    ap.add_argument("--refresh", action="store_true", help="ignore cache")
    ap.add_argument("--no-espn", action="store_true")
    ap.add_argument("--out", default="reports/phase0")
    ap.add_argument("--runner-label", default=os.environ.get("RUNNER_LABEL", "local"))
    args = ap.parse_args()

    divisions = [d.strip() for d in args.divisions.split(",") if d.strip()]
    f = Fetcher(Path(args.cache), args.rate, args.refresh)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log = lambda m: print(m, file=sys.stderr, flush=True)  # noqa: E731

    weeks, all_games = [], []
    for w in args.week:
        a, b = (dt.date.fromisoformat(x) for x in w.split(":"))
        log(f"Week {a} → {b}")
        games, sb_fail = pull_week(f, args.ncaa_base.rstrip("/"), a, b, divisions, log)
        wk = {"start": str(a), "end": str(b), "summary": summarize(games, divisions),
              "sb_failures": sb_fail}
        if "d1" in divisions and not args.no_espn:
            log("  ESPN D1 cross-check")
            wk["crosswalk_d1"] = crosswalk_d1(games, pull_espn(f, a, b))
        weeks.append(wk)
        for g in games.values():
            all_games.append({**g, "week": str(a)})

    results = {"weeks": weeks, "decision": decide(weeks), "http": f.stats}

    # Per-game detail CSV for digging into failures.
    cols = ["week", "date", "game_id", "listed_in", "state", "home_seo", "away_seo",
            "home_conf", "away_conf", "home_score", "away_score", "box_status", "pbp_status",
            "box_both", "box_players_both", "totals_match_score", "totals_internal_ok",
            "players_sum_ok", "pbp_any", "pbp_events", "pbp_subs", "pbp_final_matches"]
    import csv
    with open(out / "games.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for g in all_games:
            w.writerow([
                g["week"], g["date"], g["game_id"], "|".join(g["listed_in"]), g["state"],
                g["home"]["seo"], g["away"]["seo"], g["home_conf"], g["away_conf"],
                g["home_score"], g["away_score"], g["box_status"], g["pbp_status"],
                *(int(bool(g.get(k))) for k in ("box_both", "box_players_both",
                                                "totals_match_score", "totals_internal_ok",
                                                "players_sum_ok", "pbp_any")),
                g["pbp_events"], int(g["pbp_subs"]), int(g["pbp_final_matches"]),
            ])
    (out / "summary.json").write_text(json.dumps(results, indent=2, default=list))
    md = render_md(results, args)
    (out / "report.md").write_text(md)
    print(md)

    # Exit non-zero if nothing came back at all (e.g. blocked IP) so CI flags it.
    total = sum(wk["summary"][d]["games_listed"] for wk in weeks for d in wk["summary"])
    if total == 0:
        log("No games returned — source unreachable or blocked from this IP.")
        sys.exit(2)


if __name__ == "__main__":
    main()
