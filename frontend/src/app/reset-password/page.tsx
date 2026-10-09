import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { ResetForm } from "./ResetForm";

export const metadata = { title: "새 비밀번호" };

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={<Loading />}>
      <ResetForm />
    </Suspense>
  );
}
