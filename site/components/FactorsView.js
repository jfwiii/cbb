"use client";
import { useMemo, useState } from "react";
import { f1, useData } from "@/lib/data";
import { BetaBadge, CsvButton, DivTabs, Loading, PercentileChip, TeamLink, Th } from "@/components/ui";

export const FACTORS = [["efg", "eFG%"], ["tov", "TOV%"], ["orb", "ORB%"], ["ftr", "FTr"]];
const SPLITS = [["season", "Season"], ["last10", "Last 10"], ["conference", "Conference"]];

export default function FactorsView({ div }) {
  const state = useData(`factors/${div}.json`);
  return (
    <>
      <div className="sh">
        <div className="st">{div.toUpperCase()} Four Factors<BetaBadge /></div>
        <div className="sd">
          Shooting, turnovers, offensive rebounding and free throws, on offense and (as opponent values) on defense.
          Chips show the percentile within the division, colored by what's good (a low TOV% shades orange).
          Adjusted values remove opponent and venue; splits use in-division games.
        </div>
      </div>
      <Loading state={state}>{(data) => <Table div={div} data={data} />}</Loading>
    </>
  );
}

function Table({ div, data }) {
  const [split, setSplit] = useState("season");
  const [adj, setAdj] = useState(true);
  const [sort, setSort] = useState(["off.eff", -1]);
  const [q, setQ] = useState("");
  const useAdj = adj && split === "season";
  const cellOf = (t, side, f) => {
    if (useAdj) return t.adjusted[side][f] || [null, null, null];
    return t[split][side][f] || [null, null, null];
  };
  const rows = useMemo(() => {
    const [k, dir] = sort;
    let r = data.teams.filter((t) => t[split].g > 0 && (!q || t.name.toLowerCase().includes(q.toLowerCase())));
    const get = (t) => {
      if (k === "name") return t.name;
      const [side, f] = k.split(".");
      return cellOf(t, side, f)[2]; // sort by percentile (good-direction)
    };
    return [...r].sort((a, b) => {
      const va = get(a), vb = get(b);
      if (va == null) return 1; if (vb == null) return -1;
      return (typeof va === "string" ? va.localeCompare(vb) : va - vb) * dir;
    });
  }, [data, split, useAdj, sort, q]);
  const click = (k) => setSort(([sk, sd]) => (sk === k ? [k, -sd] : [k, k === "name" ? 1 : -1]));
  const cols = [["eff", "Eff"], ...FACTORS];
  const csv = () => rows.map((t) => [t.name, t.conf, t[split].g,
    ...["off", "def"].flatMap((s) => cols.map(([f]) => cellOf(t, s, f)[0]))]);
  const head = (side) => cols.map(([f, label]) => (
    <Th key={side + f} k={f === "eff" ? (side === "off" ? "AdjO" : "AdjD") : label}
        label={f === "eff" ? (useAdj ? (side === "off" ? "AdjO" : "AdjD") : (side === "off" ? "ORtg" : "DRtg")) : label}
        onClick={() => click(`${side}.${f}`)} sorted={sort[0] === `${side}.${f}`} dir={sort[1]} />
  ));
  return (
    <>
      <div className="tbl-ctrl">
        <DivTabs div={div} base="factors" />
        <div className="fbtns">
          {SPLITS.map(([k, l]) => <button key={k} className={`fb ${split === k ? "on" : ""}`} onClick={() => setSplit(k)}>{l}</button>)}
        </div>
        <div className="fbtns">
          <button className={`fb ${useAdj ? "on" : ""}`} disabled={split !== "season"} onClick={() => setAdj(true)} title={split !== "season" ? "Adjusted values are season-only for now" : ""}>Adjusted</button>
          <button className={`fb ${!useAdj ? "on" : ""}`} onClick={() => setAdj(false)}>Raw</button>
        </div>
        <input className="tsearch" placeholder="Search teams" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="spacer" />
        <CsvButton filename={`${div}-four-factors-${split}${useAdj ? "-adj" : ""}.csv`}
          header={["Team", "Conf", "G", ...["Off", "Def"].flatMap((s) => cols.map(([, l]) => `${s} ${l}`))]} rows={csv} />
      </div>
      <div className="tw">
        <table>
          <thead>
            <tr>
              <th className="tl" colSpan={3}></th>
              <th colSpan={5} style={{ textAlign: "center" }}>Offense</th>
              <th colSpan={5} style={{ textAlign: "center" }}>Defense (opponents)</th>
            </tr>
            <tr>
              <Th k="Team" left onClick={() => click("name")} sorted={sort[0] === "name"} dir={sort[1]} />
              <Th k="Conf" left />
              <Th k="G" />
              {head("off")}
              {head("def")}
            </tr>
          </thead>
          <tbody>
            {rows.map((t) => (
              <tr key={t.id} className={t[split].g < 5 ? "smallsample" : ""}>
                <td className="name sticky"><TeamLink slug={t.slug} name={t.name} /></td>
                <td className="tl conf">{t.conf ? t.conf.toUpperCase() : "—"}</td>
                <td>{t[split].g}</td>
                {["off", "def"].flatMap((side) => cols.map(([f]) => {
                  const [v, rk, p] = cellOf(t, side, f);
                  return (
                    <td key={side + f} title={rk ? `Rank ${rk}` : undefined}>
                      {f1(v)} <PercentileChip p={p} />
                    </td>
                  );
                }))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
