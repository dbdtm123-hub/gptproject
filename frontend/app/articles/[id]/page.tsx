"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import Markdown from "../../../components/Markdown";
import { api, Article, date } from "../../../lib/api";

export default function ArticleDetail() {
  const { id } = useParams<{ id: string }>(); const router = useRouter();
  const [article, setArticle] = useState<Article | null>(null);
  const [related, setRelated] = useState<Article[]>([]);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => {
    const controller = new AbortController(); let pending = false;
    const refresh = async () => {
      if (pending || document.visibilityState === "hidden") return;
      pending = true;
      try {
        const value = await api<Article>(`articles/${id}`, { signal: controller.signal });
        const links = await Promise.allSettled(value.related_articles.map(relatedId => api<Article>(`articles/${relatedId}`, { signal: controller.signal })));
        if (!controller.signal.aborted) {
          setArticle(value); setRelated(links.flatMap(result => result.status === "fulfilled" ? [result.value] : [])); setError("");
        }
      } catch (e) { if (!controller.signal.aborted) setError((e as Error).message); }
      finally { pending = false; }
    };
    void refresh(); const interval = window.setInterval(refresh, 3000);
    window.addEventListener("focus", refresh);
    return () => { controller.abort(); window.clearInterval(interval); window.removeEventListener("focus", refresh); };
  }, [id]);
  async function remove() {
    if (!article || !window.confirm(`“${article.title}” 글을 삭제할까요?`)) return;
    setBusy(true);
    try { await api(`articles/${id}`, { method: "DELETE" }); router.push("/"); }
    catch (e) { setError((e as Error).message); setBusy(false); }
  }
  return <main className="reading"><nav className="actions"><Link href="/">← 글 목록</Link>{article && <Link href={`/articles/${id}/edit`}>수정</Link>}</nav>
    {error && <p role="alert" className="notice">{error}</p>}
    {!article && !error && <p>글을 불러오는 중…</p>}
    {article && <>
      <article className="article-body"><header>
        <div className="meta"><span>{article.category}{article.subcategory ? ` / ${article.subcategory}` : ""}</span><span className={`badge ${article.status}`}>{article.status === "published" ? "발행됨" : "초안"}</span></div>
        <h1>{article.title}</h1><p className="lead">{article.summary}</p>
        <div className="dates">작성 <time dateTime={article.created_at}>{date(article.created_at)}</time> · 수정 <time dateTime={article.updated_at}>{date(article.updated_at)}</time></div>
        <div className="tags">{article.tags.map(t => <span key={t}>#{t}</span>)}</div>
      </header><Markdown content={article.content_markdown} />
        <footer className="source">출처: {article.source_type}{article.source_reference ? ` · ${article.source_reference}` : ""}<br />주제 slug: {article.slug}</footer>
      </article>
      {related.length > 0 && <section aria-label="관련 글"><h2>관련 글</h2>{related.map(item => <p key={item.id}><Link href={`/articles/${item.id}`}>{item.title}</Link></p>)}</section>}
      <button className="danger" disabled={busy} onClick={remove}>글 삭제</button>
    </>}
  </main>;
}
