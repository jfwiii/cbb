import RatingsView from "@/components/RatingsView";

export function generateStaticParams() {
  return [{ div: "d1" }, { div: "d2" }, { div: "d3" }];
}

export default function Page({ params }) {
  return <RatingsView div={params.div} />;
}
