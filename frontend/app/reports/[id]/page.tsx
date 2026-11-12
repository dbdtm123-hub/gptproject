"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import Markdown from "../../../components/Markdown";
import { api, date } from "../../../lib/api";
import { compareMetrics, Metric, Report, ReportRevision } from "../../../lib/reports";

function Observation({ metric }: { metric?: Metric }) {
  if (!metric) return <>미수집</>;
  return <>{metric.value === null ? "미측정" : metric.value.toLocaleString("ko-KR")} {metric.unit}
    {metric.window_start && metric.window_end && <small className="metric-window">{date(metric.window_start)}–{date(metric.window_end)} ({(Date.parse(metric.window_end) - Date.parse(metric.window_start)) / 1000}s)</small>}
    {metric.observed_at && <small className="metric-window">{date(metric.observed_at)}</small>}{metric.note && <small className="metric-window">{metric.note}</small>}</>;
}
export default function ReportDetail() {
  const { id } = useParams<{ id: string }>(); const [report, setReport] = useState<Report | null>(null);
  const [history, setHistory] = useState<ReportRevision[]>([]); const [historyPage, setHistoryPage] = useState(0);
  const [historyLoaded, setHistoryLoaded] = useState(false); const [error, setError] = useState("");
  useEffect(() => {
    const c = new AbortController();
    api<Report>(`reports/${id}`, { signal: c.signal }).then(setReport).catch(e => { if (!c.signal.aborted) setError(e.message); });
    return () => c.abort();
  }, [id]);
  async function loadHistory(page: number) {
    try { setHistory(await api<ReportRevision[]>(`reports/${id}/history?limit=10&offset=${page * 10}`)); setHistoryLoaded(true); setHistoryPage(page); }
    catch (e) { setError((e as Error).message); }
  }
  return <main className="reading reports"><nav className="actions"><Link href="/reports">← 운영 리포트</Link>{report && <Link href={`/reports/${id}/edit`}>리포트 수정</Link>}</nav>
    {error && <p role="alert" className="notice">{error}</p>}{!report && !error && <p>불러오는 중…</p>}
    {report && <><article className="article-body"><header><div className="meta"><span>{report.project} / {report.component}</span><span className={`badge ${report.status.toLowerCase()}`}>{report.status}</span></div>
      <h1>{report.title}</h1><p className="lead">{report.summary}</p><p>{report.report_id} · {report.author} · {report.source} · 버전 {report.revision}</p><p className="dates">작성 {date(report.created_at)} · 수정 {date(report.updated_at)}</p></header>
      {[["환경·운영 건강", report.environment], ["확인한 사실", report.findings], ["실제로 적용한 변경", report.changes]].map(([title, content]) => content && <div key={title}><h2>{title}</h2><Markdown content={content} /></div>)}
      <h2>전후 측정 비교</h2>{report.metrics_before.length + report.metrics_after.length === 0 ? <p>구조화된 측정값이 없습니다. 원문 기록을 확인하세요.</p> : <div className="report-table"><table><thead><tr><th>지표</th><th>변경 전</th><th>변경 후</th><th>차이</th></tr></thead><tbody>{compareMetrics(report.metrics_before, report.metrics_after).map(row => <tr key={JSON.stringify([row.name, row.unit])}><th>{row.name}</th><td><Observation metric={row.before} /></td><td><Observation metric={row.after} /></td><td>{row.delta === null ? "—" : `${Number(row.delta.toFixed(4)).toLocaleString("ko-KR")} ${row.unit}`}</td></tr>)}</tbody></table></div>}
      <h2>남은 문제</h2><Markdown content={report.remaining_issues || "기록된 미해결 문제가 없습니다."} /><h2>제안·다음 조치</h2><Markdown content={report.next_actions || "기록된 다음 조치가 없습니다."} />
      <details><summary>원문 Markdown</summary><Markdown content={report.raw_markdown} /></details>
    </article>
    {(report.related_report_ids.length > 0 || report.related_article_ids.length > 0) && <section><h2>관련 기록</h2>{report.related_report_ids.map(link => <p key={link}><Link href={`/reports/${link}`}>{link}</Link></p>)}{report.related_article_ids.map(link => <p key={link}><Link href={`/admin/articles/${link}/edit`}>관련 기술 글 {link}</Link></p>)}</section>}
    <section><h2>수정 이력</h2><p>각 버전의 기록 시각과 원본은 보존됩니다.</p><button className="secondary" onClick={() => void loadHistory(0)}>이력 보기</button>
      {history.map(h => <details key={h.revision}><summary>버전 {h.revision} · {date(h.recorded_at)} · {h.actor}</summary><p>{h.snapshot.summary}</p><Markdown content={h.snapshot.raw_markdown} /></details>)}
      {historyLoaded && <nav className="actions" aria-label="수정 이력 페이지"><button disabled={historyPage === 0} onClick={() => void loadHistory(historyPage - 1)}>이전</button><span>{historyPage + 1}</span><button disabled={history.length < 10} onClick={() => void loadHistory(historyPage + 1)}>다음</button></nav>}
    </section></>}
  </main>;
}
