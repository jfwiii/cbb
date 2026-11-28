"""SQLite schema (DESIGN.md, "Database schema") and small helpers."""
from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS seasons (
  season_id INTEGER PRIMARY KEY,          -- 2027 = 2026-27
  start_date TEXT, end_date TEXT,
  tourney_start TEXT, tourney_end TEXT
);

CREATE TABLE IF NOT EXISTS conferences (
  conf_id TEXT NOT NULL,                  -- ncaa.com conference seo, e.g. 'socon'
  season_id INTEGER NOT NULL,
  division TEXT,                          -- d1 / d2 / d3
  name TEXT, short_name TEXT,
  PRIMARY KEY (conf_id, season_id)
);

CREATE TABLE IF NOT EXISTS teams (
  team_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,                     -- canonical display name
  slug TEXT NOT NULL UNIQUE,
  full_name TEXT, char6 TEXT,
  kind TEXT NOT NULL DEFAULT 'ncaa',      -- 'ncaa' or 'non-ncaa' (NAIA, NCCAA, ...)
  color TEXT, logo TEXT
);

CREATE TABLE IF NOT EXISTS team_seasons (
  team_id INTEGER NOT NULL REFERENCES teams(team_id),
  season_id INTEGER NOT NULL,
  division TEXT NOT NULL,                 -- d1 / d2 / d3 / non-ncaa
  conf_id TEXT,
  returning_min_pct REAL,                 -- feeds the preseason prior (Phase 3)
  PRIMARY KEY (team_id, season_id)
);

CREATE TABLE IF NOT EXISTS team_aliases (
  source TEXT NOT NULL,                   -- 'ncaa' (seo slug) or 'espn' (team id)
  source_key TEXT NOT NULL,
  team_id INTEGER NOT NULL REFERENCES teams(team_id),
  origin TEXT,                            -- teams.csv / manual / auto-score-pair / auto-non-ncaa
  PRIMARY KEY (source, source_key)
);

CREATE TABLE IF NOT EXISTS games (
  game_id INTEGER PRIMARY KEY,
  season_id INTEGER NOT NULL,
  date TEXT NOT NULL,                     -- scoreboard date (ET)
  tipoff_utc TEXT,
  home_id INTEGER NOT NULL REFERENCES teams(team_id),
  away_id INTEGER NOT NULL REFERENCES teams(team_id),
  neutral INTEGER,                        -- NULL = unknown
  game_type TEXT NOT NULL DEFAULT 'regular',  -- regular / conf_tourney / ncaa / nit / other
  is_conference INTEGER,
  crossover INTEGER,                      -- teams in different divisions (or non-NCAA)
  status TEXT NOT NULL,                   -- scheduled / live / final / postponed / canceled
  home_score INTEGER, away_score INTEGER,
  num_ot INTEGER,
  broadcast TEXT,
  venue TEXT,
  listed_in TEXT,                         -- ncaa.com scoreboards listing it, e.g. 'd1|d2'
  source TEXT NOT NULL,
  source_game_id TEXT NOT NULL,
  espn_id TEXT,
  updated_at TEXT NOT NULL,
  UNIQUE (source, source_game_id)
);
CREATE INDEX IF NOT EXISTS games_date ON games(date);
CREATE INDEX IF NOT EXISTS games_season ON games(season_id);

CREATE TABLE IF NOT EXISTS team_game_box (
  game_id INTEGER NOT NULL REFERENCES games(game_id),
  team_id INTEGER NOT NULL REFERENCES teams(team_id),
  opp_id INTEGER NOT NULL REFERENCES teams(team_id),
  fgm INTEGER, fga INTEGER, tpm INTEGER, tpa INTEGER, ftm INTEGER, fta INTEGER,
  oreb INTEGER, dreb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER, tov INTEGER,
  pf INTEGER, pts INTEGER,
  minutes INTEGER,                        -- game minutes: 40 + 5 per OT
  poss REAL,                              -- this team's estimate; average both for game poss
  PRIMARY KEY (game_id, team_id)
);

