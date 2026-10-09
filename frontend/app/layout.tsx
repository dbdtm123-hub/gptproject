import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "AutoLog 기술 블로그", description: "ChatGPT 대화에서 만든 개인 기술 블로그와 지식 베이스" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="ko"><body>{children}</body></html>;
}
