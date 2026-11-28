"use client";
import { useData } from "@/lib/data";
import { Loading } from "@/components/ui";

export default function Status() {
  const s = useData("status.json");
  return (
    <>
      <div className="sh"><div className="st">Data status</div>
        <div className="sd">Nightly pipeline health: coverage by division, recent runs and games kept out of the ratings until fixed.</div></div>
      <Loading state={s}>{(d) => (
        <>
          <div className="lbl">Generated {new Date(d.generated_at).toLocaleString("en-US", { timeZone: "America/New_York" })} ET · season {d.season - 1}–{String(d.season).slice(2)}</div>
          <div className="h2">Coverage this season</div>
          <div className="tw"><table>
            <thead><tr><th className="tl">Division</th><th>Finals</th><th>Box score</th><th>Play-by-play</th><th>OK for ratings</th></tr></thead>
            <tbody>{Object.entries(d.season_coverage).map(([div, c]) => (
              <tr key={div}><td className="tl">{div.toUpperCase()}</td><td>{c.finals}</td><td>{c.box_pct}%</td><td>{c.pbp_pct}%</td><td>{c.ratings_ok_pct}%</td></tr>))}
            </tbody></table></div>
          <div className="h2">Recent runs</div>
          <div className="tw"><table>
            <thead><tr><th className="tl">Started (UTC)</th><th className="tl">Mode</th><th className="tl">Window</th><th>Finals</th><th>With box</th><th>Errors</th><th>Minutes</th><th>OK</th></tr></thead>
            <tbody>{d.recent_runs.map((r) => (
              <tr key={r.run_id}><td className="tl">{r.started_at}</td><td className="tl">{r.mode}</td><td className="tl">{r.window_start} → {r.window_end}</td>
                <td>{r.games_expected}</td><td>{r.games_fetched}</td><td>{r.error_count || 0}</td><td>{r.duration_sec ? (r.duration_sec / 60).toFixed(1) : "—"}</td><td>{r.ok ? "■" : "▼"}</td></tr>))}
            </tbody></table></div>
          <div className="h2">Open data issues ({d.open_issues.length})</div>
          <div className="tw"><table>
            <thead><tr><th className="tl">Date</th><th className="tl">Game</th><th className="tl">Div</th><th className="tl">Issue</th></tr></thead>
            <tbody>{d.open_issues.map((i) => (
              <tr key={i.game_id}><td className="tl">{i.date}</td><td className="tl">{i.away} @ {i.home}</td><td className="tl">{i.listed_in}</td><td className="tl">{i.issues || "no play-by-play"}</td></tr>))}
            </tbody></table></div>
        </>
      )}</Loading>
    </>
  );
}