CREATE TABLE IF NOT EXISTS players (
  player_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS player_seasons (
  player_id INTEGER NOT NULL REFERENCES players(player_id),
  team_id INTEGER NOT NULL REFERENCES teams(team_id),
  season_id INTEGER NOT NULL,
  name_key TEXT NOT NULL,                 -- normalized name used to match box rows
  jersey TEXT, class TEXT, position TEXT, height TEXT,
  PRIMARY KEY (player_id, team_id, season_id),
  UNIQUE (team_id, season_id, name_key)
);

CREATE TABLE IF NOT EXISTS player_game_box (
  game_id INTEGER NOT NULL REFERENCES games(game_id),
  player_id INTEGER NOT NULL REFERENCES players(player_id),
  team_id INTEGER NOT NULL REFERENCES teams(team_id),
  starter INTEGER, minutes REAL,
  fgm INTEGER, fga INTEGER, tpm INTEGER, tpa INTEGER, ftm INTEGER, fta INTEGER,
  oreb INTEGER, dreb INTEGER, ast INTEGER, stl INTEGER, blk INTEGER, tov INTEGER,
  pf INTEGER, pts INTEGER,
  PRIMARY KEY (game_id, player_id)
);

CREATE TABLE IF NOT EXISTS pbp_raw_index (
  game_id INTEGER PRIMARY KEY REFERENCES games(game_id),
  source TEXT NOT NULL,
  path TEXT NOT NULL,                     -- release:<tag>/<asset>
  fetched_at TEXT NOT NULL,
  bytes INTEGER, sha256 TEXT, events INTEGER
);

CREATE TABLE IF NOT EXISTS pbp_events (    -- Phase 5
  game_id INTEGER NOT NULL, seq INTEGER NOT NULL, period INTEGER, clock_sec INTEGER,
  team_id INTEGER, player_id INTEGER, event_type TEXT, shot_type TEXT, made INTEGER,
  points INTEGER, home_score INTEGER, away_score INTEGER, raw_text TEXT,
  PRIMARY KEY (game_id, seq)
);

CREATE TABLE IF NOT EXISTS game_quality (
  game_id INTEGER PRIMARY KEY REFERENCES games(game_id),
  has_box INTEGER,                        -- both team boxes present
  box_totals_match INTEGER,               -- box points = final score, both teams
  player_box_complete INTEGER,            -- player points sum to team points, both teams
  poss_ok INTEGER,                        -- possessions between 50 and 95
  is_duplicate INTEGER,                   -- same teams, same date, different source id
  has_pbp INTEGER,
  pbp_has_subs INTEGER,
  pbp_reconciles_to_box INTEGER,          -- Phase 5
  pbp_quality_ok INTEGER,                 -- Phase 5
  ok_for_ratings INTEGER,                 -- passes every box-level check
  issues TEXT
);

-- Phase 2+ tables
CREATE TABLE IF NOT EXISTS model_meta (    -- one row per division fit ("through" date)
  season_id INTEGER NOT NULL, division TEXT NOT NULL, date TEXT NOT NULL,
  model_version TEXT NOT NULL,
  mu REAL, h REAL, mu_t REAL, sigma REAL, n_games INTEGER, params TEXT,
  factor_weights TEXT,                    -- JSON: four-factor regression weights
  PRIMARY KEY (season_id, division, date, model_version)
);
CREATE TABLE IF NOT EXISTS ratings_daily ( -- ratings through `date` (games on or before it)
  team_id INTEGER NOT NULL, date TEXT NOT NULL, model_version TEXT NOT NULL,
  adj_o REAL, adj_d REAL, adj_em REAL, adj_t REAL,
  rank_em INTEGER, rank_o INTEGER, rank_d INTEGER, rank_t INTEGER,
  se_em REAL, rank_lo INTEGER, rank_hi INTEGER,   -- 80% rank range
  games INTEGER,                          -- in-division games in the fit
  sos REAL, ncsos REAL, luck REAL,
  PRIMARY KEY (team_id, date, model_version)
);
CREATE INDEX IF NOT EXISTS ratings_date ON ratings_daily(date, model_version);
CREATE TABLE IF NOT EXISTS team_factors_daily (
  team_id INTEGER NOT NULL, date TEXT NOT NULL, split TEXT NOT NULL, data TEXT,
  PRIMARY KEY (team_id, date, split)
);
CREATE TABLE IF NOT EXISTS predictions (   -- frozen before tip
  game_id INTEGER NOT NULL, model_version TEXT NOT NULL,
  home_pts REAL, away_pts REAL, poss REAL, home_win_prob REAL,
  created_at TEXT, ratings_date TEXT,
  PRIMARY KEY (game_id, model_version)
);
CREATE TABLE IF NOT EXISTS watch_scores (
  game_id INTEGER PRIMARY KEY, score REAL, components TEXT, tags TEXT, computed_at TEXT
);
CREATE TABLE IF NOT EXISTS sim_runs (
  sim_id INTEGER PRIMARY KEY, kind TEXT, stage TEXT, created_at TEXT, n_runs INTEGER
);
CREATE TABLE IF NOT EXISTS sim_team_results (
  sim_id INTEGER, team_id INTEGER, data TEXT, PRIMARY KEY (sim_id, team_id)
);

CREATE TABLE IF NOT EXISTS ingest_runs (
  run_id INTEGER PRIMARY KEY,
  started_at TEXT NOT NULL, finished_at TEXT,
  mode TEXT, source TEXT,
  window_start TEXT, window_end TEXT,
  games_expected INTEGER,                 -- finals listed on ncaa.com scoreboards
  games_fetched INTEGER,                  -- finals with both team boxes
  coverage TEXT,                          -- JSON: per-division coverage
  unmatched TEXT,                         -- JSON list of unmatched NCAA team slugs
  errors TEXT,                            -- JSON list
  http TEXT,                              -- JSON request counters
  duration_sec REAL,
  ok INTEGER
);
"""


def _columns(conn, table):
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def _migrate(conn: sqlite3.Connection) -> None:
    """Phase 1 created placeholder Phase 2 tables with no data; rebuild them with the real
    columns. Only ever drops tables that are still in their placeholder shape."""
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "ratings_daily" in tables and "rank_o" not in _columns(conn, "ratings_daily"):
        for t in ("ratings_daily", "predictions", "watch_scores", "team_factors_daily"):
            conn.execute(f"DROP TABLE IF EXISTS {t}")
    elif "predictions" in tables and "ratings_date" not in _columns(conn, "predictions"):
        conn.execute("DROP TABLE predictions")


def connect(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    _migrate(conn)
    conn.executescript(SCHEMA)
    return conn
