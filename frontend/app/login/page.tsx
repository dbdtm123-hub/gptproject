"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { api } from "../../lib/api";

export default function Login() {
  const router = useRouter();
  const [username, setUsername] = useState(""); const [password, setPassword] = useState("");
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  async function login(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    try { await api("auth/login", { method: "POST", body: JSON.stringify({ username, password }) }); setPassword(""); router.replace("/admin"); router.refresh(); }
    catch (e) { setError((e as Error).message); setBusy(false); }
  }
  return <main className="login"><Link href="/">← 공개 블로그</Link><h1>관리자 로그인</h1>
    <section><form onSubmit={login}>
      <label>사용자 이름<input autoComplete="username" required maxLength={200} value={username} onChange={e => setUsername(e.target.value)} /></label>
      <label>비밀번호<input type="password" autoComplete="current-password" required maxLength={1024} value={password} onChange={e => setPassword(e.target.value)} /></label>
      {error && <p className="notice" role="alert">{error}</p>}
      <button type="submit" disabled={busy}>{busy ? "로그인 중…" : "로그인"}</button>
    </form></section>
  </main>;
}
