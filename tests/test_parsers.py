import json
import sqlite3
import unittest
from pathlib import Path

from pipeline import config, crosswalk, db
from pipeline.ncaa import (game_status, name_key, parse_boxscore, parse_minutes, pbp_summary,
                           possessions)

FIX = Path(__file__).parent / "fixtures"


class ParserTests(unittest.TestCase):
    def test_boxscore(self):
        box = parse_boxscore(json.loads((FIX / "boxscore_6660932.json").read_text()))
        self.assertEqual(set(box), {"westfield-st", "purchase-st"})
        w = box["westfield-st"]["team"]
        self.assertEqual((w["fgm"], w["fga"], w["pts"], w["oreb"], w["dreb"]), (24, 67, 63, 18, 19))
        self.assertTrue(box["purchase-st"]["players"])

    def test_possessions(self):
        b = {"fga": 67, "oreb": 18, "tov": 18, "fta": 25}
        self.assertAlmostEqual(possessions(b), 67 - 18 + 18 + 0.475 * 25)
        self.assertIsNone(possessions({"fga": None, "oreb": 1, "tov": 1, "fta": 1}))

    def test_pbp_summary(self):
        s = pbp_summary(json.loads((FIX / "pbp_6660932.json").read_text()))
        self.assertEqual(s["events"], 202)
        self.assertTrue(s["has_subs"])
        self.assertEqual(s["periods"], 2)

    def test_minutes_status_names(self):
        self.assertEqual(parse_minutes("8"), 8.0)
        self.assertAlmostEqual(parse_minutes("12:30"), 12.5)
        self.assertIsNone(parse_minutes(""))
        self.assertEqual(game_status({"gameState": "final", "finalMessage": "FINAL"}), "final")
        self.assertEqual(game_status({"gameState": "pre", "finalMessage": "Canceled"}), "canceled")
        self.assertEqual(name_key("Jo-Ann", "O'Neil Jr."), "joannoneiljr")

    def test_season(self):
        import datetime as dt
        self.assertEqual(config.season_for(dt.date(2026, 11, 18)), 2027)
        self.assertEqual(config.season_for(dt.date(2026, 1, 15)), 2026)


class CrosswalkTests(unittest.TestCase):
    def setUp(self):
        self.conn = db.connect(":memory:")
        self.conn.execute("INSERT INTO teams (team_id, name, slug) VALUES (1, 'Harding', 'harding')")
        self.conn.execute("INSERT INTO team_aliases VALUES ('ncaa', 'harding', 1, 'teams.csv')")

    def test_resolve(self):
        r = crosswalk.resolve_ncaa
        self.assertEqual(r(self.conn, {"seo": "harding"}, "gac"), (1, "matched"))
        self.assertEqual(r(self.conn, {"seo": "new-d2-school"}, "gac"), (None, "unmatched"))
        tid, how = r(self.conn, {"seo": "some-naia", "short": "Some NAIA"}, "")
        self.assertEqual(how, "auto-non-ncaa")
        kind = self.conn.execute("SELECT kind FROM teams WHERE team_id=?", (tid,)).fetchone()[0]
        self.assertEqual(kind, "non-ncaa")
        self.assertEqual(r(self.conn, {"seo": "some-naia"}, ""), (tid, "matched"))

    def test_norm(self):
        self.assertTrue(crosswalk.norm_names("Central Conn. St.") & crosswalk.norm_names("Central Connecticut State"))


if __name__ == "__main__":
    unittest.main()
