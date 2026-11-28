"""Adjusted efficiency ratings and predictions (DESIGN.md, "Stats and ratings methodology").

Per division and season, a ridge regression on one row per team-game:

    100 * pts / poss = mu + O[team] + D[opp] + h * venue

O and D are shrunk toward a prior (last season's final rating, pulled toward the
conference mean; zero when there is none). AdjO = mu + O, AdjD = mu + D,
AdjEM = AdjO - AdjD. Tempo uses the same structure on possessions per 40 minutes.
Only games between two teams in the division that pass validation count.

Ratings are fitted "through" every date that had games, using only games up to and
including that date. Predictions for a date use the ratings through the day before,
so every stored prediction is a genuine pregame number (frozen with model_version).

Phase 2 uses sensible defaults; Phase 3 tunes every setting in a time-ordered backtest.
"""
from __future__ import annotations

import collections
import datetime as dt
import json
import math
import sqlite3
from dataclasses import asdict, dataclass

import numpy as np

from . import config

MODEL_VERSION = "v0-beta"


@dataclass(frozen=True)
class Params:
    lam: float = 3.0               # ridge strength on O/D, in game-equivalents
    lam_tempo: float = 3.0
    half_life_days: float = 75.0   # mild recency decay
    blowout_cap: float = 18.0      # margin error (pts) beyond which a game is down-weighted
    blowout_scale: float = 12.0
    prior_keep: float = 0.6        # last-season rating kept; rest pulled to conference mean
    sigma_inflate: float = 1.10    # in-sample margin residuals understate real error
    sigma_floor: float = 9.0


P = Params()


def phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
def division_games(conn: sqlite3.Connection, sid: int, div: str) -> list[dict]:
    """In-division finals that pass validation, with both team boxes."""
    q = """
    SELECT g.game_id, g.date, g.home_id, g.away_id, COALESCE(g.neutral, 0) AS neutral,
           g.is_conference, g.home_score, g.away_score,
           bh.poss AS h_poss, ba.poss AS a_poss, bh.minutes AS minutes
    FROM games g
    JOIN game_quality q ON q.game_id = g.game_id AND q.ok_for_ratings = 1
    JOIN team_seasons th ON th.team_id = g.home_id AND th.season_id = g.season_id
    JOIN team_seasons ta ON ta.team_id = g.away_id AND ta.season_id = g.season_id
    JOIN team_game_box bh ON bh.game_id = g.game_id AND bh.team_id = g.home_id
    JOIN team_game_box ba ON ba.game_id = g.game_id AND ba.team_id = g.away_id
    WHERE g.season_id = ? AND g.status = 'final' AND th.division = ? AND ta.division = ?
    ORDER BY g.date"""
    out = []
    for r in conn.execute(q, (sid, div, div)):
        poss = (r["h_poss"] + r["a_poss"]) / 2
        out.append({**dict(r), "poss": poss,
                    "tempo": poss * 40 / (r["minutes"] or 40)})
    return out


def division_teams(conn, sid: int, div: str) -> list[int]:
    return [r[0] for r in conn.execute(
        "SELECT team_id FROM team_seasons WHERE season_id=? AND division=? ORDER BY team_id",
        (sid, div))]


def prior_for(conn, sid: int, div: str, teams: list[int]) -> dict[int, tuple[float, float, float]]:
    """(O, D, T) deviations to shrink toward: last season's final ratings, pulled toward the
    team's current conference mean. Empty when last season isn't rated."""
    last = conn.execute(
        "SELECT MAX(date) FROM model_meta WHERE season_id=? AND division=? AND model_version=?",
        (sid - 1, div, MODEL_VERSION)).fetchone()[0]
    if not last:
        return {}
    meta = conn.execute("SELECT mu, mu_t FROM model_meta WHERE season_id=? AND division=? "
                        "AND date=? AND model_version=?",
                        (sid - 1, div, last, MODEL_VERSION)).fetchone()
    if not meta:
        return {}
    prev = {}
    for r in conn.execute(
            """SELECT r.team_id, r.adj_o, r.adj_d, r.adj_t FROM ratings_daily r
               JOIN team_seasons ts ON ts.team_id = r.team_id AND ts.season_id = ?
                 AND ts.division = ?
               WHERE r.date = ? AND r.model_version = ?""", (sid - 1, div, last, MODEL_VERSION)):
        prev[r["team_id"]] = (r["adj_o"] - meta["mu"], r["adj_d"] - meta["mu"],
                              r["adj_t"] - meta["mu_t"])
    conf = dict(conn.execute("SELECT team_id, conf_id FROM team_seasons WHERE season_id=?",
                             (sid,)).fetchall())
    by_conf = collections.defaultdict(list)
    for t in teams:
        if t in prev:
            by_conf[conf.get(t)].append(prev[t])
    conf_mean = {c: tuple(float(np.mean([v[i] for v in vals])) for i in range(3))
                 for c, vals in by_conf.items()}
    out = {}
    for t in teams:
        cm = conf_mean.get(conf.get(t), (0.0, 0.0, 0.0))
        if t in prev:
            out[t] = tuple(P.prior_keep * prev[t][i] + (1 - P.prior_keep) * cm[i] for i in range(3))
        else:
            out[t] = tuple(0.5 * cm[i] for i in range(3))  # new to the division
    return out


