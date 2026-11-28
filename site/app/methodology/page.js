export const metadata = { title: "Methodology · jfwiii.net College Basketball" };

const STEPS = [
  ["Data", "Every D1, D2 and D3 game from ncaa.com's scoreboards, box scores and play-by-play, pulled nightly at about 4:17 a.m. ET with a 10:17 a.m. catch-up. ESPN cross-checks D1 and supplies neutral sites and TV."],
  ["Validation", "Box totals must match the final score, both team boxes must exist, possessions must be plausible, and duplicates are dropped. Failing games stay out of the ratings and are listed on the status page."],
  ["Ratings", "For each division separately, a ridge regression fits points per 100 possessions = average + offense − opponent defense + home court. Games against other divisions count toward records only. Every rating carries an uncertainty band."],
  ["Early season", "Each team starts from last season's final rating, pulled toward its conference mean, and its own games take over as they accumulate. Older games count a little less, and runaway margins are down-weighted."],
  ["Predictions", "Possessions come from both teams' adjusted tempo, the margin from the AdjEM gap plus home court, and the win probability from a normal curve fit to each division. Predictions are frozen before tip."],
  ["Four factors", "eFG%, TOV%, ORB% and FTr (FTM / FGA), raw and opponent-adjusted. Each division gets its own weights from a regression of efficiency on the factors; they drive the 'where the margin comes from' chart."],
  ["Watch Score", "35% a close game between good teams, 35% style extremes and notable pairings, 30% stakes (conference title odds for now; NCAA bids and seeds arrive with tournament mode)."],
  ["Beta", "Settings are sensible defaults for now. A time-ordered backtest on 2024–25 and 2025–26 tunes them next, and the beta badge comes off once the ratings beat the baselines."],
];

export default function Methodology() {
  return (
    <>
      <div className="sh"><div className="st">Methodology</div></div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(300px,1fr))", gap: 12 }}>
        {STEPS.map(([t, b], i) => (
          <div key={t} className="panel">
            <div style={{ fontFamily: "'Bebas Neue',sans-serif", fontSize: 28, color: "var(--gold)", lineHeight: 1 }}>{i + 1}</div>
            <div style={{ fontWeight: 600, margin: "4px 0" }}>{t}</div>
            <div className="sd" style={{ fontSize: 12 }}>{b}</div>
          </div>
        ))}
      </div>
    </>
  );
}
