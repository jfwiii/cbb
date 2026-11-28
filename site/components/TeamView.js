"use client";
import { useMemo, useState } from "react";
import Link from "next/link";
import { f1, fmtDate, pct0, signed, tipET, useData } from "@/lib/data";
import { BetaBadge, CsvButton, DivPill, Loading, PercentileChip, Sparkline, TeamLink, Th, ValRank } from "@/components/ui";
import { FACTORS } from "@/components/FactorsView";
import PlayerTable from "@/components/PlayerTable";

const TABS = ["Overview", "Four Factors", "Players", "Schedule"];

export default function TeamView({ slug }) {
  const state = useData(`teams/${slug}.json`);
  const [tab, setTab] = useState("Four Factors");
  return (
    <Loading state={state}>
      {(d) => (
        <>
          <Header d={d} />
          <div className="fbtns" style={{ margin: "6px 0 16px" }}>
            {TABS.map((t) => <button key={t} className={`fb ${tab === t ? "on" : ""}`} onClick={() => setTab(t)}>{t}</button>)}
          </div>
          {tab === "Overview" && <Overview d={d} />}
          {tab === "Four Factors" && <FactorsTab d={d} />}
          {tab === "Players" && <PlayerTable players={d.players} team={d.team} />}
          {tab === "Schedule" && <Schedule d={d} />}
        </>
      )}
    </Loading>
  );
}

function Header({ d }) {
  const r = d.ratings, t = d.team;
  const S = ({ k, v, rk, title }) => (
    <div className="stat" title={title}><div className="k">{k}</div><div className="v"><ValRank v={v} rk={rk} /></div></div>
  );
  return (
    <>
      <div className="thead">
        <span className="tname">{t.name}</span>
        <DivPill div={t.div} />
        {t.conf ? <span className="conf">{t.conf_name || t.conf.toUpperCase()}</span> : null}
        <BetaBadge />
      </div>
      <div className="strip">
        <div className="stat"><div className="k">Record</div><div className="v">{d.record.w}-{d.record.l}<span className="rk">{d.record.cw}-{d.record.cl} conf</span></div></div>
        <div className="stat" title={`80% rank range ${r.rk_lo}–${r.rk_hi} of ${r.n_teams}`}><div className="k">Rank</div><div className="v">{r.rk}<span className="rk">of {r.n_teams}</span></div></div>
        <div className="stat"><div className="k">AdjEM</div><div className="v">{signed(r.em)}<span className="rk">±{f1(r.se)}</span></div></div>
        <S k="AdjO" v={r.o} rk={r.o_rk} />
        <S k="AdjD" v={r.d} rk={r.d_rk} />
        <S k="AdjT" v={r.t} rk={r.t_rk} />
        <div className="stat"><div className="k">SOS</div><div className="v">{signed(r.sos)}</div></div>
        <div className="stat"><div className="k">Luck</div><div className="v">{signed(r.luck, 2)}</div></div>
      </div>
      {r.g < 5 ? <div className="disc">Small sample: {r.g} in-division games so far.</div> : null}
    </>
  );
}

