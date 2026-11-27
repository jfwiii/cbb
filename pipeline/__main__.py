"""CLI: python -m pipeline {nightly,load,publish,seed-teams,export-teams} ..."""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
import time
from pathlib import Path

from . import config, crosswalk, db
from .fetch import Fetcher
from .ingest import (RunState, coverage, enrich_espn, finalize, ingest_date, now_utc,
                     write_pbp_bundles)
from .ncaa import NcaaClient, conf_seo
from .publish import publish

EXIT_UNMATCHED, EXIT_COVERAGE = 3, 4


def daterange(a: dt.date, b: dt.date):
    while a <= b:
        yield a
        a += dt.timedelta(days=1)


def log(msg: str) -> None:
    print(f"[{dt.datetime.now():%H:%M:%S}] {msg}", file=sys.stderr, flush=True)


def setup(args):
    conn = db.connect(args.db)
    data = Path(args.data_dir)
    n = crosswalk.load_teams_csv(conn, data / "teams.csv")
    m = crosswalk.load_aliases_csv(conn, data / "team_aliases.csv")
    conn.commit()
    log(f"crosswalk: {n} teams from teams.csv, {m} manual aliases")
    f = Fetcher(cache_dir=Path(args.cache) if args.cache else None)
    return conn, f, NcaaClient(f, args.ncaa_base)


def schedule_dates(client: NcaaClient, start: dt.date, end: dt.date) -> list[dt.date]:
    """Dates with games between start and end, from the schedule route (falls back to every day)."""
    dates = set()
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        for div in config.DIVISIONS:
            got = client.schedule_month(div, y, m)
            if got is None:
                return list(daterange(start, end))
            dates |= {dt.date.fromisoformat(k) for k, v in got.items() if v}
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return sorted(d for d in dates if start <= d <= end)


def finish(conn, f, st: RunState, args, mode: str, run_id: int, t0: float, w0, w1) -> int:
    finalize(conn, st.touched_seasons)
    bundles = write_pbp_bundles(st, Path(args.out) / "pbp")
    cov = coverage(st)
    low = [d for d, c in cov.items()
           if c["finals"] >= config.COVERAGE_MIN_GAMES and c["pct"] < config.COVERAGE_FAIL_BELOW]
    ok = not st.unmatched and not low
    conn.execute(
        """UPDATE ingest_runs SET finished_at=?, window_start=?, window_end=?, games_expected=?,
             games_fetched=?, coverage=?, unmatched=?, errors=?, http=?, duration_sec=?, ok=?
           WHERE run_id=?""",
        (now_utc(), str(w0), str(w1), st.u_finals, st.u_box, json.dumps(cov),
         json.dumps(sorted(st.unmatched)), json.dumps(st.errors), json.dumps(dict(f.stats)),
         round(time.time() - t0, 1), int(ok), run_id))
    conn.commit()
    site = publish(conn, Path(args.out))
    conn.execute("VACUUM")
    conn.close()

    log(f"{mode} done: {st.games_seen} games seen, {st.u_box}/{st.u_finals} finals with box, "
        f"{len(bundles)} PBP bundles, {len(st.errors)} errors; published to {site}")
    log(f"coverage: {json.dumps(cov)}")
    if st.auto_added:
        log(f"auto-added non-NCAA teams: {sorted(set(st.auto_added))}")
    if st.new_teams:
        log(f"new NCAA teams (--allow-new-teams): {sorted(set(st.new_teams))}")
    if st.unmatched:
        path = Path(args.out) / "unmatched.csv"
        with open(path, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["ncaa_slug", "example_game"])
            w.writerows(sorted(st.unmatched.items()))
        log(f"UNMATCHED NCAA teams ({len(st.unmatched)}), see {path}: {sorted(st.unmatched)}")
        return EXIT_UNMATCHED
    if low:
        log(f"COVERAGE below {config.COVERAGE_FAIL_BELOW:.0%} in: {low}")
        return EXIT_COVERAGE
    return 0


def start_run(conn, mode) -> int:
    return conn.execute("INSERT INTO ingest_runs (started_at, mode, source) VALUES (?,?,?)",
                        (now_utc(), mode, "ncaa")).lastrowid


def cmd_nightly(args) -> int:
    conn, f, client = setup(args)
    st = RunState(allow_new_teams=args.allow_new_teams)
    t0, run_id = time.time(), start_run(conn, "nightly")
    today = dt.date.fromisoformat(args.today) if args.today else config.today_et()
    w0, w1 = today - dt.timedelta(days=config.REFETCH_DAYS), today
    ahead = today + dt.timedelta(days=config.SCHEDULE_AHEAD_DAYS)

    for d in daterange(w0, w1):
        log(f"results {d}")
        ingest_date(conn, client, d, st, details=True)
    for d in daterange(w1 + dt.timedelta(days=1), ahead):
        ingest_date(conn, client, d, st, details=False)

    future = conn.execute("SELECT COUNT(*) FROM games WHERE date>? AND status='scheduled'",
                          (ahead.isoformat(),)).fetchone()[0]
    season_end = config.season_bounds(config.season_for(today))[1]
    if (today.weekday() == 0 or future == 0 or args.schedule_refresh) and ahead < season_end:
        dates = schedule_dates(client, ahead + dt.timedelta(days=1), season_end)
        log(f"full schedule refresh: {len(dates)} dates")
        for d in dates:
            ingest_date(conn, client, d, st, details=False)

    for d in daterange(w0, ahead):
        enrich_espn(conn, f, d, st)
    return finish(conn, f, st, args, "nightly", run_id, t0, w0, w1)


