import { Suspense } from "react";
import { Loading } from "@/components/ui";
import { AuthForm } from "./AuthForm";

export const metadata = { title: "로그인" };

export default function LoginPage() {
  return (
    <Suspense fallback={<Loading />}>
      <AuthForm mode="login" />
    </Suspense>
  );
}