function Overview({ d }) {
  const finals = d.schedule.filter((g) => g.status === "final");
  const next = d.schedule.filter((g) => g.status === "scheduled").slice(0, 3);
  return (
    <div className="panels">
      <div className="panel">
        <h3>Rating trend</h3>
        <Sparkline values={d.trend.map((x) => x[1])} w={420} h={80} />
        <div className="lbl" style={{ marginTop: 6 }}>AdjEM by date · now {signed(d.ratings.em)} (#{d.ratings.rk})</div>
      </div>
      <div className="panel">
        <h3>Last 5</h3>
        <GameList games={finals.slice(-5).reverse()} />
        <h3 style={{ marginTop: 14 }}>Next 3</h3>
        {next.length ? <GameList games={next} /> : <div className="muted">No scheduled games in the data yet.</div>}
      </div>
    </div>
  );
}

function GameList({ games }) {
  return (
    <table><tbody>
      {games.map((g) => (
        <tr key={g.id}>
          <td className="tl">{fmtDate(g.date, { month: "short", day: "numeric" })}</td>
          <td className="tl">{g.site === "A" ? "@" : g.site === "N" ? "vs" : ""} <TeamLink slug={g.opp.slug} name={g.opp.name} />{g.opp.rk ? <span className="rk">#{g.opp.rk}</span> : null}</td>
          <td>{g.status === "final" ? `${g.pts > g.opp_pts ? "W" : "L"} ${g.pts}-${g.opp_pts}` : tipET(g.tip)}</td>
          <td>{g.status === "final" ? "" : g.win_prob != null ? pct0(g.win_prob) : ""}</td>
          <td>{g.status !== "final" && g.watch != null ? <span title="Watch Score" style={{ color: "var(--gold)" }}>★ {Math.round(g.watch)}</span> : ""}</td>
        </tr>
      ))}
    </tbody></table>
  );
}

const SPLITS = [["season", "Season"], ["last10", "Last 10"], ["conference", "Conference"]];
const CONTRIB_LABEL = { efg: "eFG%", tov: "TOV%", orb: "ORB%", ftr: "FTr" };

function FactorsTab({ d }) {
  const [split, setSplit] = useState("season");
  const [adj, setAdj] = useState(true);
  const f = d.factors;
  if (!f) return <div className="disc">No in-division games yet.</div>;
  const useAdj = adj && split === "season";
  const cell = (side, k) => (useAdj ? f.adjusted[side][k] : f[split][side][k]) || [null, null, null];
  const rows = [["eff", useAdj ? "Efficiency (Adj)" : "Efficiency"], ...FACTORS];
  const Panel = ({ side, title }) => (
    <div className="panel">
      <h3>{title}</h3>
      <table>
        <thead><tr><Th k="Stat" left /><Th k="Value" /><Th k="Rk" /><Th k="Pctl" /></tr></thead>
        <tbody>
          {rows.map(([k, label]) => {
            const [v, rk, p] = cell(side, k);
            const name = side === "def" && k !== "eff" ? `Opp ${label}` : label;
            return <tr key={k}><td className="tl">{name}</td><td>{f1(v)}</td><td className="muted">{rk ?? "—"}</td><td><PercentileChip p={p} /></td></tr>;
          })}
        </tbody>
      </table>
    </div>
  );
  const contrib = d.contrib || {};
  const maxAbs = Math.max(4, ...Object.values(contrib).map((x) => Math.abs(x || 0)));
  const ex = f.extra || {};
  const strip = [["tpar", "3PAr"], ["tp", "3P%"], ["twop", "2P%"], ["ft", "FT%"], ["ast", "AST%"], ["blk", "BLK%"], ["stl", "STL%"], ["tempo", "Tempo"]];
  return (
    <>
      <div className="tbl-ctrl">
        <div className="fbtns">{SPLITS.map(([k, l]) => <button key={k} className={`fb ${split === k ? "on" : ""}`} onClick={() => setSplit(k)}>{l} ({f[k].g})</button>)}</div>
        <div className="fbtns">
          <button className={`fb ${useAdj ? "on" : ""}`} disabled={split !== "season"} onClick={() => setAdj(true)}>Adjusted</button>
          <button className={`fb ${!useAdj ? "on" : ""}`} onClick={() => setAdj(false)}>Raw</button>
        </div>
      </div>
      <div className="panels">
        <Panel side="off" title="Offense" />
        <Panel side="def" title="Defense" />
      </div>
      <div className="panel" style={{ marginTop: 14 }}>
        <h3>Where the margin comes from <span className="lbl">pts/100 vs. division average</span></h3>
        <div className="contrib">
          {["off", "def"].flatMap((side) => Object.keys(CONTRIB_LABEL).map((k) => {
            const v = contrib[`${side}_${k}`];
            if (v == null) return null;
            const w = (Math.abs(v) / maxAbs) * 50;
            return [
              <span key={side + k + "l"}>{side === "off" ? "Off" : "Def"} {CONTRIB_LABEL[k]}</span>,
              <span key={side + k + "b"} className="cbar">
                <span className="axis" />
                <span className="seg" style={{ left: v >= 0 ? "50%" : `${50 - w}%`, width: `${w}%`, background: v >= 0 ? "var(--orange)" : "var(--blue)" }} />
              </span>,
              <span key={side + k + "v"} style={{ textAlign: "right" }}>{signed(v)}</span>,
            ];
          }))}
        </div>
        <div className="lbl" style={{ marginTop: 8 }}>
          Sum {signed(Object.values(contrib).reduce((a, b) => a + (b || 0), 0))} vs AdjEM {signed(d.ratings.em)} · orange adds to the margin, blue costs it · weights from this division's regression
        </div>
      </div>
      <div className="strip" style={{ marginTop: 14 }}>
        {strip.map(([k, l]) => {
          const [v, rk, p] = ex[k] || [];
          return <div key={k} className="stat"><div className="k">{l}</div><div className="v">{f1(v)}{rk ? <span className="rk">{rk}</span> : null}</div>{p != null ? <PercentileChip p={p} /> : null}</div>;
        })}
      </div>
    </>
  );
}

function Schedule({ d }) {
  const rows = d.schedule;
  const csv = () => rows.map((g) => [g.date, g.site, g.opp.name, g.status, g.pts, g.opp_pts, g.pred_margin, g.margin, g.win_prob,
    g.factors?.off.efg, g.factors?.off.tov, g.factors?.off.orb, g.factors?.off.ftr, g.factors?.def.efg, g.factors?.def.tov, g.factors?.def.orb, g.factors?.def.ftr]);
  return (
    <>
      <div className="tbl-ctrl"><span className="spacer" />
        <CsvButton filename={`${d.team.slug}-schedule.csv`} header={["Date", "Site", "Opp", "Status", "PTS", "Opp PTS", "Pred margin", "Margin", "Win %", "eFG%", "TOV%", "ORB%", "FTr", "Opp eFG%", "Opp TOV%", "Opp ORB%", "Opp FTr"]} rows={csv} />
      </div>
      <div className="tw"><table>
        <thead><tr>
          <Th k="Date" left /><Th k="Opponent" left /><Th k="Result" /><Th k="Pred" title="Predicted margin (pregame)" /><Th k="Win %" />
          <Th k="Watch" /><Th k="eFG%" /><Th k="TOV%" /><Th k="ORB%" /><Th k="FTr" />
          <Th k="Opp eFG%" label="Opp eFG" /><Th k="Opp TOV%" label="Opp TOV" /><Th k="Opp ORB%" label="Opp ORB" /><Th k="Opp FTr" label="Opp FTr" />
        </tr></thead>
        <tbody>
          {rows.map((g) => {
            const fx = g.factors;
            return (
              <tr key={g.id} className={g.crossover ? "smallsample" : ""} title={g.crossover ? "Crossover / non-NCAA opponent: counts toward the record only" : undefined}>
                <td className="tl">{fmtDate(g.date, { month: "short", day: "numeric" })}</td>
                <td className="tl">{g.site === "A" ? "@ " : g.site === "N" ? "vs " : ""}<TeamLink slug={g.opp.slug} name={g.opp.name} />{g.opp.rk ? <span className="rk">#{g.opp.rk}</span> : null}{g.opp.div && g.opp.div !== d.team.div ? <> <DivPill div={g.opp.div} /></> : null}{g.conf_game ? <span className="rk">conf</span> : null}</td>
                <td>{g.status === "final" ? <span style={{ color: g.pts > g.opp_pts ? "var(--gold)" : undefined }}>{g.pts > g.opp_pts ? "W" : "L"} {g.pts}-{g.opp_pts}</span> : tipET(g.tip)}</td>
                <td>{signed(g.pred_margin)}</td>
                <td>{g.win_prob != null ? pct0(g.win_prob) : "—"}</td>
                <td>{g.watch != null ? Math.round(g.watch) : "—"}</td>
                {["efg", "tov", "orb", "ftr"].map((k) => <td key={k}>{fx ? f1(fx.off[k]) : "—"}</td>)}
                {["efg", "tov", "orb", "ftr"].map((k) => <td key={"d" + k}>{fx ? f1(fx.def[k]) : "—"}</td>)}
              </tr>
            );
          })}
        </tbody>
      </table></div>
    </>
  );
}
