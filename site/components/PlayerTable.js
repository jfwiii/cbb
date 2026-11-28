"use client";
import { useMemo, useState } from "react";
import { f1, pctColor } from "@/lib/data";
import { CsvButton, Th } from "@/components/ui";

const ADV = [
  ["min_pct", "Min%"], ["ortg", "ORtg", "Player ORtg"], ["usg", "Usg%"], ["shots", "%Shots"], ["floor", "Floor%"],
  ["efg", "eFG%"], ["ts", "TS%"], ["orb_pct", "ORB%"], ["drb_pct", "DRB%"], ["ast_pct", "AST%", "Player AST%"],
  ["tov_pct", "TOV%", "Player TOV%"], ["blk_pct", "BLK%"], ["stl_pct", "STL%"], ["fc40", "FC/40"], ["ftr", "FTr"],
  ["ft_pct", "FT%", "3P% · 2P% · FT%"], ["twop_pct", "2P%", "3P% · 2P% · FT%"], ["tp_pct", "3P%", "3P% · 2P% · FT%"], ["tpar", "3PAr"],
];
const BOX = [["pts", "PTS"], ["oreb", "OREB"], ["dreb", "DREB"], ["ast", "AST"], ["stl", "STL"], ["blk", "BLK"], ["tov", "TOV"], ["pf", "PF"],
  ["fgm", "FGM"], ["fga", "FGA"], ["tpm", "3PM"], ["tpa", "3PA"], ["ftm", "FTM"], ["fta", "FTA"]];
const VIEWS = ["Advanced", "Per 40", "Per game", "Totals"];

function Scatter({ players }) {
  const pts = players.filter((p) => p.ortg[0] != null && p.min_pct[0] >= 10);
  if (pts.length < 3) return null;
  const W = 560, H = 220, pad = 34;
  const xs = pts.map((p) => p.usg[0]), ys = pts.map((p) => p.ortg[0]);
  const x0 = Math.min(10, ...xs), x1 = Math.max(30, ...xs), y0 = Math.min(80, ...ys), y1 = Math.max(130, ...ys);
  const X = (v) => pad + ((v - x0) / (x1 - x0)) * (W - pad * 2), Y = (v) => H - pad + 8 - ((v - y0) / (y1 - y0)) * (H - pad * 2);
  return (
    <div className="panel" style={{ marginBottom: 14 }}>
      <h3>Usage vs. efficiency <span className="lbl">ORtg against Usg% · dot size = Min%</span></h3>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} style={{ maxWidth: W }}>
        <line x1={pad} x2={W - pad} y1={Y(100)} y2={Y(100)} stroke="var(--bdr)" strokeDasharray="3 3" />
        <line x1={X(20)} x2={X(20)} y1={pad - 10} y2={H - pad + 8} stroke="var(--bdr)" strokeDasharray="3 3" />
        <text x={pad} y={H - 6} fill="var(--t3)" fontSize="9" fontFamily="DM Mono">Usg% {x0.toFixed(0)}–{x1.toFixed(0)} →</text>
        <text x={4} y={pad - 14} fill="var(--t3)" fontSize="9" fontFamily="DM Mono">ORtg ↑</text>
        {pts.map((p) => (
          <g key={p.name}>
            <circle cx={X(p.usg[0])} cy={Y(p.ortg[0])} r={3 + p.min_pct[0] / 12} fill="var(--gold)" fillOpacity=".35" stroke="var(--gold)" />
            <text x={X(p.usg[0]) + 6} y={Y(p.ortg[0]) - 6} fill="var(--t2)" fontSize="9.5" fontFamily="Outfit">{p.name.split(" ").slice(-1)[0]}</text>
          </g>
        ))}
      </svg>
    </div>
  );
}

export default function PlayerTable({ players, team }) {
  const [view, setView] = useState("Advanced");
  const [sort, setSort] = useState(["min_pct", -1]);
  const [showBench, setBench] = useState(false);
  const val = (p, k) => {
    if (view === "Advanced") return Array.isArray(p[k]) ? p[k][0] : p[k];
    const v = p[k];
    if (view === "Totals") return v;
    if (view === "Per game") return p.g ? v / p.g : null;
    return p.min ? (v * 40) / p.min : null;
  };
  const cols = view === "Advanced" ? ADV : BOX;
  const sorted = useMemo(() => {
    const [k, dir] = sort;
    return [...players].sort((a, b) => {
      const va = k === "name" ? a.name : val(a, k), vb = k === "name" ? b.name : val(b, k);
      if (va == null) return 1; if (vb == null) return -1;
      return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * dir;
    });
  }, [players, sort, view]);
  const rotation = sorted.filter((p) => p.min_pct[0] >= 10), bench = sorted.filter((p) => p.min_pct[0] < 10);
  const click = (k) => setSort(([sk, sd]) => (sk === k ? [k, -sd] : [k, -1]));
  const csv = () => sorted.map((p) => [p.name, p.cls, p.pos, p.g, ...cols.map(([k]) => val(p, k))]);
  const digits = view === "Totals" ? 0 : 1;
  const Row = ({ p }) => (
    <tr>
      <td className="name sticky">{p.name}{p.no ? <span className="rk">#{p.no}</span> : null}</td>
      <td className="tl muted">{(p.cls || "").slice(0, 2)}</td>
      <td className="tl muted">{p.pos || ""}</td>
      <td>{p.g}</td>
      {cols.map(([k]) => {
        const v = val(p, k);
        const pc = view === "Advanced" && Array.isArray(p[k]) ? p[k][1] : null;
        return <td key={k} style={{ background: pc != null ? pctColor(pc, 0.22) : undefined }} title={pc != null ? `${Math.round(pc)}th pct among qualified ${team.div.toUpperCase()} players` : undefined}>{v == null ? "—" : Number(v).toFixed(digits)}</td>;
      })}
    </tr>
  );
  return (
    <>
      {view === "Advanced" ? <Scatter players={players} /> : null}
      <div className="tbl-ctrl">
        <div className="fbtns">{VIEWS.map((v) => <button key={v} className={`fb ${view === v ? "on" : ""}`} onClick={() => { setView(v); setSort([v === "Advanced" ? "min_pct" : "pts", -1]); }}>{v}</button>)}</div>
        <span className="lbl">Shading: percentile among {team.div.toUpperCase()} players with ≥40% of minutes</span>
        <span className="spacer" />
        <CsvButton filename={`${team.slug}-players-${view.toLowerCase().replace(/ /g, "")}.csv`} header={["Player", "Cls", "Pos", "G", ...cols.map(([, l]) => l)]} rows={csv} />
      </div>
      <div className="tw"><table>
        <thead><tr>
          <Th k="Player" left onClick={() => click("name")} sorted={sort[0] === "name"} dir={sort[1]} />
          <Th k="Cls" left /><Th k="Pos" left /><Th k="G" />
          {cols.map(([k, l, gk]) => <Th key={k} k={gk || l} label={l} onClick={() => click(k)} sorted={sort[0] === k} dir={sort[1]} />)}
        </tr></thead>
        <tbody>
          {rotation.map((p) => <Row key={p.name} p={p} />)}
          {bench.length ? (
            <tr onClick={() => setBench(!showBench)} style={{ cursor: "pointer" }}>
              <td className="tl muted" colSpan={4 + cols.length}>{showBench ? "▼" : "▶"} Bench: {bench.length} players under 10% of minutes</td>
            </tr>
          ) : null}
          {showBench && bench.map((p) => <Row key={p.name} p={p} />)}
        </tbody>
      </table></div>
    </>
  );
}
