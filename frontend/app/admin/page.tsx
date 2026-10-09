"use client";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, ArticlePage, date } from "../../lib/api";

export default function Admin() {
  const router = useRouter(); const [data, setData] = useState<ArticlePage>({ items: [], total: 0, limit: 10, offset: 0 });
  const [page, setPage] = useState(0); const [status, setStatus] = useState(""); const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    const params = new URLSearchParams({ limit: "10", offset: String(page * 10) });
    if (status) params.set("status", status);
    const result = await api<ArticlePage>(`articles?${params}`);
    setData(result); if (page > Math.max(0, Math.ceil(result.total / 10) - 1)) setPage(Math.max(0, Math.ceil(result.total / 10) - 1));
  }, [page, status]);
  useEffect(() => { load().catch(e => setError(e.message)); }, [load]);
  async function remove(id: string, title: string) {
    if (!window.confirm(`“${title}” 글을 삭제할까요?`)) return;
    setBusy(true); setError("");
    try { await api(`articles/${id}`, { method: "DELETE" }); await load(); }
    catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  async function logout() {
    try { await api("auth/logout", { method: "POST", body: "{}" }); router.replace("/login"); router.refresh(); }
    catch (e) { setError((e as Error).message); }
  }
  return <main className="admin"><header className="blog-header"><div><Link href="/">← 공개 블로그</Link><h1>글 관리</h1></div>
    <div className="actions"><Link className="button" href="/admin/articles/new">글 작성</Link><button className="secondary" onClick={logout}>로그아웃</button></div></header>
    <label>글 상태<select aria-label="관리자 상태 필터" value={status} onChange={e => { setStatus(e.target.value); setPage(0); }}><option value="">전체</option><option value="draft">임시저장</option><option value="published">발행됨</option></select></label>
    {error && <p className="notice" role="alert">{error}</p>}
    <h2>총 {data.total}개</h2>
    {data.items.map(article => <section key={article.id} className="admin-entry"><div className="meta"><span>{article.category}</span><span className={`badge ${article.status}`}>{article.status === "draft" ? "임시저장" : "발행됨"}</span></div>
      <h2>{article.title}</h2><p className="article-preview">{article.summary}</p><p className="dates">수정 {date(article.updated_at)}</p>
      <div className="actions"><Link href={`/admin/articles/${article.id}/edit`}>본문·상태 수정</Link>{article.status === "published" && <Link href={`/articles/${article.id}`}>공개 글 보기</Link>}<button className="danger" disabled={busy} onClick={() => remove(article.id, article.title)}>삭제</button></div>
    </section>)}
    {data.total === 0 && <p>조건에 맞는 글이 없습니다.</p>}
    <nav className="blog-pagination" aria-label="관리자 페이지"><button className="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>이전</button><span>{page + 1} / {Math.max(1, Math.ceil(data.total / 10))}</span><button className="secondary" disabled={(page + 1) * 10 >= data.total} onClick={() => setPage(page + 1)}>다음</button></nav>
  </main>;
}