def cmd_load(args) -> int:
    conn, f, client = setup(args)
    st = RunState(allow_new_teams=args.allow_new_teams)
    t0, run_id = time.time(), start_run(conn, "load")
    a, b = dt.date.fromisoformat(args.start), dt.date.fromisoformat(args.end)
    dates = schedule_dates(client, a, b)
    log(f"load {a} -> {b}: {len(dates)} dates with games")
    last = a - dt.timedelta(days=1)
    next_file = Path(args.out) / "next_start.txt"
    next_file.unlink(missing_ok=True)
    for d in dates:
        if args.time_budget_min and (time.time() - t0) / 60 > args.time_budget_min:
            next_file.parent.mkdir(parents=True, exist_ok=True)
            next_file.write_text(d.isoformat())
            log(f"time budget reached; resume from {d}")
            break
        log(f"load {d}")
        ingest_date(conn, client, d, st, details=True)
        if not args.no_espn:
            enrich_espn(conn, f, d, st)
        last = d
    return finish(conn, f, st, args, "load", run_id, t0, a, last)


def cmd_publish(args) -> int:
    conn = db.connect(args.db)
    print(publish(conn, Path(args.out)))
    return 0


def cmd_seed_teams(args) -> int:
    """Build data/teams.csv from every team on ncaa.com scoreboards in a date range."""
    f = Fetcher(cache_dir=Path(args.cache) if args.cache else None)
    client = NcaaClient(f, args.ncaa_base)
    seen: dict[str, dict] = {}
    for rng in args.range:
        a, b = (dt.date.fromisoformat(x) for x in rng.split(":"))
        dates = schedule_dates(client, a, b)
        log(f"seed {a} -> {b}: {len(dates)} dates")
        for d in dates:
            for div in config.DIVISIONS:
                _, games = client.scoreboard(div, d)
                for g in games:
                    for side in ("home", "away"):
                        n = g[side]["names"]
                        r = seen.setdefault(n["seo"], {"names": n, "conf": False})
                        r["conf"] |= bool(conf_seo(g[side]))
                        if n.get("full") and not r["names"].get("full"):
                            r["names"] = n
    rows = sorted(seen.values(), key=lambda r: (not r["conf"], r["names"]["seo"]))
    with open(args.out_csv, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(crosswalk.TEAMS_COLS)
        for i, r in enumerate(rows, 1):
            n = r["names"]
            w.writerow([i, n["seo"], n.get("short") or n["seo"], n.get("full") or "",
                        n.get("char6") or "", "ncaa" if r["conf"] else "non-ncaa"])
    log(f"wrote {len(rows)} teams to {args.out_csv} "
        f"({sum(r['conf'] for r in rows)} NCAA, {sum(not r['conf'] for r in rows)} non-NCAA)")
    return 0


def cmd_export_teams(args) -> int:
    conn = db.connect(args.db)
    crosswalk.export_teams_csv(conn, Path(args.data_dir) / "teams.csv")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m pipeline")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, needs_db=True):
        if needs_db:
            p.add_argument("--db", default="work/cbb.sqlite")
        p.add_argument("--out", default="work")
        p.add_argument("--data-dir", default="data")
        p.add_argument("--cache", default=None, help="optional on-disk HTTP cache dir")
        p.add_argument("--ncaa-base", default=config.NCAA_API_BASE)
        p.add_argument("--allow-new-teams", action="store_true",
                       help="create unknown NCAA teams instead of failing (bootstrap only)")

    p = sub.add_parser("nightly", help="last 3 days of results + upcoming schedule")
    common(p)
    p.add_argument("--today", help="override today's ET date (YYYY-MM-DD)")
    p.add_argument("--schedule-refresh", action="store_true", help="force full schedule refresh")
    p.set_defaults(fn=cmd_nightly)

    p = sub.add_parser("load", help="results for a date range (initial load / backfill)")
    common(p)
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--time-budget-min", type=float, default=0,
                   help="stop after this many minutes and write <out>/next_start.txt")
    p.add_argument("--no-espn", action="store_true")
    p.set_defaults(fn=cmd_load)

    p = sub.add_parser("publish", help="rebuild site JSON + CSV only")
    common(p)
    p.set_defaults(fn=cmd_publish)

    p = sub.add_parser("seed-teams", help="build data/teams.csv from scoreboards")
    common(p, needs_db=False)
    p.add_argument("--range", action="append", required=True, help="START:END, repeatable")
    p.add_argument("--out-csv", default="data/teams.csv")
    p.set_defaults(fn=cmd_seed_teams)

    p = sub.add_parser("export-teams", help="write data/teams.csv from the database")
    common(p)
    p.set_defaults(fn=cmd_export_teams)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
