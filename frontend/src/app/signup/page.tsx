import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { AuthForm } from "../login/AuthForm";

export const metadata = { title: "가입하기" };

export default function SignupPage() {
  return (
    <Suspense fallback={<Loading />}>
      <AuthForm mode="signup" />
    </Suspense>
  );
}
