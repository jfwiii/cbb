"""Single source of stat definitions (DESIGN.md, "Stat names"). Feeds glossary.csv now and
the site's hover text later."""

GLOSSARY = [
    ("G", "Games", "Games played"),
    ("W · L", "Wins · losses", "All games, including crossover and non-NCAA opponents"),
    ("ORtg", "Raw offensive rating", "Points scored per 100 possessions, unadjusted"),
    ("DRtg", "Raw defensive rating", "Points allowed per 100 possessions, unadjusted"),
    ("Tempo", "Raw tempo", "Possessions per 40 minutes, unadjusted"),
    ("Poss", "Possessions", "FGA − OREB + TOV + 0.475 × FTA (a game's possessions average both teams)"),
    ("eFG%", "Effective field goal %", "(FGM + 0.5 × 3PM) / FGA"),
    ("TOV%", "Turnover %", "TOV / Poss"),
    ("ORB%", "Offensive rebound %", "OREB / (OREB + opponent DREB)"),
    ("FTr", "Free throw rate", "FTM / FGA"),
    ("3PAr", "Three-point attempt rate", "3PA / FGA"),
    ("3P% · 2P% · FT%", "Shooting percentages", "Makes / attempts"),
    ("AST%", "Assist rate", "AST / FGM"),
    ("TS%", "True shooting %", "PTS / (2 × (FGA + 0.44 × FTA))"),
    ("Opp", "Opponent", "Prefix: the same stat for the team's opponents (defense)"),
    ("Site", "Site", "H home, A away, N neutral (neutral known for D1 games via ESPN)"),
    ("Crossover", "Crossover", "1 when the teams are in different divisions or one is non-NCAA"),
    ("QualityOK", "Quality OK", "1 when the game passes box-score validation and counts toward ratings"),
]
