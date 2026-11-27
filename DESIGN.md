# College Basketball Stats Hub — Design Doc

Nov 26, 2026

## Overview

**jfwiii.net College Basketball** (cbb.jfwiii.net) covers three ways to follow men's college basketball: March Madness, favorite teams across D1 and D2, and "what's worth having on tonight." It holds every D1 and D2 game, plus D3 if the Phase 0 data check passes. It updates itself every morning after the night's games and turns into a tournament landing page in March.

**Goals**

- KenPom-style ratings for each division, with conference filters and conference pages.
- Cleaning The Glass–style team pages: four factors on offense and defense with percentile ranks, and side-by-side matchup comparisons.
- A nightly Watch Score that ranks the upcoming games across divisions.
- A tournament mode that reuses the tournament.jfwiii.net model (composite rating, Monte Carlo, Path Analysis, TV Guide), extended to the D2 tournament.

**Not in v1:** women's basketball (the schema leaves room for it), live in-game updates, lineup and on/off stats, betting lines, NAIA and JUCO.

## Decisions locked

These were settled on 2026-11-26. Code should treat them as requirements, not suggestions.

| Area | Decision | Reason |
| --- | --- | --- |
| Name | jfwiii.net College Basketball, at cbb.jfwiii.net | Owner's call |
| Timeline | A working site as soon as possible. No date targets; the only fixed deadline is tournament mode before conference tournaments start. | Owner's call |
| Play-by-play | Store raw PBP for every game from launch. Parse it in Phase 5, showing derived stats only where a per-game quality flag passes. | Hard to backfill later. Parsing messy D2/D3 feeds shouldn't hold up launch. |
| Rating scale | A separate adjusted-efficiency fit for each division. Games against other divisions count toward records only. | Too few crossover games to put the divisions on one scale reliably. |
| Stat depth at launch | Team four factors, adjusted ratings and predictions, plus player box-score advanced stats. Phase 5 adds on/off and a BPM-style rating. | All of the launch set comes from box scores, the reliable layer. |
| Stack | Next.js static export on Netlify, redeployed only when code changes. Each night's data (JSON and CSV) is published to GitHub Pages from a public repo and loaded in the browser from a configurable base URL. The pipeline runs on GitHub Actions; the SQLite file and raw PBP are GitHub Release assets. | Public repos get unlimited Actions minutes. The site needs only Netlify's free tier. |
| Watch Score | 35% close game between good teams, 35% style extremes (strength-vs-strength and mismatches alike), 30% stakes. No team is pinned or boosted. | It should reflect style preferences, not rooting interests. |
| FT rate | FTM / FGA (Dean Oliver) | Owner's call |
| Downloads | Every team and player table downloads as CSV, plus nightly bulk CSV files | Owner's request; costs nothing because it's built from data already published |
| Look and feel | Dark theme, uppercase headings, pills and glyph markers, matching tournament.jfwiii.net | One family of sites |

## Architecture

```
Nightly pipeline (GitHub Actions, public repo, ~4:15 a.m. ET + ~10:15 a.m. ET catch-up)

  ncaa.com JSON ─────────┐
  ESPN API (D1 backup) ──┼──> Ingest + validate ──> SQLite database
  Presto/Sidearm (gaps) ─┘    (upsert by source       (games, boxes, ratings_daily;
                               game id; box totals     GitHub Release asset)
                               = final score;                 │
                               team crosswalk)                │ full recompute
                                    │                         v
                                    v                 Ratings + simulations
                              Raw PBP archive         (ridge fit per division,
                              (daily Release-asset     predictions, Watch Score,
                               bundles; parsed in      season + bracket sims)
                               Phase 5)                       │
                                                              v
  cbb.jfwiii.net  <── fetches JSON in browser ──  Site JSON + CSV export
  (Next.js static export on Netlify;                (gh-pages branch,
   redeploys only on code changes)                   served by GitHub Pages)
```

One scheduled job runs every step in order. Raw PBP is archived on the side, so Phase 5 can parse it without scraping again. The site never reads the database directly, only the JSON exported from it.

## Data sources

ncaa.com's JSON feeds are the main source for all three divisions. ESPN backs up D1, and school or conference stat pages fill D2/D3 gaps. stats.ncaa.org is not scraped nightly because the NCAA blocks scrapers there ([dwillis](https://buttondown.com/dwillis/archive/rails-against-the-machine/)).

