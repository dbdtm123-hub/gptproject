"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, Article, ArticlePage, Category } from "../lib/api";
import Markdown from "./Markdown";

type Draft = { title: string; slug: string; category: string; subcategory: string; summary: string; content_markdown: string;
  tags: string; source_type: string; source_reference: string; status: "draft" | "published"; related_articles: string[] };
const empty: Draft = { title: "", slug: "", category: "기술", subcategory: "", summary: "", tags: "", source_type: "manual",
  source_reference: "", status: "draft", related_articles: [], content_markdown: "# 제목\n\n## 구성 환경\n\n## 구축 과정\n\n## 발생한 문제\n\n## 원인과 해결\n\n## 오늘 배운 것\n" };

export default function ArticleEditor({ id }: { id?: string }) {
  const router = useRouter(); const [draft, setDraft] = useState<Draft>(empty);
  const [version, setVersion] = useState(""); const [loaded, setLoaded] = useState(!id);
  const [categories, setCategories] = useState<Category[]>([]); const [choices, setChoices] = useState<ArticlePage["items"]>([]);
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false); const [preview, setPreview] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    api<Category[]>("categories", { signal: controller.signal }).then(setCategories).catch(() => {});
    const mergeChoices = (items: ArticlePage["items"]) => setChoices(current => Array.from(new Map([...current, ...items].map(item => [item.id, item])).values()));
    api<ArticlePage>("articles?limit=100", { signal: controller.signal }).then(page => mergeChoices(page.items)).catch(() => {});
    if (id) api<Article>(`articles/${id}`, { signal: controller.signal }).then(async article => {
      setDraft({ ...article, subcategory: article.subcategory || "", source_reference: article.source_reference || "", tags: article.tags.join(", ") });
      setVersion(article.updated_at); setLoaded(true);
      const related = await Promise.allSettled(article.related_articles.map(relatedId => api<Article>(`articles/${relatedId}`, { signal: controller.signal })));
      if (!controller.signal.aborted) mergeChoices(related.flatMap(result => result.status === "fulfilled" ? [result.value] : []));
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, [id]);
  function change<K extends keyof Draft>(key: K, value: Draft[K]) { setDraft(previous => ({ ...previous, [key]: value })); }
  async function save(event: React.FormEvent) {
    event.preventDefault(); setBusy(true); setError("");
    const body = { title: draft.title, ...(draft.slug ? { slug: draft.slug } : {}), category: draft.category,
      subcategory: draft.subcategory.trim() || null, summary: draft.summary, content_markdown: draft.content_markdown,
      tags: draft.tags.split(",").map(t => t.trim()).filter(Boolean), source_type: draft.source_type,
      source_reference: draft.source_reference.trim() || null, status: draft.status, related_articles: draft.related_articles,
      ...(id ? { expected_updated_at: version } : {}) };
    try { const saved = await api<Article>(id ? `articles/${id}` : "articles", { method: id ? "PATCH" : "POST", body: JSON.stringify(body) }); router.push(`/articles/${saved.id}`); }
    catch (e) { setError((e as Error).message); setBusy(false); }
  }
  return <main className="editor"><nav><Link href={id ? `/articles/${id}` : "/"}>← 돌아가기</Link></nav><h1>{id ? "글 수정" : "직접 작성"}</h1>
    {error && <p className="notice" role="alert">{error}</p>}
    {!loaded ? <p>글을 불러오는 중…</p> : <form onSubmit={save}>
      <section><label>제목<input required maxLength={200} value={draft.title} onChange={e => change("title", e.target.value)} /></label>
        <label>Slug (비우면 제목에서 생성)<input maxLength={200} pattern="[a-z0-9가-힣]+(-[a-z0-9가-힣]+)*" value={draft.slug} onChange={e => change("slug", e.target.value)} /></label>
        <div className="columns"><label>카테고리<input required list="categories" maxLength={100} value={draft.category} onChange={e => change("category", e.target.value)} /></label>
          <label>하위 분류<input maxLength={100} value={draft.subcategory} onChange={e => change("subcategory", e.target.value)} /></label></div>
        <datalist id="categories">{categories.map(c => <option key={c.id} value={c.name} />)}</datalist>
        <label htmlFor="article-summary">요약</label><textarea id="article-summary" required maxLength={5000} value={draft.summary} onChange={e => change("summary", e.target.value)} />
        <label>태그 (쉼표로 구분)<input value={draft.tags} onChange={e => change("tags", e.target.value)} /></label>
        <label>상태<select aria-label="글 상태" value={draft.status} onChange={e => change("status", e.target.value as Draft["status"])}><option value="draft">초안</option><option value="published">발행됨</option></select></label>
        <label>출처 유형<input required maxLength={100} value={draft.source_type} onChange={e => change("source_type", e.target.value)} /></label>
        <label>출처 참조<input maxLength={2000} value={draft.source_reference} onChange={e => change("source_reference", e.target.value)} /></label>
        <label>관련 글<select aria-label="관련 글 선택" multiple value={draft.related_articles} onChange={e => change("related_articles", Array.from(e.target.selectedOptions, option => option.value))}>
          {choices.filter(article => article.id !== id).map(article => <option key={article.id} value={article.id}>{article.title}</option>)}</select></label>
        <small>최근 100개 글에서 선택할 수 있습니다. API에서는 글 ID로 관련 글을 지정할 수 있습니다.</small>
      </section>
      <section><div className="section-header"><h2>Markdown 본문</h2><button type="button" className="secondary" onClick={() => setPreview(!preview)}>{preview ? "편집하기" : "미리보기"}</button></div>
        {preview ? <Markdown content={draft.content_markdown} /> : <div><label htmlFor="article-content">본문</label><textarea id="article-content" className="markdown-input" required maxLength={200000} value={draft.content_markdown} onChange={e => change("content_markdown", e.target.value)} /></div>}
      </section><button disabled={busy || !draft.title.trim() || !draft.summary.trim() || !draft.content_markdown.trim()} type="submit">{busy ? "저장 중…" : "글 저장"}</button>
      {id && <p className="source">편집 중 다른 클라이언트가 글을 수정하면 저장을 차단합니다. 충돌 시 입력을 보관하고 최신 글을 다시 확인하세요.</p>}
    </form>}
  </main>;
}
