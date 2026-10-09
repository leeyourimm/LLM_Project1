import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { CompareView } from "./CompareView";

export const metadata = { title: "기업 비교" };

export default function ComparePage() {
  return (
    <Suspense fallback={<Loading />}>
      <CompareView />
    </Suspense>
  );
}