| Source | Coverage | Role | Risk |
| --- | --- | --- | --- |
| ncaa.com scoreboard and game JSON, via the [henrygd/ncaa-api](https://github.com/henrygd/ncaa-api) route pattern (self-host its Docker image or call ncaa.com directly) | Scoreboards for D1/D2/D3, plus each game's box score, play-by-play and team stats | Main source for schedule, results, box scores and raw PBP | Unofficial, so the format can change. D2/D3 box and PBP completeness is unverified. The public demo is limited to 3 requests/second. |
| ESPN site API (scoreboard with `groups=50` for all of D1, plus game summary) | All of D1; lower divisions are thin | D1 cross-check and fallback; broadcast network for the Tonight page | Unofficial. Without `groups=50` it returns only Top 25 games. |
| PrestoSports / Sidearm school and conference stat pages (StatCrew-style box score and PBP) | Most D2/D3 programs, d3hoops.com | Gap filler, one parser per host format | Many hosts and slightly different markup |
| stats.ncaa.org | Everything, official | Manual reference and spot checks only | Blocks automated scraping |
| KenPom, Torvik, Massey | D1 (Massey also covers D2/D3) | Tournament-mode composite inputs only, as on tournament.jfwiii.net | No subscription needed: only the free ratings table on kenpom.com's front page is used, pulled once a day and only in March. Torvik and Massey are free too. |

**Phase 0 coverage check (do this before building anything else)**

1. Pull every D1, D2 and D3 game from the most recent complete week of this season, Nov 16–22, 2026, through the ncaa.com game endpoints. That tests today's feed formats, and early-season weeks are full of crossover and non-NCAA opponents, which stress the team crosswalk.
2. Also pull one week of 2025–26 conference play (Jan 12–18, 2026). Backfill and tuning depend on past seasons, so confirm those games are still served in full.
3. For each division and week, report the share of games with: a final score, a box score for both teams, any PBP, and PBP that includes substitution events.
4. Check that box-score team totals add up to the final score, and how often team names match the crosswalk automatically.
5. Run the same pull from a GitHub Actions runner to confirm cloud IPs aren't blocked (FanGraphs blocked them for the mlb app). If they are, fall back to a self-hosted runner.
6. Decide: D3 ships if box-score coverage is at least 95%. Any division below that gets a PrestoSports/Sidearm fallback parser, written conference by conference.

## Database schema

The database is one SQLite file, with team-season and game as the central grains. A season is roughly 16,000 games: about 32,000 team box rows, 400,000 player box rows, and (from Phase 5) 6–7 million PBP events. Raw PBP JSON is stored gzipped outside the database, so the core file stays small.

| Table | One row per | Key fields |
| --- | --- | --- |
| `seasons` | season | season\_id (2027 = 2026–27), start/end dates, tournament dates |
| `conferences` | conference × season | conf\_id, season\_id, division, name, short\_name |
| `teams` | program | team\_id, canonical name, slug, colors, logo |
| `team_seasons` | team × season | team\_id, season\_id, division, conf\_id, returning\_min\_pct (feeds the preseason prior) |
| `team_aliases` | source name | source, source\_key, team\_id (the crosswalk; unknown names fail loudly) |
| `games` | game | game\_id, season\_id, date, tipoff\_utc, home/away team\_id, neutral, game\_type (regular / conf\_tourney / ncaa / nit / other), is\_conference, crossover (true when the teams are in different divisions), status, scores, num\_ot, broadcast, source + source\_game\_id, updated\_at |
| `team_game_box` | team × game | game\_id, team\_id, opp\_id, FGM/FGA, 3PM/3PA, FTM/FTA, OREB, DREB, AST, STL, BLK, TOV, PF, PTS, minutes, poss |
| `players` / `player_seasons` | player / player × team-season | name, jersey, class, position, height |
| `player_game_box` | player × game | same counting stats as the team box, plus starter and minutes |
| `pbp_raw_index` | game | game\_id, source, object-storage path, fetched\_at, byte size, checksum |
| `pbp_events` (Phase 5) | event | game\_id, seq, period, clock\_sec, team\_id, player\_id, event\_type, shot\_type (rim / 2pt jumper / 3), made, points, running score, raw\_text |
| `game_quality` | game | has\_box, has\_pbp, box\_totals\_match, pbp\_has\_subs, pbp\_reconciles\_to\_box, pbp\_quality\_ok |
| `ratings_daily` | team × date | AdjO, AdjD, AdjEM, AdjT, ranks, SOS, non-conference SOS, luck, model\_version |
| `team_factors_daily` | team × date × split | raw and schedule-adjusted four factors for offense and defense; split = season / last10 / conference |
| `predictions` | game × model\_version | projected score, possessions, home win probability, created\_at (frozen before tip, used for backtests) |
| `watch_scores` | game | score 0–100, component values (JSON), style tags |
| `sim_runs` / `sim_team_results` | simulation / team × run | kind (conference / ncaa\_d1 / ncaa\_d2), stage, round-by-round probabilities |
| `ingest_runs` | nightly run | started\_at, source, games expected vs. fetched, errors, duration |

The full schedule is loaded at the start of the season and refreshed weekly. That way Tonight, predictions and simulations always have future games to work with.

## Stats and ratings methodology

Each division gets its own opponent- and venue-adjusted efficiency fit, recomputed every night, following KenPom and Torvik. Three additions come from general statistics: shrinkage tuned on held-out games, validation that only ever predicts forward in time, and an uncertainty band shown with every rating.

**Possessions** (average of the two teams' estimates). 0.475 is the usual college free-throw coefficient; the NBA convention is 0.44.

```latex
\text{Poss} = \text{FGA} - \text{OREB} + \text{TOV} + 0.475 \cdot \text{FTA}
```

**Four factors**, for offense and (as opponent values) defense:

- eFG% = (FGM + 0.5 × 3PM) / FGA
- TOV% = TOV / Poss
- ORB% = OREB / (OREB + opponent DREB). On defense this is the opponent's ORB%.
- FTr = FTM / FGA (Dean Oliver)

Oliver's 40/25/20/15 weights are only approximate. Instead, each division-season gets its own weights from a regression of efficiency on the four factors, and those weights drive the factor-contribution chart on team pages.

**Adjusted efficiency**

```latex
\text{PPP}_{\text{off}} = \mu + O_{\text{team}} - D_{\text{opp}} + h \cdot \text{venue}
```

- **Model:** ridge regression on one row per team-game. This is the same as a random-effects model that shrinks every team toward average until the data pulls it away. KenPom adjusts multiplicatively instead (each game's raw efficiency × the national average ÷ the opponent's adjusted defense) ([KenPom](https://kenpom.com/blog/ratings-explanation/)). The backtest compares this additive fit with the same fit on log efficiency (the multiplicative version) and keeps whichever predicts better.
- **Recency:** a mild exponential decay with its half-life tuned in the backtest. KenPom has cut back on recency weighting over time ([KenPom](https://kenpom.com/blog/ratings-methodology-update/)), so start gentle.
- **Blowouts:** lopsided games are down-weighted by how far the margin ran past the projected margin. KenPom added variable game weights to handle runaway margins ([KenPom](https://kenpom.com/blog/?p=267)). Torvik discounts a blowout of a much weaker opponent by as much as 75% ([DBR forum summary of T-Rank](https://forums.dukebasketballreport.com/forums/showthread.php?p=1124670)).
- **Garbage time (Phase 5, needs PBP):** stop counting possessions once a game is decided. KenPom found that once a lead reaches about 18 points with five minutes left, the rest of the game tells you nothing about the next meeting ([KenPom](https://kenpom.com/blog/?p=1921)). Torvik freezes efficiency once the game is no longer in question ([T-Rank FAQ](https://adamcwisports.blogspot.com/p/every-possession-counts.html)).
- **Venue:** one home-court value per division, in points per 100 possessions. Neutral sites get 0; D2 regional hosts in March get home.
- **Preseason prior:** last season's final AdjO and AdjD, scaled by the share of minutes returning (from player box scores), then pulled toward the conference mean. Its weight fades as games pile up. The fade rate is tuned in the backtest, not fixed at a game count. KenPom keeps some preseason influence early on for the same small-sample reason ([KenPom](https://kenpom.com/blog/?p=473)).
- **Tempo:** the same structure on possessions per 40 minutes gives AdjT.
- **Uncertainty:** every rating carries a standard error from the fit. Ranks show an 80% range on hover, and teams with few in-division games are flagged. Before fitting, check that each division's schedule links every team to every other (early-season D3 can split into islands).

**Schedule-adjusted factors:** the same model run on each factor.

**SOS** = average opponent AdjEM, also given for non-conference games only. **Luck** = actual wins minus the sum of pregame win probabilities.

**Predictions:** projected possessions from both teams' AdjT, combined multiplicatively. Margin = AdjEM difference × possessions / 100, plus h for a home game. Win probability = Φ(margin / σ), with σ fit per division. Predictions are frozen before tip with a model\_version.

**Validation**

1. Time-ordered only: for each date, fit on games before it and predict that day's games. Never random cross-validation, which would let the future leak in.
2. Tune every setting (ridge penalty, recency half-life, blowout weight, prior fade, home court, σ) on 2024–25, and report accuracy on 2025–26, which tuning never sees. The test season must be separate from the tuning season, or the reported accuracy flatters the model. Testing on the most recent season shows how it handles today's game. Once the numbers are reported, re-tune on both seasons together for the production model, and track live accuracy through 2026–27.
3. Metrics: margin MAE and RMSE; log loss and Brier score for win probabilities; a calibration plot (predicted vs. actual win rate in 10% bins).
4. Baselines to beat: home court alone, and last season's final ratings carried forward. For D1, also check rank correlation with KenPom's final 2025–26 ratings.

**Small samples:** team stats based on fewer than 5 games are greyed. Player leaderboards require at least 40% of team minutes.

**Conference ratings:** each conference is ranked by its members' average AdjEM. A 10,000-run simulation of the remaining conference schedule gives projected standings, title odds and seed odds.

## Watch Score

Every upcoming game gets a 0–100 Watch Score built from the owner's viewing preferences: a close game between good teams, an unusual style, and stakes. No team is pinned or boosted; favorite teams' games rank on the same terms as everyone else's.

```latex
W = 100 \cdot \left( 0.35 \, C \, Q^{\gamma} + 0.35 \, X + 0.30 \, S \right), \quad \gamma = 1.5
```

Each component runs from 0 to 1. Weights and γ live in a config file so they can be tuned.

| Component | Weight | What it measures | How it's computed |
| --- | --- | --- | --- |
| C × Q^γ — Close and good | 35% | A likely close game between two good teams | C = 1 − 2·\|p − 0.5\|, where p is the predicted win probability. Q = the geometric mean of the two teams' AdjEM percentiles within the division. Raising Q to γ = 1.5 means a toss-up between two top-20 teams far outscores a toss-up between two sub-200 teams. |
| X — Style extremes | 35% | Four-factor and tempo outliers | Each team's z-scores within its division for AdjT, adjusted eFG% (offense and defense), TOV% forced, ORB%, 3PA rate and AdjD. X = the largest \|z\| in the game, scaled from 0 at 1σ to 1 at 3σ, plus 0.15 (capped at 1) for a notable pairing in any factor, counted equally whether it's strength vs. strength (one team's top-10% offense against the other's top-10% defense) or a mismatch (top-10% offense against bottom-10% defense, or the fastest against the slowest tempo). |
| S — Stakes | 30% | How much the game changes seeding or placement | From the nightly season simulation: for each team, the gap between its odds if it wins and if it loses, summed over the outcomes that matter (conference regular-season title, top-4 seed or bye in the conference tournament, NCAA bid, NCAA seed line; D2 uses the top-8 regional ranking as the bid proxy). Take the larger of the two teams, then scale against the division's distribution for that date. It rises naturally from November to March. |

**Style tags** appear as pills, in three kinds treated equally. Each tag names the factor involved.

- **Shared extremes** (both teams): Track Meet (both top 10% in tempo), Rock Fight (both bottom 10%), Shooting Gallery (both top 10% in eFG%), Glass War (both top 10% in ORB%), Turnover Chaos (both top 10% in TOV% forced).
- **Strength vs. strength** (one team's top-10% offense in a factor against the other's top-10% defense in it): Unstoppable vs. Immovable, e.g. "Unstoppable vs. Immovable: on the glass."
- **Mismatches** (top 10% against bottom 10% in the same factor): Tempo Tug-of-War (fastest vs. slowest), Open Season (elite shooting vs. a bottom-10% eFG% defense), Second-Chance Feast (elite offensive rebounding vs. poor defensive rebounding), Pickpocket Alert (a turnover-prone offense vs. a ball-hawking defense), Parade to the Line (an elite FTr offense vs. a foul-prone defense).

**Display.** Each game shows its score, three small component bars, its tags, the TV or stream source, and tip time in ET. A division filter (All / D1 / D2 / D3) and a minimum-score slider sit above the list.

**Feedback loop (Phase 5).** A post-game Excitement rating from PBP (lead changes, final-5-minute margin, overtime) goes into an archive, like watchingbaseball.com's. It makes it possible to check whether the pregame score picked the right games and adjust the weights.

## Site map and pages

The home page is Tonight, the Watch Score slate, for most of the season and the tournament page in March. Ratings, conference, team and player pages are the KenPom/CTG core underneath.

| Route | Page | What's on it |
| --- | --- | --- |
| `/` | Tonight | Date navigation with previous and next arrows like watchingbaseball.com. Games ranked by Watch Score with division filter, tags and TV source. Final scores and Excitement after the games. |
| `/[div]/ratings` (d1, d2, d3) | Ratings | KenPom-style table: Rk, Team, Conf, W-L, AdjEM, AdjO, AdjD, AdjT, Luck, SOS, NCSOS, each with its rank, plus a 30-day trend sparkline. Conference multi-select filter, sortable columns, percentile shading, an as-of date picker reading `ratings_daily`. |
| `/[div]/factors` | Four Factors | Every team's four factors on offense and defense, with percentile chips; season / last 10 / conference-only and adjusted / raw toggles |
| `/[div]/players` | Players | Division leaderboard (at least 40% of team minutes), filterable by conference, class and position |
| `/[div]/conferences` | Conferences | Conferences ranked by average AdjEM, with best team, worst team and projected champion |
| `/[div]/conf/[slug]` | Conference | Standings, projected final standings with title and seed odds, conference-only four factors, the conference tournament bracket simulation in late February |
| `/team/[slug]` | Team | Tabs: Overview, Four Factors, Players, Schedule (see Design details) |
| `/game?id=` | Game | Matchup view before the game; box score and actual vs. expected four factors after (see Design details) |
| `/tournament` | Tournament | See Tournament mode |
| `/methodology`, `/status` | Notes and data health | Numbered methodology cards (as on tournament.jfwiii.net). Last update time, coverage % by division, open data issues. |

**Build approach**

- The Next.js static export holds only the site's code and page templates. Netlify builds and deploys it only when the code changes, never nightly.
- Every page loads its data from nightly JSON under a configurable base URL (default: the repo's GitHub Pages address): `/ratings/d1.json`, `/teams/{slug}.json`, `/games/{id}.json`, and so on. Pages show a skeleton for the split second while data loads.
- Team and conference routes are generated from the team list at deploy time. A team new to a division falls back to `/team?slug=` until the next deploy.
- Sorting and filtering happen client-side. Shared components: `RatingsTable`, `FactorPanel`, `ContributionBars`, `MatchupMatrix`, `PercentileChip`, `PlayerTable`, `WatchCard`, `Sparkline`.

## Design details

### Look and feel

Pages are dense and numbers-first, in the tournament.jfwiii.net style: dark, as compact as KenPom's tables, with CTG-style percentile chips and a standard abbreviation on every stat.

- **Theme:** tournament.jfwiii.net's dark palette, fonts, uppercase section labels, numbered method cards and glyph markers (■ ● ▼ ★). Colors live in CSS variables, so a light theme is a small add-on later.
- **Numbers:** fixed-width digits, right-aligned. One decimal for ratings and percentages (eFG% 54.3), three for win probability only where it shows as a decimal. Each rank sits small and grey right after its value, KenPom-style.
- **Percentile color:** one colorblind-safe diverging scale, blue (bottom) → neutral grey (middle) → orange (top), never red/green. The scale follows what's good, not the raw number: a low TOV% shades orange.
- **Labels:** D1 / D2 / D3 as small pills; conference short names link to conference pages.
- **Definitions:** every column header shows its definition on hover or tap, all pulled from one glossary file so the wording never drifts.
- **Honesty about samples:** values based on fewer than 5 games are greyed with a "small sample" note; rank ranges appear on hover.
- **Mobile:** sticky team or player column with horizontal scroll on wide tables; Tonight becomes a stack of cards.

### Stat names

Every stat uses the standard abbreviation, and these definitions feed the glossary file behind the hover text. Most are the KenPom/Oliver set. Floor% and the factor contributions are the less common additions that earn their place.

**Team**

| Abbrev | Name | Definition |
| --- | --- | --- |
| AdjEM | Adjusted efficiency margin | AdjO − AdjD: points per 100 possessions better than an average team in the division, neutral court |
| AdjO / AdjD | Adjusted offensive / defensive efficiency | Points scored / allowed per 100 possessions against an average division opponent |
| AdjT | Adjusted tempo | Possessions per 40 minutes against an average opponent |
| ORtg / DRtg | Raw offensive / defensive rating | Points scored / allowed per 100 possessions, unadjusted |
| eFG% | Effective field goal % | (FGM + 0.5 × 3PM) / FGA |
| TOV% | Turnover % | TOV / Poss |
| ORB% | Offensive rebound % | OREB / (OREB + opponent DREB) |
| FTr | Free throw rate | FTM / FGA |
| 3PAr | Three-point attempt rate | 3PA / FGA |
| 3P% · 2P% · FT% | Shooting percentages | Makes / attempts |
| AST% | Assist rate | AST / FGM |
| BLK% | Block % | Blocks / opponent 2PA |
| STL% | Steal % | Steals / opponent possessions |
| Factor contribution | Points per 100 from each factor | (Team factor − division average) × that factor's regression weight; the eight contributions (offense and defense) add up to roughly AdjEM |
| SOS · NCSOS | Strength of schedule | Average opponent AdjEM; all games · non-conference only |
| Luck | Luck | Actual wins − the sum of pregame win probabilities |

**Player**

| Abbrev | Name | Definition |
| --- | --- | --- |
| Min% | Minutes % | Minutes / (team minutes ÷ 5) |
| ORtg | Offensive rating (Oliver) | Points produced per 100 possessions used |
| Usg% | Usage | Share of team possessions he uses (FGA, 0.475 × FTA, TOV) while on the floor; KenPom's %Poss |
| %Shots | Shot share | Share of team FGA while on the floor |
| Floor% | Floor percentage (Oliver) | Scoring possessions / possessions used: how often he produces at least one point |
| eFG% · TS% | Shooting efficiency | TS% = PTS / (2 × (FGA + 0.44 × FTA)), the standard definition |
| ORB% · DRB% | Rebound % | Share of available rebounds grabbed while on the floor |
| AST% | Assist % | Share of teammates' FGM he assisted while on the floor |
| TOV% | Turnover % | TOV / (FGA + 0.44 × FTA + TOV) |
| BLK% · STL% | Block % · Steal % | Blocks / opponent 2PA · steals / opponent possessions, while on the floor |
| FC/40 | Fouls committed per 40 | PF × 40 / minutes |
| FTr · 3PAr | Rates | FTM / FGA · 3PA / FGA |

Phase 5 adds the play-by-play stats:

- **FD/40** (fouls drawn per 40 minutes)
- **Shot mix and FG%** at the rim, on 2-point jumpers and on 3s, plus transition share
- **On/off:** the team's ORtg, DRtg and net rating with the player on vs. off the floor. Each split shows its possession count so small samples are obvious, and only games whose PBP passes the substitution check are used.
- **BPM-style rating:** a box-score plus-minus estimate. It ships only if it projects next-season impact better than ORtg and Usg% together.

Individual box-score DRtg is left out on purpose. Beyond rebounds, blocks and steals, box-score defense is mostly noise, which is why on/off DRtg is the defensive number worth waiting for.

### Team page

The Four Factors tab is the heart of the team page: offense and defense side by side with value, rank and percentile, then an Oliver-style breakdown of where the team's margin comes from.

- **Header strip** on every tab: team, division and conference pills, W-L, and AdjEM, AdjO, AdjD, AdjT with ranks.
- **Toggles:** Season / Last 10 / Conference, and Adjusted / Raw. Both change every number and percentile on the tab.
- **Factor panel:** offense on the left, defense (opponent values) on the right. Rows: Efficiency, eFG%, TOV%, ORB%, FTr. Each row shows value, rank of N and a percentile chip.
- **Where the margin comes from:** a diverging bar per factor, in points per 100 possessions vs. the division average, using the regression weights from the methodology. CTG doesn't show this; it turns the four factors into one picture of why a team is good.
- **Shooting and style strip:** 3PAr, 3P%, 2P%, FT%, AST%, BLK%, STL%, AdjT, each with a percentile chip.
- **Other tabs:** Overview (rating trend, last 5 results, next 3 games with Watch Scores), Players (see Player table), Schedule (each game with opponent rank, predicted vs. actual margin, and game-level four factors).

```
Team page — Four Factors tab (wireframe; values are placeholders)

┌ Team name  [D2] [Conf]            W-L · AdjEM · AdjO · AdjD · AdjT, each with rank ┐
[Season | Last 10 | Conference]                                  [Adjusted | Raw]

┌ OFFENSE ─────────────────────────┐  ┌ DEFENSE ─────────────────────────┐
│ Stat              Value Rk  Pctl │  │ Stat              Value Rk  Pctl │
│ Efficiency (AdjO)  ##.# ##  ████░│  │ Efficiency (AdjD)  ##.# ##  ███░░│
│ eFG%               ##.# ##  ███░░│  │ Opp eFG%           ##.# ##  ████░│
│ TOV%               ##.# ##  █░░░░│  │ Opp TOV%           ##.# ##  █░░░░│
│ ORB%               ##.# ##  ████░│  │ Opp ORB%           ##.# ##  ██░░░│
│ FTr                ##.# ##  ██░░░│  │ Opp FTr            ##.# ##  ███░░│
└──────────────────────────────────┘  └──────────────────────────────────┘

┌ Where the margin comes from (pts/100 vs. division average) ───────────────┐
│ Off eFG%                        |██████         (orange = adds to margin)   │
│ Off TOV%                     ███|                (blue = costs margin)       │
│ Off ORB%                        |████                                        │
│ Off FTr                         |█                                           │
│ Def eFG%                        |█████                                       │
│ Def TOV%                      ██|                                            │
│ Def ORB%                    ████|                                            │
│ Def FTr                         |██                                          │
│ The eight bars add up to roughly AdjEM                                       │
└──────────────────────────────────────────────────────────────────────────────┘

[ 3PAr | 3P% | 2P% | FT% | AST% | BLK% | STL% | AdjT ]  each with value + percentile chip
```

In the build, each grey bar becomes a value with its rank, and each chip fills to the team's percentile in the blue-to-orange scale.

### Game page: matchup view

The game page sets each offense against the other team's defense, factor by factor, as CTG does, and adds a projected value for this game.

- Two blocks: *Team A offense vs. Team B defense*, then the reverse.
- Rows: Efficiency, eFG%, TOV%, ORB%, FTr, 3PAr, Tempo.
- Each row shows: offense value and percentile chip · an edge marker (◀ or ▶) pointing at whichever side ranks higher · defense value and chip · **Projected** for this game, from the same additive model (division average + offense effect − defense effect).
- The row with the widest percentile gap is highlighted as "the matchup to watch." That same gap feeds the Unstoppable vs. Immovable tag.
- Above the blocks: projected score, win probability, projected possessions, and Watch Score with its component bars.
- After the game: box score, actual vs. projected for each factor (so you can see which factor decided it), and Excitement.

### Player table

The team Players tab and the division leaderboard share one table, laid out like KenPom's, with Oliver's skill-versus-workload view on top.

- **Columns:** Player · Cls · Pos · Ht · G · Min% · ORtg · Usg% · %Shots · Floor% · eFG% · TS% · ORB% · DRB% · AST% · TOV% · BLK% · STL% · FC/40 · FTr · FT% · 2P% · 3P% · 3PAr. Each shooting % shows makes-attempts underneath in grey.
- **Views:** Advanced (default) | Per 40 | Per game | Totals. Sorted by Min% by default; every column sorts.
- **Usage-efficiency chart** above the table: ORtg against Usg% for every rotation player, with dot size = Min%. It shows who carries the load and who is efficient only at low usage.
- Players under 10% of minutes collapse into a "Bench" row that can be expanded. The division leaderboard requires at least 40% of team minutes.
- Percentile chips compare each player with qualified players in the same division.

### Downloads

Every team and player table has a Download CSV button, and full-division files are published nightly for bulk use. Both cost nothing to serve.

- **Button:** exports the table exactly as shown, with current filters, sort, splits and toggles. It's built in the browser from data already loaded.
- **Bulk files** under the same data URL, refreshed nightly: `/csv/{season}/{div}/teams.csv` (ratings plus raw and adjusted four factors), `players.csv`, `games.csv` (one row per team-game box score) and `schedule.csv` (with predictions and Watch Scores).
- Column headers use the glossary abbreviations, and a `glossary.csv` beside them lists the definitions.
- Past seasons stay downloadable once they're backfilled. A Downloads link in the footer lists every file.

## Tournament mode

Tournament mode brings tournament.jfwiii.net into the site as a template that rebuilds itself. Brackets load automatically, and the 200,000-run simulation reruns after every round, with no hand updates.

The saved tournament.jfwiii.net page is in `reference/tournament/`: the source for its sections, simulation logic and look.

- **Conference tournament week (late February to Selection Sunday):** each conference page gets a bracket simulation with title and auto-bid odds. Tonight ranks tournament games higher through the Stakes component.
- **Switch-over:** when the ncaa.com brackets endpoint returns a field for the current season, `/` redirects to `/tournament` until the day after the title game. A config date override is kept as a backup.
- **D1 tournament:** keep the existing sections (Methodology, Team Probabilities, Full Bracket, Path Analysis, TV Guide) and the stage selector. Build with the 50% KenPom / 25% Torvik / 25% Massey composite and show the site's own AdjEM as a comparison column. Whether the site's rating joins the composite is decided before March (see Open questions). TV Guide matchup matrices use the site's own adjusted factors.
- **D2 tournament:** the same template at `/tournament/d2`, with the D2 field of 64 in eight regions. The simulation gives home court to regional hosts. The composite is the site's own AdjEM blended with Massey.
- **D3:** the same template, only if D3 passes Phase 0.
- **Value Report / public pick rates:** left out of the public build, as on the current public tournament site. A private flag can turn it back on.
- **Regenerated by the nightly job:** sim results by stage, so the stage selector history is kept automatically.

## Operations

The main job runs at about 4:15 a.m. ET from November through early April, and a catch-up pass runs at about 10:15 a.m. ET. Everything is rebuilt from scratch each night and published as JSON, so the site is current by breakfast and stays at $0.

**Why those times**

- **4:15 a.m.:** the latest West Coast tips end around 1 a.m. ET, and games in Hawaii (Big West in D1; Hawaii-based schools in D2) can end near 3 a.m. ET. Box scores usually post within an hour of the final, so this leaves margin and still updates before morning.
- **10:15 a.m.:** D2 and D3 sports information directors often file box scores and corrections the next morning. This pass picks those up.
- **Off the hour:** GitHub schedules can start late when load is heavy, and runs scheduled on the hour are the most crowded. So the cron lines are `17 9 * * *` and `17 15 * * *` (UTC).
- **Daylight saving:** GitHub cron runs in UTC, so after DST starts on March 14, 2027 the runs move to 5:17 and 11:17 a.m. ET. That's still fine for the tournament.

**Running it**

- **Schedule:** the two daily runs from November to early April; the full schedule refreshes every Monday. From April to October it runs weekly for roster and schedule updates only.
- **Idempotent:** every write is an upsert keyed on source and source\_game\_id. Each run re-fetches the previous 3 days to pick up stat corrections, and each team-game box is overwritten whole.
- **Recompute:** all ratings, factors, predictions, simulations and Watch Scores are rebuilt every night. No incremental math to drift.
- **Validation, written to `game_quality`:** box totals must equal the final score; both team boxes present; possessions between 50 and 95; no duplicate games. Failures are kept out of the rating fit until fixed and listed on `/status`.
- **Crosswalk:** `teams.csv` plus `team_aliases`. An unmatched team name fails the run into `unmatched.csv`, so new names get mapped instead of quietly becoming duplicate teams.
- **Storage:** the SQLite file is a GitHub Release asset (`db-latest` plus 14 dated backups), downloaded at job start and re-uploaded at the end. Raw PBP is bundled into one gzipped file per day, also a Release asset. Cloudflare R2 is the fallback if Releases get clumsy.
- **Publish:** the job writes the night's JSON and CSV to a `gh-pages` branch, force-pushed each night so git history doesn't grow, and GitHub Pages serves it. Netlify redeploys only when the site code changes.
- **Alerting:** a failed workflow emails the repo owner (GitHub default). Each run writes a summary to `ingest_runs`, shown on `/status`. Coverage below 90% for any division also fails the run on purpose.
- **Etiquette:** throttle to 2 requests/second per host, cache responses, and send a descriptive user agent.

**Operating cost: $0 a month, with a public repo**

| Service | Role | Allowance | Expected use | Added cost |
| --- | --- | --- | --- | --- |
| GitHub Actions | Nightly pipeline | Unlimited on public repos ([GitHub pricing](https://dev.to/kalemi/how-github-actions-pricing-works-m0o)) | Estimated 15–20 min/day in season | $0, and none of the account's private-repo Actions minutes |
| GitHub Releases | SQLite file + raw PBP archive | Free | Under 1 GB per season | $0 |
| GitHub Pages | Nightly JSON + CSV | Free for public repos | Tens of MB, replaced nightly | $0 |
| Netlify | Site code only | Free tier is enough: a few deploys a month, on code changes only | Free tier | $0 |
| Cloudflare R2 (fallback only) | Storage if Releases get clumsy | 10 GB, 1M writes, 10M reads/month, free egress ([R2 pricing](https://developers.cloudflare.com/r2/pricing/)) | 1–2 GB | $0 |
| Rating inputs for March | Tournament composite | KenPom's front-page ratings table, Torvik's data files and Massey are all free | Daily pull in March | $0 |

The repo is public so the pipeline's Actions minutes are unlimited and the nightly data can be served from GitHub Pages. Nothing in the repo is sensitive. Any keys live in GitHub Actions secrets, and the data is public on the site anyway. Keeping nightly data off Netlify also means the site never needs more than Netlify's free tier.

## Build phases

The goal is a working site as soon as possible. The first usable version ships before the ratings are fully tuned, and later phases improve it in place. There are no date targets. The one fixed deadline comes from the calendar: tournament mode has to be live before conference tournaments start in late February. Each phase ends with a gate that has to pass before the next one starts.

1. **Phase 0 — Coverage check:** the source test in Data sources, on Nov 16–22, 2026 plus one 2025–26 week.
   - Gate: a coverage report per division and week; D3 in or out; fallback parsers needed or not; cloud-IP check passed.
2. **Phase 1 — Current-season pipeline:** schema, crosswalk, ncaa.com ingester, validation, raw PBP archiving, nightly workflow, Release-asset storage, JSON and CSV publish to GitHub Pages. Load 2026–27 to date and backfill 2025–26, which the preseason prior needs.
   - Gate: 3 straight clean nightly runs with ≥ 98% box coverage in D1 and D2, and zero unmatched teams.
3. **Phase 2 — Working site (beta):** ratings with sensible default settings, four factors and their weights, predictions, Watch Score; the Ratings, Four Factors, Team and Tonight pages; CSV buttons; status page. A "beta" badge marks ratings as untuned.
   - Gate: live at cbb.jfwiii.net and updating every night.
4. **Phase 3 — Tuning:** backfill 2024–25; backtest harness; additive vs. log fit; tune on 2024–25, test on 2025–26, then re-tune on both; uncertainty bands; returning-minutes prior.
   - Gate: MAE, log loss, Brier and calibration plot reported per division; beats both baselines; D1 rank correlation with KenPom's final 2025–26 ratings of at least 0.95. The beta badge comes off.
5. **Phase 4 — Rest of the site and tournament mode:** Players, Conferences with simulations, Game pages, methodology page, a Watch Score tuning pass after a week of real use of Tonight, conference tournament sims, D1 and D2 NCAA templates ported from tournament.jfwiii.net, automatic switch-over, and the D1 composite decision. Tournament mode has to be done before conference tournaments start.
   - Gate: a full dry run against the 2026 D1 and D2 brackets reproduces the live site's stage tables.
6. **Phase 5 — Play-by-play:** event parser per source format, `game_quality` PBP checks, garbage-time trimming in the ratings, shot-type and transition splits, FD/40, on/off ORtg / DRtg / net rating, the BPM-style rating (tested against ORtg + Usg% on next-season impact), post-game Excitement and the Watch Score archive.
   - Gate: on D1, PBP-derived points match the box score in at least 99% of games before any PBP stat is shown.

## Open questions

Settled so far, and now in Decisions locked: name, timeline, FT rate, D2 bid proxy, TV/stream source, women's basketball (later, via a `sport` column), Watch Score weights and tags, CSV downloads, the BPM-style rating in Phase 5, and a public repo with data on GitHub Pages.

- [ ] **Before conference tournaments: the D1 tournament composite.** Should the site's own rating join it? KenPom stays in regardless, as the industry standard. Default: keep 50/25/25 KenPom/Torvik/Massey with the site's AdjEM shown beside it, and decide with the Phase 3 backtest numbers.

## Sources

- [henrygd/ncaa-api](https://github.com/henrygd/ncaa-api): ncaa.com route pattern, self-hosting and rate limits
- [SELECT \* — NCAA stats scraping blocked](https://buttondown.com/dwillis/archive/rails-against-the-machine/)
- [ESPN scoreboard `groups=50` for all D1 games](https://coderapp.vercel.app/answer/69953440)
- KenPom: [ratings explanation](https://kenpom.com/blog/ratings-explanation/), [methodology update](https://kenpom.com/blog/ratings-methodology-update/), [runaway margins](https://kenpom.com/blog/?p=267), [preseason influence](https://kenpom.com/blog/?p=473), [garbage time](https://kenpom.com/blog/?p=1921)
- Torvik: [T-Rank FAQ](https://adamcwisports.blogspot.com/p/every-possession-counts.html), [DBR forum summary of T-Rank's game control](https://forums.dukebasketballreport.com/forums/showthread.php?p=1124670)
- Costs: [Netlify credits](https://docs.netlify.com/manage/accounts-and-billing/billing/billing-for-credit-based-plans/how-credits-work/), [Netlify legacy plans](https://docs.netlify.com/manage/accounts-and-billing/billing/billing-for-legacy-plans/legacy-pricing-plans/), [GitHub Actions pricing explainer](https://dev.to/kalemi/how-github-actions-pricing-works-m0o), [Cloudflare R2 pricing](https://developers.cloudflare.com/r2/pricing/)
- [tournament.jfwiii.net](https://tournament.jfwiii.net) and [watchingbaseball.com](https://watchingbaseball.com): existing sites this builds on
