"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, date } from "../../lib/api";
import { Report, ReportPage, statuses } from "../../lib/reports";

export default function ReportsDashboard() {
  const [data, setData] = useState<ReportPage>({ items: [], total: 0, limit: 10, offset: 0 });
  const [index, setIndex] = useState<{ projects: string[]; components: string[] }>({ projects: [], components: [] });
  const [latest, setLatest] = useState<Report[]>([]);
  const [project, setProject] = useState(""); const [component, setComponent] = useState("");
  const [status, setStatus] = useState(""); const [q, setQuery] = useState(""); const [search, setSearch] = useState("");
  const [sort, setSort] = useState("newest"); const [page, setPage] = useState(0);
  const [unresolved, setUnresolved] = useState(false); const [error, setError] = useState(""); const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    api<typeof index>("reports/projects", { signal: controller.signal }).then(async value => {
      setIndex(value);
      const results = await Promise.all(value.projects.map(p => api<Report>(`reports/latest?${new URLSearchParams({ project: p })}`, { signal: controller.signal })));
      if (!controller.signal.aborted) setLatest(results);
    }).catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const controller = new AbortController(); setLoading(true);
    const params = new URLSearchParams({ limit: "10", offset: String(page * 10), sort });
    for (const [key, value] of Object.entries({ project, component, status, q })) if (value) params.set(key, value);
    api<ReportPage>(`reports${unresolved ? "/unresolved" : ""}?${params}`, { signal: controller.signal }).then(value => {
      if (!controller.signal.aborted) { setData(value); setError(""); setLoading(false); }
    }).catch(e => { if (!controller.signal.aborted) { setError(e.message); setLoading(false); } });
    return () => controller.abort();
  }, [project, component, status, q, sort, page, unresolved]);
  return <main className="blog reports">
    <header className="blog-header"><div><Link href="/">← 기술 블로그</Link><h1>운영 리포트</h1><p>실행한 변경과 측정 결과, 남은 문제를 비공개로 기록합니다.</p></div>
      <Link className="button" href="/reports/new">리포트 작성</Link></header>
    {latest.length > 0 && <section aria-label="프로젝트별 최신 리포트"><h2>프로젝트별 최신 실행</h2><div className="report-projects">{latest.map(r => <Link key={r.project} href={`/reports/${r.report_id}`}><strong>{r.project}</strong><span>{r.title}</span><small>{r.status} · {date(r.created_at)}</small></Link>)}</div></section>}
    <section aria-label="리포트 검색과 필터">
      <form className="search" onSubmit={e => { e.preventDefault(); setQuery(search.trim()); setPage(0); }}><label htmlFor="report-search">제목·내용 검색</label><div className="actions"><input id="report-search" maxLength={200} value={search} onChange={e => setSearch(e.target.value)} /><button>검색</button></div></form>
      <div className="report-filters"><label>프로젝트<select aria-label="프로젝트" value={project} onChange={e => { setProject(e.target.value); setPage(0); }}><option value="">전체 프로젝트</option>{index.projects.map(p => <option key={p}>{p}</option>)}</select></label>
        <label>컴포넌트<select aria-label="컴포넌트" value={component} onChange={e => { setComponent(e.target.value); setPage(0); }}><option value="">전체 컴포넌트</option>{index.components.map(p => <option key={p}>{p}</option>)}</select></label>
        <label>실행 상태<select aria-label="실행 상태" value={status} onChange={e => { setStatus(e.target.value); setPage(0); }}><option value="">전체 상태</option>{statuses.map(s => <option key={s}>{s}</option>)}</select></label>
        <label>날짜 정렬<select aria-label="날짜 정렬" value={sort} onChange={e => { setSort(e.target.value); setPage(0); }}><option value="newest">최신 실행순</option><option value="oldest">오래된 실행순</option></select></label></div>
      <label className="report-check"><input type="checkbox" checked={unresolved} onChange={e => { setUnresolved(e.target.checked); setPage(0); }} />컴포넌트별 최신 실행의 미해결 문제만</label>
    </section>
    {error && <p className="notice" role="alert">{error}</p>}
    <h2>리포트 {data.total}개</h2>
    {loading && <p role="status">불러오는 중…</p>}
    {!loading && !data.items.length && <p>조건에 맞는 리포트가 없습니다.</p>}
    <div className="article-grid">{data.items.map(r => <article className="article-card" key={r.report_id}><div className="meta"><span>{r.project} / {r.component}</span><span className={`badge ${r.status.toLowerCase()}`}>{r.status}</span></div>
      <h2><Link href={`/reports/${r.report_id}`}>{r.title}</Link></h2><small>{r.report_id}</small><p>{r.summary}</p>
      {r.remaining_issues && <p className="report-issue"><strong>미해결:</strong> {r.remaining_issues.slice(0, 180)}{r.remaining_issues.length > 180 ? "…" : ""}</p>}
      <p className="dates">실행 기록 {date(r.created_at)} · 수정 {date(r.updated_at)}</p></article>)}</div>
    <nav className="pagination actions" aria-label="리포트 페이지"><button className="secondary" disabled={page === 0 || loading} onClick={() => setPage(page - 1)}>이전</button><span>{page + 1} / {Math.max(1, Math.ceil(data.total / 10))}</span><button className="secondary" disabled={(page + 1) * 10 >= data.total || loading} onClick={() => setPage(page + 1)}>다음</button></nav>
  </main>;
}
