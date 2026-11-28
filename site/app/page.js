import { Suspense } from "react";
import Tonight from "@/components/Tonight";

export default function Page() {
  return (
    <Suspense fallback={null}>
      <Tonight />
    </Suspense>
  );
}