# --------------------------------------------------------------------------- #
# Fitting
# --------------------------------------------------------------------------- #
def _solve(n_params, rows_idx, rows_val, y, w, penalized, lam, prior_vec):
    """Weighted ridge via normal equations. rows_idx/val: (R, k) sparse design rows."""
    A = np.zeros((n_params, n_params))
    b = np.zeros(n_params)
    R, k = rows_idx.shape
    for a in range(k):
        np.add.at(b, rows_idx[:, a], w * rows_val[:, a] * y)
        for c in range(k):
            np.add.at(A, (rows_idx[:, a], rows_idx[:, c]), w * rows_val[:, a] * rows_val[:, c])
    pen = np.zeros(n_params)
    pen[penalized] = lam
    A[np.diag_indices(n_params)] += pen
    b += pen * prior_vec
    Ainv = np.linalg.inv(A)
    coef = Ainv @ b
    pred = (coef[rows_idx] * rows_val).sum(axis=1)
    resid = y - pred
    sigma2 = float((w * resid ** 2).sum() / max(w.sum() - n_params * 0.5, 1.0))
    return coef, Ainv * sigma2, pred


def fit(games: list[dict], teams: list[int], through: dt.date, prior: dict) -> dict | None:
    """Fit ratings using games on or before `through`."""
    gs = [g for g in games if g["date"] <= through.isoformat()]
    if len(gs) < 5:
        return None
    idx = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    # parameters: 0 mu, 1 h, 2..n+1 O, n+2..2n+1 D
    rows_idx, rows_val, y, base_w, gref = [], [], [], [], []
    for gi, g in enumerate(gs):
        if g["home_id"] not in idx or g["away_id"] not in idx:
            continue
        age = (through - dt.date.fromisoformat(g["date"])).days
        wr = 0.5 ** (age / P.half_life_days)
        venue = 0 if g["neutral"] else 1
        h, a = idx[g["home_id"]], idx[g["away_id"]]
        rows_idx.append([0, 1, 2 + h, 2 + n + a])
        rows_val.append([1, venue, 1, 1])
        y.append(100 * g["home_score"] / g["poss"])
        rows_idx.append([0, 1, 2 + a, 2 + n + h])
        rows_val.append([1, -venue, 1, 1])
        y.append(100 * g["away_score"] / g["poss"])
        base_w += [wr, wr]
        gref += [gi, gi]
    if len(y) < 10:
        return None
    rows_idx, rows_val = np.array(rows_idx), np.array(rows_val, dtype=float)
    y, base_w = np.array(y), np.array(base_w)
    n_params = 2 + 2 * n
    penalized = np.arange(2, n_params)
    prior_vec = np.zeros(n_params)
    for t, i in idx.items():
        if t in prior:
            prior_vec[2 + i], prior_vec[2 + n + i] = prior[t][0], prior[t][1]

    coef, cov, pred = _solve(n_params, rows_idx, rows_val, y, base_w, penalized, P.lam, prior_vec)
    # Blowout down-weighting: compare each game's margin with the fitted margin.
    gref = np.array(gref)
    poss = np.array([gs[i]["poss"] for i in gref])
    margin_err = np.zeros(len(gs))
    for r in range(0, len(y), 2):
        act = (y[r] - y[r + 1]) * poss[r] / 100
        exp = (pred[r] - pred[r + 1]) * poss[r] / 100
        margin_err[gref[r]] = abs(act - exp)
    excess = np.maximum(0, margin_err[gref] - P.blowout_cap)
    w = base_w / (1 + excess / P.blowout_scale)
    coef, cov, pred = _solve(n_params, rows_idx, rows_val, y, w, penalized, P.lam, prior_vec)

    # Tempo: possessions/40 = mu_t + T[home] + T[away]
    t_idx, t_val, ty, tw = [], [], [], []
    for gi, g in enumerate(gs):
        if g["home_id"] in idx and g["away_id"] in idx:
            age = (through - dt.date.fromisoformat(g["date"])).days
            t_idx.append([0, 1 + idx[g["home_id"]], 1 + idx[g["away_id"]]])
            t_val.append([1, 1, 1])
            ty.append(g["tempo"])
            tw.append(0.5 ** (age / P.half_life_days))
    tprior = np.zeros(1 + n)
    for t, i in idx.items():
        if t in prior:
            tprior[1 + i] = prior[t][2]
    tcoef, _, _ = _solve(1 + n, np.array(t_idx), np.array(t_val, dtype=float), np.array(ty),
                         np.array(tw), np.arange(1, 1 + n), P.lam_tempo, tprior)

    # Margin residual SD (for win probabilities)
    errs = []
    for r in range(0, len(y), 2):
        act = (y[r] - y[r + 1]) * poss[r] / 100
        exp = (pred[r] - pred[r + 1]) * poss[r] / 100
        errs.append(act - exp)
    sigma = max(P.sigma_floor, float(np.std(errs)) * P.sigma_inflate)

    mu, hca = float(coef[0]), float(coef[1])
    games_played = collections.Counter()
    for g in gs:
        games_played[g["home_id"]] += 1
        games_played[g["away_id"]] += 1
    out = {}
    for t, i in idx.items():
        o, d = 2 + i, 2 + n + i
        se_em = math.sqrt(max(cov[o, o] + cov[d, d] - 2 * cov[o, d], 0))
        out[t] = {"adj_o": mu + coef[o], "adj_d": mu + coef[d], "adj_em": coef[o] - coef[d],
                  "adj_t": float(tcoef[0] + tcoef[1 + i]), "se_em": se_em,
                  "games": games_played[t]}
    return {"teams": out, "mu": mu, "h": hca, "mu_t": float(tcoef[0]), "sigma": sigma,
            "n_games": len(gs)}


