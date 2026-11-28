"use client";
import { useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { addDays, fmtDate, pct0, tipET, todayET, useData } from "@/lib/data";
import { BetaBadge, DivPill, Loading, TeamLink } from "@/components/ui";

function Bar({ label, v }) {
  return (
    <>
      <span>{label}</span>
      <span className="bt"><span className="bf" style={{ width: `${Math.round(100 * (v || 0))}%`, display: "block" }} /></span>
    </>
  );
}

function Side({ s, other, final }) {
  const won = final && s.pts != null && other.pts != null && s.pts > other.pts;
  return (
    <div className="row">
      <DivPill div={s.div} />
      <span className={`tm ${won ? "win" : ""}`}><TeamLink slug={s.slug} name={s.name} /></span>
      <span className="meta">{s.rk ? `#${s.rk} · ` : ""}{s.w}-{s.l}{s.conf ? ` · ${s.conf.toUpperCase()}` : ""}</span>
      <span className={`pts ${won ? "win" : ""}`}>{final ? s.pts : s.pred != null ? <span className="muted">{Math.round(s.pred)}</span> : ""}</span>
    </div>
  );
}

function WatchCard({ g }) {
  const final = g.status === "final";
  const c = g.components;
  const fav = g.home_win_prob == null ? null : g.home_win_prob >= 0.5 ? ["home", g.home_win_prob] : ["away", 1 - g.home_win_prob];
  return (
    <div className="wcard">
      <div className={`wscore ${g.watch == null ? "na" : ""}`} title="Watch Score">{g.watch == null ? "—" : Math.round(g.watch)}</div>
      <div className="wteams">
        <Side s={g.away} other={g.home} final={final} />
        <Side s={g.home} other={g.away} final={final} />
        {g.tags?.length ? (
          <div className="tags">{g.tags.map((t, i) => <span key={i} className={`tag ${t.kind}`} title={t.teams ? t.teams.join(" vs ") : ""}>{t.name}</span>)}</div>
        ) : null}
      </div>
      <div className="wmeta">
        <span>{final ? "FINAL" : g.status === "live" ? "LIVE" : tipET(g.tip)}{g.tv ? ` · ${g.tv}` : ""}{g.neutral ? " · neutral" : ""}</span>
        {fav ? <span className="muted">{(fav[0] === "home" ? g.home : g.away).name} {pct0(fav[1])}{g.poss ? ` · ${Math.round(g.poss)} poss` : ""}</span> : g.crossover ? <span className="muted">Crossover · records only</span> : null}
        {c ? (
          <div className="bars">
            <Bar label="Close+good" v={c.close_good} />
            <Bar label="Style" v={c.style} />
            <Bar label="Stakes" v={c.stakes} />
          </div>
        ) : null}
      </div>
    </div>
  );
}

export default function Tonight() {
  const params = useSearchParams();
  const router = useRouter();
  const date = params.get("date") || todayET();
  const [div, setDiv] = useState("all");
  const [minScore, setMin] = useState(0);
  const state = useData(`tonight/${date}.json`);
  const go = (d) => router.push(d === todayET() ? "/" : `/?date=${d}`);

  return (
    <>
      <div className="sh">
        <div className="st">Tonight<BetaBadge /></div>
        <div className="sd">Every D1, D2 and D3 game ranked by Watch Score: a close game between good teams (35%), style extremes (35%) and stakes (30%). No team is boosted.</div>
      </div>
      <div className="datenav">
        <button className="fb" onClick={() => go(addDays(date, -1))}>◀ Prev</button>
        <span className="day">{fmtDate(date, { weekday: "long", month: "short", day: "numeric" })}</span>
        <button className="fb" onClick={() => go(addDays(date, 1))}>Next ▶</button>
        {date !== todayET() ? <button className="fb" onClick={() => go(todayET())}>Today</button> : null}
        <span className="spacer" />
        <div className="fbtns">
          {["all", "d1", "d2", "d3"].map((d) => (
            <button key={d} className={`fb ${div === d ? "on" : ""}`} onClick={() => setDiv(d)}>{d === "all" ? "ALL" : d.toUpperCase()}</button>
          ))}
        </div>
        <label className="lbl" style={{ display: "flex", alignItems: "center", gap: 8 }}>
          Min {minScore}
          <input type="range" min="0" max="80" step="5" value={minScore} onChange={(e) => setMin(+e.target.value)} />
        </label>
      </div>
      <Loading state={state.error ? { ...state, error: { message: "No slate published for this date yet (the slate covers 3 days back to 7 days ahead)" } } : state}>
        {(data) => <Slate data={data} div={div} minScore={minScore} />}
      </Loading>
    </>
  );
}

function Slate({ data, div, minScore }) {
  const games = useMemo(() => data.games.filter((g) =>
    (div === "all" || g.home.div === div || g.away.div === div) &&
    (minScore === 0 || (g.watch ?? -1) >= minScore)), [data, div, minScore]);
  if (!games.length) return <div className="disc">No games match.</div>;
  return <div className="cards">{games.map((g) => <WatchCard key={g.id} g={g} />)}</div>;
}
