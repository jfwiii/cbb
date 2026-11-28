import FactorsView from "@/components/FactorsView";

export function generateStaticParams() {
  return [{ div: "d1" }, { div: "d2" }, { div: "d3" }];
}

export default function Page({ params }) {
  return <FactorsView div={params.div} />;
}