def components(games: list[dict], teams: list[int]) -> int:
    """Number of connected pieces in the division's schedule graph (1 = fully linked)."""
    parent = {t: t for t in teams}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    played = set()
    for g in games:
        if g["home_id"] in parent and g["away_id"] in parent:
            parent[find(g["home_id"])] = find(g["away_id"])
            played |= {g["home_id"], g["away_id"]}
    return len({find(t) for t in played})


# --------------------------------------------------------------------------- #
# Predictions
# --------------------------------------------------------------------------- #
def predict(fitres: dict, home: int, away: int, neutral: bool) -> dict | None:
    th, ta = fitres["teams"].get(home), fitres["teams"].get(away)
    if not th or not ta:
        return None
    mu_t = fitres["mu_t"]
    poss = th["adj_t"] * ta["adj_t"] / mu_t if mu_t else (th["adj_t"] + ta["adj_t"]) / 2
    venue = 0 if neutral else 1
    mu, h = fitres["mu"], fitres["h"]
    home_ppp = mu + (th["adj_o"] - mu) + (ta["adj_d"] - mu) + h * venue
    away_ppp = mu + (ta["adj_o"] - mu) + (th["adj_d"] - mu) - h * venue
    hp, ap = home_ppp * poss / 100, away_ppp * poss / 100
    return {"home_pts": hp, "away_pts": ap, "poss": poss,
            "home_win_prob": phi((hp - ap) / fitres["sigma"])}


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #
def run(conn: sqlite3.Connection, seasons: list[int] | None = None, recompute_days: int = 5,
        log=print) -> dict:
    """Fit ratings through every game date (refitting the last `recompute_days` and any
    missing dates), store ratings_daily + model_meta, and write pregame predictions."""
    today = config.today_et()
    seasons = seasons or [r[0] for r in conn.execute(
        "SELECT DISTINCT season_id FROM games ORDER BY 1")]
    current = {}
    for sid in seasons:
        for div in config.DIVISIONS:
            teams = division_teams(conn, sid, div)
            games = division_games(conn, sid, div)
            if not teams or len(games) < 5:
                continue
            prior = prior_for(conn, sid, div, teams)
            dates = sorted({g["date"] for g in games})
            last_game_day = dt.date.fromisoformat(dates[-1])
            through_days = [dt.date.fromisoformat(d) for d in dates]
            if sid == config.season_for(today) and today > last_game_day:
                through_days.append(min(today, config.season_bounds(sid)[1]))
            # A stored fit is reusable only if it used the same settings and prior status.
            setup = json.dumps({**asdict(P), "prior": bool(prior)})
            done = {r[0] for r in conn.execute(
                "SELECT date FROM model_meta WHERE season_id=? AND division=? AND model_version=? "
                "AND params=?", (sid, div, MODEL_VERSION, setup))}
            cutoff = (today - dt.timedelta(days=recompute_days)).isoformat()
            fits = {}
            for d in through_days:
                ds = d.isoformat()
                if ds in done and ds < cutoff:
                    continue
                res = fit(games, teams, d, prior)
                if not res:
                    continue
                fits[ds] = res
                store(conn, sid, div, ds, res, teams, setup)
            n_comp = components(games, teams)
            log(f"ratings {sid} {div}: {len(games)} games, {len(fits)} dates fitted, "
                f"schedule components={n_comp}, prior={'yes' if prior else 'no'}")
            write_predictions(conn, sid, div, today)
            current[(sid, div)] = latest_fit_date(conn, sid, div)
    compute_luck_sos(conn, seasons)
    conn.commit()
    return current


