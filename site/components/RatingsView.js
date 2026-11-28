"use client";
import { useMemo, useState } from "react";
import { f1, f3, fmtDate, pctColor, signed, useData } from "@/lib/data";
import { BetaBadge, CsvButton, DivTabs, Loading, Sparkline, TeamLink, Th, ValRank } from "@/components/ui";

const COLS = [
  // key, label, getter, digits, higherBetter (null = no shading)
  ["em", "AdjEM", (r) => r.em, 1, true],
  ["o", "AdjO", (r) => r.o, 1, true],
  ["d", "AdjD", (r) => r.d, 1, false],
  ["t", "AdjT", (r) => r.t, 1, null],
  ["luck", "Luck", (r) => r.luck, 3, null],
  ["sos", "SOS", (r) => r.sos, 1, null],
  ["ncsos", "NCSOS", (r) => r.ncsos, 1, null],
];

function rankBy(rows, key, higher) {
  const s = rows.filter((r) => r[key] != null).sort((a, b) => (higher ? b[key] - a[key] : a[key] - b[key]));
  const out = {};
  s.forEach((r, i) => { out[r.id] = i + 1; });
  return out;
}

export default function RatingsView({ div }) {
  const cur = useData(`ratings/${div}.json`);
  const [asof, setAsof] = useState(null);
  const hist = useData(asof ? `ratings/${div}-history.json` : null);
  return (
    <>
      <div className="sh">
        <div className="st">{div.toUpperCase()} Ratings<BetaBadge /></div>
        <div className="sd">
          Opponent- and venue-adjusted efficiency, fitted separately for each division. AdjEM is points per 100
          possessions better than an average {div.toUpperCase()} team on a neutral court. Games against other
          divisions count toward records only. Faded rows have fewer than 5 in-division games; hover a rank for its 80% range.
        </div>
      </div>
      <Loading state={cur}>
        {(data) => <Table div={div} data={data} asof={asof} setAsof={setAsof} hist={hist} />}
      </Loading>
    </>
  );
}

