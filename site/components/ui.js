"use client";
import Link from "next/link";
import { f1, pctColor, useGlossary, downloadCsv } from "@/lib/data";

export function DivPill({ div }) {
  if (!div) return null;
  return <span className={`pill pill-${div}`}>{div.toUpperCase()}</span>;
}

export function ConfLink({ conf, div }) {
  if (!conf) return <span className="muted">—</span>;
  return <span className="conf">{conf.toUpperCase()}</span>;
}

export function TeamLink({ slug, name }) {
  if (!slug) return <span>{name}</span>;
  return <Link href={`/team/${slug}/`} className="tlink">{name}</Link>;
}

/** Value with small grey rank after it, KenPom-style. */
export function ValRank({ v, rk, digits = 1, small }) {
  return (
    <span className={small ? "smallsample" : ""}>
      {v == null ? "—" : Number(v).toFixed(digits)}
      {rk ? <span className="rk">{rk}</span> : null}
    </span>
  );
}

/** CTG-style percentile chip: fills to the percentile on the blue-grey-orange scale. */
export function PercentileChip({ p }) {
  if (p == null) return <span className="pchip empty" />;
  return (
    <span className="pchip" title={`${Math.round(p)}th percentile`}>
      <span className="pfill" style={{ width: `${Math.max(4, p)}%`, background: pctColor(p) }} />
      <span className="ptxt">{Math.round(p)}</span>
    </span>
  );
}

/** Column header with glossary hover text. */
export function Th({ k, label, onClick, sorted, dir, left, title }) {
  const g = useGlossary();
  const def = title || (g[k] ? `${g[k].name}: ${g[k].def}` : undefined);
  return (
    <th className={`${left ? "tl" : ""} ${sorted ? "sorted" : ""}`} onClick={onClick} title={def}>
      {label ?? k}{sorted ? <span className="arr">{dir > 0 ? "▲" : "▼"}</span> : null}
    </th>
  );
}

export function Sparkline({ values, w = 80, h = 20 }) {
  const v = (values || []).filter((x) => x != null);
  if (v.length < 2) return <span className="muted">—</span>;
  const lo = Math.min(...v), hi = Math.max(...v), span = hi - lo || 1;
  const pts = v.map((x, i) => `${(i / (v.length - 1)) * w},${h - 2 - ((x - lo) / span) * (h - 4)}`).join(" ");
  const up = v[v.length - 1] >= v[0];
  return (
    <svg width={w} height={h} className="spark" aria-label={`trend ${f1(v[0])} to ${f1(v[v.length - 1])}`}>
      <polyline points={pts} fill="none" stroke={up ? "var(--orange)" : "var(--blue)"} strokeWidth="1.5" />
    </svg>
  );
}

export function CsvButton({ filename, header, rows }) {
  return (
    <button className="fb" onClick={() => downloadCsv(filename, header, rows())} title="Download this table as CSV">
      ↓ CSV
    </button>
  );
}

export function Loading({ state, children }) {
  if (state.error) return <div className="disc">Couldn't load data ({String(state.error.message || state.error)}).</div>;
  if (state.loading || !state.data) return <div className="skeleton"><div /><div /><div /><div /></div>;
  return children(state.data);
}

export function BetaBadge() {
  return <span className="beta" title="Ratings use default settings; tuning comes in Phase 3">BETA</span>;
}

export function DivTabs({ div, base }) {
  return (
    <div className="fbtns">
      {["d1", "d2", "d3"].map((d) => (
        <Link key={d} href={`/${d}/${base}/`} className={`fb ${d === div ? "on" : ""}`}>{d.toUpperCase()}</Link>
      ))}
    </div>
  );
}