def store(conn, sid, div, ds, res, teams, setup: str):
    conn.execute(
        """INSERT INTO model_meta (season_id, division, date, model_version, mu, h, mu_t, sigma,
             n_games, params) VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(season_id, division, date, model_version) DO UPDATE SET mu=excluded.mu,
             h=excluded.h, mu_t=excluded.mu_t, sigma=excluded.sigma, n_games=excluded.n_games,
             params=excluded.params""",
        (sid, div, ds, MODEL_VERSION, res["mu"], res["h"], res["mu_t"], res["sigma"],
         res["n_games"], setup))
    rated = [(t, v) for t, v in res["teams"].items() if v["games"] > 0]
    em_sorted = sorted(rated, key=lambda x: -x[1]["adj_em"])
    rank = {t: i + 1 for i, (t, _) in enumerate(em_sorted)}
    o_rank = {t: i + 1 for i, (t, _) in enumerate(sorted(rated, key=lambda x: -x[1]["adj_o"]))}
    d_rank = {t: i + 1 for i, (t, _) in enumerate(sorted(rated, key=lambda x: x[1]["adj_d"]))}
    t_rank = {t: i + 1 for i, (t, _) in enumerate(sorted(rated, key=lambda x: -x[1]["adj_t"]))}
    ems = np.array([v["adj_em"] for _, v in em_sorted])
    conn.execute("DELETE FROM ratings_daily WHERE date=? AND model_version=? AND team_id IN "
                 f"({','.join('?' * len(teams))})", (ds, MODEL_VERSION, *teams))
    for t, v in rated:
        lo_em, hi_em = v["adj_em"] - 1.2816 * v["se_em"], v["adj_em"] + 1.2816 * v["se_em"]
        rank_hi = int((ems > lo_em).sum())          # worst plausible rank
        rank_lo = int((ems > hi_em).sum()) + 1      # best plausible rank
        conn.execute(
            """INSERT INTO ratings_daily (team_id, date, model_version, adj_o, adj_d, adj_em, adj_t,
                 rank_em, rank_o, rank_d, rank_t, se_em, rank_lo, rank_hi, games)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (t, ds, MODEL_VERSION, v["adj_o"], v["adj_d"], v["adj_em"], v["adj_t"], rank[t],
             o_rank[t], d_rank[t], t_rank[t], v["se_em"], rank_lo, max(rank_hi, rank_lo),
             v["games"]))


def latest_fit_date(conn, sid, div):
    return conn.execute("SELECT MAX(date) FROM model_meta WHERE season_id=? AND division=? AND "
                        "model_version=?", (sid, div, MODEL_VERSION)).fetchone()[0]


def load_fit(conn, sid, div, ds) -> dict | None:
    m = conn.execute("SELECT * FROM model_meta WHERE season_id=? AND division=? AND date=? AND "
                     "model_version=?", (sid, div, ds, MODEL_VERSION)).fetchone()
    if not m:
        return None
    teams = {r["team_id"]: dict(r) for r in conn.execute(
        "SELECT r.* FROM ratings_daily r JOIN team_seasons ts ON ts.team_id=r.team_id AND "
        "ts.season_id=? AND ts.division=? WHERE r.date=? AND r.model_version=?",
        (sid, div, ds, MODEL_VERSION))}
    return {"teams": teams, "mu": m["mu"], "h": m["h"], "mu_t": m["mu_t"], "sigma": m["sigma"]}


def write_predictions(conn, sid, div, today: dt.date):
    """Pregame predictions for in-division games: ratings through the previous game date.
    Past games keep their first (frozen) prediction; future games are refreshed."""
    fit_dates = [r[0] for r in conn.execute(
        "SELECT date FROM model_meta WHERE season_id=? AND division=? AND model_version=? "
        "ORDER BY date", (sid, div, MODEL_VERSION))]
    if not fit_dates:
        return
    cache = {}
    q = """SELECT g.game_id, g.date, g.home_id, g.away_id, COALESCE(g.neutral,0) neutral, g.status
           FROM games g
           JOIN team_seasons th ON th.team_id=g.home_id AND th.season_id=g.season_id
           JOIN team_seasons ta ON ta.team_id=g.away_id AND ta.season_id=g.season_id
           WHERE g.season_id=? AND th.division=? AND ta.division=?
             AND g.status NOT IN ('missing','canceled')"""
    import bisect
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for g in conn.execute(q, (sid, div, div)).fetchall():
        i = bisect.bisect_left(fit_dates, g["date"]) - 1   # last fit strictly before game date
        if i < 0:
            continue
        ds = fit_dates[i]
        if ds not in cache:
            cache[ds] = load_fit(conn, sid, div, ds)
        p = predict(cache[ds], g["home_id"], g["away_id"], bool(g["neutral"]))
        if not p:
            continue
        frozen = g["date"] < today.isoformat() or g["status"] == "final"
        verb = "INSERT OR IGNORE" if frozen else "INSERT OR REPLACE"
        conn.execute(
            f"""{verb} INTO predictions (game_id, model_version, home_pts, away_pts, poss,
                 home_win_prob, created_at, ratings_date) VALUES (?,?,?,?,?,?,?,?)""",
            (g["game_id"], MODEL_VERSION, p["home_pts"], p["away_pts"], p["poss"],
             p["home_win_prob"], now, ds))


def compute_luck_sos(conn, seasons):
    """Luck = wins - sum of pregame win probabilities; SOS / NCSOS = mean opponent AdjEM
    (current ratings) over in-division games."""
    for sid in seasons:
        for div in config.DIVISIONS:
            ds = latest_fit_date(conn, sid, div)
            if not ds:
                continue
            cur = {r["team_id"]: r["adj_em"] for r in conn.execute(
                "SELECT team_id, adj_em FROM ratings_daily WHERE date=? AND model_version=?",
                (ds, MODEL_VERSION))}
            luck = collections.defaultdict(float)
            opp = collections.defaultdict(list)
            nc_opp = collections.defaultdict(list)
            for g in conn.execute(
                    """SELECT g.home_id, g.away_id, g.home_score, g.away_score, g.is_conference,
                              p.home_win_prob FROM games g
                       JOIN predictions p ON p.game_id=g.game_id AND p.model_version=?
                       WHERE g.season_id=? AND g.status='final'""", (MODEL_VERSION, sid)):
                if g["home_id"] not in cur or g["away_id"] not in cur:
                    continue
                hw = 1.0 if g["home_score"] > g["away_score"] else 0.0
                luck[g["home_id"]] += hw - g["home_win_prob"]
                luck[g["away_id"]] += (1 - hw) - (1 - g["home_win_prob"])
                for t, o in ((g["home_id"], g["away_id"]), (g["away_id"], g["home_id"])):
                    opp[t].append(cur[o])
                    if not g["is_conference"]:
                        nc_opp[t].append(cur[o])
            for t in cur:
                conn.execute(
                    "UPDATE ratings_daily SET luck=?, sos=?, ncsos=? WHERE team_id=? AND date=? "
                    "AND model_version=?",
                    (round(luck[t], 3), float(np.mean(opp[t])) if opp[t] else None,
                     float(np.mean(nc_opp[t])) if nc_opp[t] else None, t, ds, MODEL_VERSION))
