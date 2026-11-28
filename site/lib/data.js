"use client";
import { useEffect, useState } from "react";

/** Nightly data lives on GitHub Pages; configurable for local testing. */
export const DATA_BASE = (process.env.NEXT_PUBLIC_DATA_BASE || "https://jfwiii.github.io/cbb").replace(/\/$/, "");
export const DIVS = ["d1", "d2", "d3"];

const cache = new Map();
export function fetchJson(path) {
  if (!cache.has(path)) {
    cache.set(path, fetch(`${DATA_BASE}/${path}`).then((r) => {
      if (!r.ok) throw new Error(`${r.status} ${path}`);
      return r.json();
    }).catch((e) => { cache.delete(path); throw e; }));
  }
  return cache.get(path);
}

/** { data, error, loading } for a JSON path (null path = skip). */
export function useData(path) {
  const [state, set] = useState({ data: null, error: null, loading: !!path });
  useEffect(() => {
    if (!path) return;
    let live = true;
    set((s) => ({ ...s, loading: true, error: null }));
    fetchJson(path).then(
      (data) => live && set({ data, error: null, loading: false }),
      (error) => live && set({ data: null, error, loading: false }));
    return () => { live = false; };
  }, [path]);
  return state;
}

export const f1 = (x) => (x == null ? "—" : Number(x).toFixed(1));
export const f3 = (x) => (x == null ? "—" : Number(x).toFixed(3));
export const signed = (x, d = 1) => (x == null ? "—" : (x > 0 ? "+" : "") + Number(x).toFixed(d));
export const pct0 = (x) => (x == null ? "—" : `${Math.round(100 * x)}%`);

export function todayET() {
  const s = new Date().toLocaleDateString("en-CA", { timeZone: "America/New_York" });
  return s; // YYYY-MM-DD
}
export function addDays(iso, n) {
  const d = new Date(iso + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
export function fmtDate(iso, opts = { weekday: "short", month: "short", day: "numeric" }) {
  return new Date(iso + "T12:00:00Z").toLocaleDateString("en-US", { timeZone: "UTC", ...opts });
}
export function tipET(utc) {
  if (!utc) return "TBA";
  return new Date(utc).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour: "numeric", minute: "2-digit" }) + " ET";
}

/** Percentile (0-100, 100 = good) -> colorblind-safe diverging color: blue -> grey -> orange. */
export function pctColor(p, alpha = 1) {
  if (p == null) return "transparent";
  const lo = [59, 130, 246], mid = [100, 116, 139], hi = [245, 158, 11];
  const t = Math.max(0, Math.min(1, p / 100));
  const [a, b, u] = t < 0.5 ? [lo, mid, t / 0.5] : [mid, hi, (t - 0.5) / 0.5];
  const c = a.map((v, i) => Math.round(v + (b[i] - v) * u));
  return `rgba(${c[0]},${c[1]},${c[2]},${alpha})`;
}

/** Download rows (array of arrays) as CSV. */
export function downloadCsv(filename, header, rows) {
  const esc = (v) => {
    if (v == null) return "";
    const s = String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const body = [header, ...rows].map((r) => r.map(esc).join(",")).join("\n");
  const url = URL.createObjectURL(new Blob([body], { type: "text/csv" }));
  const a = document.createElement("a");
  a.href = url; a.download = filename; a.click();
  URL.revokeObjectURL(url);
}

export function useGlossary() {
  return useData("glossary.json").data || {};
}
