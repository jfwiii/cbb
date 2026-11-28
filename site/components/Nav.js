"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";

const TABS = [
  ["/", "Tonight"],
  ["/d1/ratings/", "D1 Ratings"],
  ["/d2/ratings/", "D2 Ratings"],
  ["/d3/ratings/", "D3 Ratings"],
  ["/d1/factors/", "Four Factors"],
  ["/status/", "Status"],
];

export default function Nav() {
  const path = usePathname() || "/";
  const active = (href) =>
    href === "/" ? path === "/" : href.includes("factors") ? path.includes("/factors") : path.startsWith(href);
  return (
    <nav className="site">
      {TABS.map(([href, label]) => (
        <Link key={href} href={href} className={`tab ${active(href) ? "active" : ""}`}>{label}</Link>
      ))}
    </nav>
  );
}
