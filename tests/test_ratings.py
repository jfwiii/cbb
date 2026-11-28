import datetime as dt
import random
import unittest

from pipeline import ratings


class RatingsTests(unittest.TestCase):
    def test_recovers_ordering(self):
        """Synthetic division: true margins drive scores; the fit should rank teams correctly."""
        rng = random.Random(1)
        true = {t: 4.0 * (10 - t) for t in range(10)}   # team 0 best
        games, gid = [], 0
        day = dt.date(2026, 11, 1)
        for rnd in range(12):
            order = list(range(10))
            rng.shuffle(order)
            for i in range(0, 10, 2):
                h, a = order[i], order[i + 1]
                poss = 70
                hp = round((105 + true[h] / 2 - true[a] / 2 + 3) * poss / 100 + rng.gauss(0, 5))
                ap = round((105 + true[a] / 2 - true[h] / 2 - 3) * poss / 100 + rng.gauss(0, 5))
                gid += 1
                games.append({"game_id": gid, "date": (day + dt.timedelta(days=rnd)).isoformat(),
                              "home_id": h, "away_id": a, "neutral": 0, "is_conference": 0,
                              "home_score": hp, "away_score": ap, "poss": poss, "tempo": poss})
        res = ratings.fit(games, list(range(10)), day + dt.timedelta(days=30), {})
        order = sorted(res["teams"], key=lambda t: -res["teams"][t]["adj_em"])
        # Spearman rank correlation with the truth
        d2 = sum((rank - t) ** 2 for rank, t in enumerate(order))
        rho = 1 - 6 * d2 / (10 * (10 ** 2 - 1))
        self.assertGreater(rho, 0.9)
        self.assertEqual(order[0], 0)
        self.assertGreater(res["h"], 2)          # home court recovered (true +6/100 total)
        p = ratings.predict(res, 0, 9, neutral=True)
        self.assertGreater(p["home_win_prob"], 0.8)


if __name__ == "__main__":
    unittest.main()
