import { Suspense } from "react";
import { ChatPage } from "@/components/chat/ChatPage";
import { Loading } from "@/components/ui";

export default function Home() {
  return (
    <Suspense fallback={<Loading />}>
      <ChatPage />
    </Suspense>
  );
}