function Table({ div, data, asof, setAsof, hist }) {
  const [sort, setSort] = useState(["rk", 1]);
  const [confs, setConfs] = useState([]);
  const [q, setQ] = useState("");
  const allConfs = useMemo(() => [...new Set(data.teams.map((t) => t.conf).filter(Boolean))].sort(), [data]);

  // Rows for the chosen as-of date (history file) or current.
  const rows = useMemo(() => {
    if (!asof || asof === data.asof || !hist.data) return data.teams;
    const i = hist.data.dates.indexOf(asof);
    const base = data.teams.map((t) => {
      const h = hist.data.teams[String(t.id)];
      if (!h || h.adj_em[i] == null) return null;
      return { ...t, em: h.adj_em[i], o: h.adj_o[i], d: h.adj_d[i], t: h.adj_t[i],
               luck: null, sos: null, ncsos: null, trend: h.adj_em.slice(Math.max(0, i - 20), i + 1) };
    }).filter(Boolean);
    const rk = rankBy(base, "em", true), ork = rankBy(base, "o", true), drk = rankBy(base, "d", false), trk = rankBy(base, "t", true);
    return base.map((r) => ({ ...r, rk: rk[r.id], o_rk: ork[r.id], d_rk: drk[r.id], t_rk: trk[r.id], rk_lo: null, rk_hi: null }));
  }, [asof, hist.data, data]);

  const n = rows.length;
  const shade = (rank, hb) => (hb == null || !rank ? undefined : pctColor(100 * (1 - (rank - 1) / Math.max(n - 1, 1)), 0.22));

  const shown = useMemo(() => {
    let r = rows.filter((t) => (!confs.length || confs.includes(t.conf)) &&
      (!q || t.name.toLowerCase().includes(q.toLowerCase())));
    const [k, dir] = sort;
    const get = k === "rk" ? (x) => x.rk : k === "name" ? (x) => x.name : k === "wl" ? (x) => x.w - x.l : (x) => x[k];
    r = [...r].sort((a, b) => {
      const va = get(a), vb = get(b);
      if (va == null) return 1; if (vb == null) return -1;
      return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * dir;
    });
    return r;
  }, [rows, confs, q, sort]);

  const click = (k, defDir = -1) => setSort(([sk, sd]) => (sk === k ? [k, -sd] : [k, defDir]));
  const toggleConf = (c) => setConfs((cs) => (cs.includes(c) ? cs.filter((x) => x !== c) : [...cs, c]));

  const csv = () => shown.map((t) => [t.rk, t.name, t.conf, `${t.w}-${t.l}`, t.em, t.o, t.o_rk, t.d, t.d_rk, t.t, t.t_rk, t.luck, t.sos, t.ncsos]);

  return (
    <>
      <div className="tbl-ctrl">
        <DivTabs div={div} base="ratings" />
        <input className="tsearch" placeholder="Search teams" value={q} onChange={(e) => setQ(e.target.value)} />
        <select className="tsel" value={asof || ""} onChange={(e) => setAsof(e.target.value || null)} title="Ratings as of a past date">
          <option value="">Current ({fmtDate(data.asof)})</option>
          {(hist.data?.dates || []).slice().reverse().slice(1).map((d) => <option key={d} value={d}>As of {fmtDate(d)}</option>)}
          {!hist.data ? <option value="__load" disabled>Pick to load history…</option> : null}
        </select>
        {!hist.data && !asof ? <button className="fb" onClick={() => setAsof(data.asof)}>History</button> : null}
        <span className="spacer" />
        <span className="lbl">Home court {signed(data.home)} · σ {f1(data.sigma)} · {data.n_games} games</span>
        <CsvButton filename={`${div}-ratings.csv`} header={["Rk", "Team", "Conf", "W-L", "AdjEM", "AdjO", "AdjO Rk", "AdjD", "AdjD Rk", "AdjT", "AdjT Rk", "Luck", "SOS", "NCSOS"]} rows={csv} />
      </div>
      <div className="fbtns" style={{ marginBottom: 12 }}>
        {allConfs.map((c) => <button key={c} className={`fb ${confs.includes(c) ? "on" : ""}`} onClick={() => toggleConf(c)}>{c.toUpperCase()}</button>)}
        {confs.length ? <button className="fb" onClick={() => setConfs([])}>Clear</button> : null}
      </div>
      <div className="tw">
        <table>
          <thead>
            <tr>
              <Th k="Rk" onClick={() => click("rk", 1)} sorted={sort[0] === "rk"} dir={sort[1]} />
              <Th k="Team" left onClick={() => click("name", 1)} sorted={sort[0] === "name"} dir={sort[1]} />
              <Th k="Conf" left />
              <Th k="W · L" label="W-L" onClick={() => click("wl")} sorted={sort[0] === "wl"} dir={sort[1]} />
              {COLS.map(([k, label, , , hb]) => (
                <Th key={k} k={label} onClick={() => click(k, hb === false ? 1 : -1)} sorted={sort[0] === k} dir={sort[1]} />
              ))}
              <Th k="Trend" title="AdjEM over the last 30 days" />
            </tr>
          </thead>
          <tbody>
            {shown.map((t) => (
              <tr key={t.id} className={t.small ? "smallsample" : ""}>
                <td className="rkcell" title={t.rk_lo ? `80% range: ${t.rk_lo}–${t.rk_hi}` : undefined}>{t.rk}</td>
                <td className="name sticky"><TeamLink slug={t.slug} name={t.name} /></td>
                <td className="tl conf">{t.conf ? t.conf.toUpperCase() : "—"}</td>
                <td>{t.w}-{t.l}</td>
                <td style={{ background: shade(t.rk, true) }}>{signed(t.em)}</td>
                <td style={{ background: shade(t.o_rk, true) }}><ValRank v={t.o} rk={t.o_rk} /></td>
                <td style={{ background: shade(t.d_rk, false) }}><ValRank v={t.d} rk={t.d_rk} /></td>
                <td><ValRank v={t.t} rk={t.t_rk} /></td>
                <td>{t.luck == null ? "—" : <ValRank v={t.luck} rk={t.luck_rk} digits={3} />}</td>
                <td>{t.sos == null ? "—" : <ValRank v={t.sos} rk={t.sos_rk} />}</td>
                <td>{t.ncsos == null ? "—" : <ValRank v={t.ncsos} rk={t.ncsos_rk} />}</td>
                <td><Sparkline values={t.trend} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
