import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { DiffView } from "./DiffView";

export const metadata = { title: "변경점" };

export default function DiffPage() {
  return (
    <Suspense fallback={<Loading />}>
      <DiffView />
    </Suspense>
  );
}
