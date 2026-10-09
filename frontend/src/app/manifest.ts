import type { MetadataRoute } from "next";

// 웹 앱 매니페스트 (/manifest.webmanifest). 아이폰·아이패드는 홈 화면에 추가해 독립 화면으로 연 웹 앱에서만
// 웹 푸시를 받을 수 있어서 display: standalone 이 필요하다.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "DART 공시 분석",
    short_name: "DART 공시",
    description: "DART 공시를 근거로 답하는 기업 분석 서비스",
    lang: "ko",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#ffffff",
  };
}
