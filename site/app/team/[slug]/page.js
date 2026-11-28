import TeamView from "@/components/TeamView";

const DATA_BASE = (process.env.NEXT_PUBLIC_DATA_BASE || "https://jfwiii.github.io/cbb").replace(/\/$/, "");

/** Team routes come from the published team list at deploy time. A team new to a division
 *  falls back to /team/?slug=... until the next deploy. */
export async function generateStaticParams() {
  try {
    const r = await fetch(`${DATA_BASE}/teams/index.json`);
    const teams = await r.json();
    const slugs = teams.map((t) => ({ slug: t.slug }));
    if (slugs.length) return slugs;
  } catch (e) {
    console.warn("teams/index.json unavailable at build; team pages use /team/?slug=", e.message);
  }
  return [{ slug: "_" }];
}
export const dynamicParams = false;

export default function Page({ params }) {
  return <TeamView slug={params.slug} />;
}
