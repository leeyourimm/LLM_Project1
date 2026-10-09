import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { VerifyEmail } from "./VerifyEmail";

export const metadata = { title: "이메일 인증" };

export default function VerifyEmailPage() {
  return (
    <Suspense fallback={<Loading />}>
      <VerifyEmail />
    </Suspense>
  );
}
