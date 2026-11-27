"""Team crosswalk: data/teams.csv + data/team_aliases.csv -> teams / team_aliases.

ncaa.com's team seo slug is the primary key into the crosswalk. An NCAA team
(one with a conference) whose slug isn't in teams.csv is *unmatched*: its games
are skipped and the run fails, so new names get mapped instead of quietly
becoming duplicate teams. Non-NCAA opponents (no conference on ncaa.com: NAIA,
NCCAA, ...) are added automatically, because they appear every week in
November and only ever count toward records.
"""
from __future__ import annotations

import csv
import re
import sqlite3
from pathlib import Path

TEAMS_COLS = ["team_id", "slug", "name", "full_name", "char6", "kind"]


def load_teams_csv(conn: sqlite3.Connection, path: Path) -> int:
    if not path.exists():
        return 0
    n = 0
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            tid = int(r["team_id"])
            conn.execute(
                """INSERT INTO teams (team_id, name, slug, full_name, char6, kind)
                   VALUES (?,?,?,?,?,?)
                   ON CONFLICT(team_id) DO UPDATE SET name=excluded.name, slug=excluded.slug,
                     full_name=excluded.full_name, char6=excluded.char6, kind=excluded.kind""",
                (tid, r["name"], r["slug"], r.get("full_name") or None,
                 r.get("char6") or None, r.get("kind") or "ncaa"))
            conn.execute(
                """INSERT INTO team_aliases (source, source_key, team_id, origin)
                   VALUES ('ncaa', ?, ?, 'teams.csv')
                   ON CONFLICT(source, source_key) DO UPDATE SET team_id=excluded.team_id""",
                (r["slug"], tid))
            n += 1
    return n


def load_aliases_csv(conn: sqlite3.Connection, path: Path) -> int:
    """Manual aliases: source,source_key,team_id (e.g. a renamed ncaa.com slug)."""
    if not path.exists():
        return 0
    n = 0
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            conn.execute(
                """INSERT INTO team_aliases (source, source_key, team_id, origin)
                   VALUES (?,?,?,'manual')
                   ON CONFLICT(source, source_key) DO UPDATE SET team_id=excluded.team_id,
                     origin='manual'""",
                (r["source"], r["source_key"], int(r["team_id"])))
            n += 1
    return n


def lookup(conn: sqlite3.Connection, source: str, key: str) -> int | None:
    row = conn.execute("SELECT team_id FROM team_aliases WHERE source=? AND source_key=?",
                       (source, key)).fetchone()
    return row[0] if row else None


def _new_team(conn, names: dict, kind: str, origin: str) -> int:
    tid = (conn.execute("SELECT COALESCE(MAX(team_id), 0) FROM teams").fetchone()[0]) + 1
    slug = names["seo"]
    if conn.execute("SELECT 1 FROM teams WHERE slug=?", (slug,)).fetchone():
        slug = f"{slug}-{tid}"
    conn.execute("INSERT INTO teams (team_id, name, slug, full_name, char6, kind) "
                 "VALUES (?,?,?,?,?,?)",
                 (tid, names.get("short") or names["seo"], slug, names.get("full") or None,
                  names.get("char6") or None, kind))
    conn.execute("INSERT INTO team_aliases (source, source_key, team_id, origin) "
                 "VALUES ('ncaa', ?, ?, ?)", (names["seo"], tid, origin))
    return tid


def resolve_ncaa(conn, names: dict, conference: str, allow_new: bool = False):
    """Returns (team_id or None, how) where how in matched / auto-non-ncaa / new / unmatched."""
    tid = lookup(conn, "ncaa", names["seo"])
    if tid is not None:
        return tid, "matched"
    if not conference:
        return _new_team(conn, names, "non-ncaa", "auto-non-ncaa"), "auto-non-ncaa"
    if allow_new:
        return _new_team(conn, names, "ncaa", "auto-new"), "new"
    return None, "unmatched"


def export_teams_csv(conn: sqlite3.Connection, path: Path) -> None:
    rows = conn.execute("SELECT team_id, slug, name, full_name, char6, kind FROM teams "
                        "ORDER BY team_id").fetchall()
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(TEAMS_COLS)
        for r in rows:
            w.writerow([r["team_id"], r["slug"], r["name"], r["full_name"] or "",
                        r["char6"] or "", r["kind"]])


# --------------------------------------------------------------------------- #
# Name normalization (for ESPN pairing diagnostics)
# --------------------------------------------------------------------------- #
STOP = {"university", "of", "the", "college", "at"}
ABBR = {
    "conn": "connecticut", "mich": "michigan", "ill": "illinois", "wash": "washington",
    "caro": "carolina", "tenn": "tennessee", "me": "maine", "ga": "georgia",
    "la": "louisiana", "fla": "florida", "ky": "kentucky", "ala": "alabama",
    "miss": "mississippi", "ark": "arkansas", "ariz": "arizona", "colo": "colorado",
    "ind": "indiana", "mo": "missouri", "neb": "nebraska", "okla": "oklahoma",
    "ore": "oregon", "pa": "pennsylvania", "tex": "texas", "va": "virginia",
    "wis": "wisconsin", "minn": "minnesota", "mass": "massachusetts", "so": "southern",
    "intl": "international", "cal": "california", "md": "maryland", "col": "college",
    "val": "valley",
}


def norm_names(*names: str) -> set[str]:
    out = set()
    for n in names:
        if not n:
            continue
        s = re.sub(r"\s*\([^)]*\)", "", n.lower())
        s = s.replace("&", " and ").replace("-", " ")
        s = re.sub(r"[.'’,]", "", s)
        toks = s.split()
        for variant in (toks, [ABBR.get(t, t) for t in toks]):
            v = re.sub(r"\bst$", "state", " ".join(variant))
            v = re.sub(r"^st\b", "saint", v)
            vt = [t for t in v.split() if t not in STOP and t != "u"]
            if vt:
                out.add(" ".join(vt))
                out.add("".join(vt))
    return out
