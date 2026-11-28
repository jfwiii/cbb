"use client";
import { Suspense } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import TeamView from "@/components/TeamView";

/** Fallback for teams added after the last deploy: /team/?slug=x, or /team/x/ rewritten here
 *  by Netlify when no prebuilt page exists. */
function Inner() {
  const q = useSearchParams().get("slug");
  const path = usePathname() || "";
  const m = path.match(/^\/team\/([^/]+)\/?$/);
  const slug = q || (m ? m[1] : null);
  if (!slug) return <div className="disc">No team given.</div>;
  return <TeamView slug={slug} />;
}

export default function Page() {
  return <Suspense fallback={null}><Inner /></Suspense>;
}
