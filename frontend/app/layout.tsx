import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = { title: "Autolog", description: "AI 일상 기록" };
export default function RootLayout({ children }: { children: React.ReactNode }) {
  return <html lang="ko"><body>{children}</body></html>;
}
