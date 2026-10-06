"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { api, ArticlePage, Category, date } from "../lib/api";

export default function Blog() {
  const [data, setData] = useState<ArticlePage>({ items: [], total: 0, limit: 20, offset: 0 });
  const [categories, setCategories] = useState<Category[]>([]);
  const [tags, setTags] = useState<string[]>([]);
  const [category, setCategory] = useState("");
  const [tag, setTag] = useState("");
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(0);
  const [error, setError] = useState("");
  const [loaded, setLoaded] = useState(false);

  const load = useCallback(async (signal: AbortSignal) => {
    const params = new URLSearchParams({ limit: "20", offset: String(page * 20) });
    if (category) params.set("category_id", category);
    if (tag) params.set("tag", tag);
    if (status) params.set("status", status);
    if (query) params.set("q", query);
    const [articles, cats, allTags] = await Promise.all([
      api<ArticlePage>(`articles?${params}`, { signal }), api<Category[]>("categories", { signal }), api<string[]>("tags", { signal }),
    ]);
    setData(articles); setCategories(cats); setTags(allTags); setLoaded(true); setError("");
    if (articles.total > 0 && page * 20 >= articles.total) setPage(Math.ceil(articles.total / 20) - 1);
  }, [category, tag, status, query, page]);

  useEffect(() => {
    const controller = new AbortController(); let pending = false;
    const refresh = () => {
      if (pending || document.visibilityState === "hidden") return;
      pending = true;
      load(controller.signal).catch(e => { if (!controller.signal.aborted) setError(e.message); }).finally(() => { pending = false; });
    };
    refresh(); const interval = window.setInterval(refresh, 3000);
    window.addEventListener("focus", refresh); document.addEventListener("visibilitychange", refresh);
    return () => { controller.abort(); window.clearInterval(interval); window.removeEventListener("focus", refresh); document.removeEventListener("visibilitychange", refresh); };
  }, [load]);

  return <main className="blog">
    <header className="blog-header"><div><span className="eyebrow">대화에서 지식으로</span><h1>AutoLog</h1>
      <p>작업 과정과 문제 해결을 오래 읽을 수 있는 기술 글로 남깁니다.</p></div>
      <Link className="button" href="/articles/new">직접 작성</Link></header>
    <section className="filters" aria-label="글 검색과 필터">
      <form className="search" onSubmit={e => { e.preventDefault(); setQuery(search.trim()); setPage(0); }}>
        <label htmlFor="search">검색</label><div className="actions"><input id="search" maxLength={200} value={search} onChange={e => setSearch(e.target.value)} placeholder="제목, 요약, 본문에서 검색" /><button type="submit">검색</button></div>
      </form>
      <div className="columns">
        <label>카테고리<select aria-label="카테고리 필터" value={category} onChange={e => { setCategory(e.target.value); setPage(0); }}><option value="">전체 카테고리</option>{categories.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
        <label>태그<select aria-label="태그 필터" value={tag} onChange={e => { setTag(e.target.value); setPage(0); }}><option value="">전체 태그</option>{tags.map(t => <option key={t}>{t}</option>)}</select></label>
        <label>상태<select aria-label="상태 필터" value={status} onChange={e => { setStatus(e.target.value); setPage(0); }}><option value="">전체 상태</option><option value="published">발행됨</option><option value="draft">초안</option></select></label>
      </div>
    </section>
    {error && <p className="notice" role="alert">{error}</p>}
    <div className="section-header"><h2>기술 노트 <small>{data.total}개</small></h2><Link href="/legacy">이전 라이프로그</Link></div>
    {!loaded && !error && <p>글을 불러오는 중…</p>}
    {loaded && data.items.length === 0 && <section><p>조건에 맞는 글이 없습니다. ChatGPT에서 글을 저장하거나 직접 작성해 보세요.</p></section>}
    <div className="article-grid">{data.items.map(article => <article key={article.id} className="article-card">
      <div className="meta"><span>{article.category}{article.subcategory ? ` / ${article.subcategory}` : ""}</span><span className={`badge ${article.status}`}>{article.status === "published" ? "발행됨" : "초안"}</span></div>
      <h2><Link href={`/articles/${article.id}`}>{article.title}</Link></h2><p>{article.summary}</p>
      <div className="tags">{article.tags.map(t => <button className="tag" key={t} onClick={() => { setTag(t); setPage(0); }}>#{t}</button>)}</div>
      <div className="dates">작성 <time dateTime={article.created_at}>{date(article.created_at)}</time><br />수정 <time dateTime={article.updated_at}>{date(article.updated_at)}</time></div>
    </article>)}</div>
    <div className="actions pagination"><button className="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>이전</button><span>{page + 1} 페이지</span><button className="secondary" disabled={(page + 1) * 20 >= data.total} onClick={() => setPage(page + 1)}>다음</button></div>
  </main>;
}
