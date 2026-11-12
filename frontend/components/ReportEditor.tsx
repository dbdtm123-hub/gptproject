"use client";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { Metric, Report, ReportFields, statuses } from "../lib/reports";

const initial: ReportFields = { title: "", project: "", component: "", author: "운영자", source: "Manual", status: "PARTIAL", summary: "", environment: "", findings: "", changes: "", metrics_before: [], metrics_after: [], remaining_issues: "", next_actions: "", raw_markdown: "", related_report_ids: [], related_article_ids: [] };
function Measurements({ title, values, onChange }: { title: string; values: Metric[]; onChange: (value: Metric[]) => void }) {
  function change(index: number, patch: Partial<Metric>) { onChange(values.map((v, i) => i === index ? { ...v, ...patch } : v)); }
  return <fieldset className="report-measurements"><legend>{title}</legend>{values.map((m, i) => <div key={i} className="measurement-entry"><div className="report-filters">
    <label>지표 이름<input required value={m.name} maxLength={200} onChange={e => change(i, { name: e.target.value })} /></label>
    <label>측정값 (비우면 미측정)<input type="number" step="any" value={m.value ?? ""} onChange={e => change(i, { value: e.target.value === "" ? null : Number(e.target.value) })} /></label>
    <label>단위<input required value={m.unit} maxLength={200} placeholder="%, MB/s, °C" onChange={e => change(i, { unit: e.target.value })} /></label></div>
    <div className="report-filters">{([['observed_at', '측정 시각'], ['window_start', '관측 구간 시작'], ['window_end', '관측 구간 끝']] as const).map(([key, label]) => <label key={key}>{label}<input type="text" value={m[key] || ""} placeholder="2026-11-12T11:39:15Z" onChange={e => change(i, { [key]: e.target.value || null })} /></label>)}</div>
    <label>측정 설명<input value={m.note || ""} maxLength={2000} onChange={e => change(i, { note: e.target.value })} /></label><button className="secondary" type="button" onClick={() => onChange(values.filter((_, n) => n !== i))}>측정 항목 빼기</button>
  </div>)}<button type="button" className="secondary" disabled={values.length >= 100} onClick={() => onChange([...values, { name: "", value: null, unit: "" }])}>측정 항목 추가</button></fieldset>;
}
export default function ReportEditor({ reportId }: { reportId?: string }) {
  const router = useRouter(); const [fields, setFields] = useState<ReportFields>(initial);
  const [id, setId] = useState(reportId || ""); const [revision, setRevision] = useState<number>();
  const [relatedReports, setRelatedReports] = useState(""); const [relatedArticles, setRelatedArticles] = useState("");
  const [error, setError] = useState(""); const [busy, setBusy] = useState(false); const [loaded, setLoaded] = useState(!reportId);
  useEffect(() => {
    if (!reportId) return;
    const c = new AbortController();
    api<Report>(`reports/${reportId}`, { signal: c.signal }).then(value => {
      const { report_id, revision, created_at: _created, updated_at: _updated, ...body } = value;
      setId(report_id); setRevision(revision); setFields(body); setRelatedReports(body.related_report_ids.join("\n")); setRelatedArticles(body.related_article_ids.join("\n")); setLoaded(true);
    }).catch(e => { if (!c.signal.aborted) setError(e.message); });
    return () => c.abort();
  }, [reportId]);
  function field<K extends keyof ReportFields>(key: K, value: ReportFields[K]) { setFields(old => ({ ...old, [key]: value })); }
  async function save(e: React.FormEvent) {
    e.preventDefault(); setBusy(true); setError("");
    const lines = (value: string) => value.split(/[\n,]/).map(v => v.trim()).filter(Boolean);
    const payload = { ...fields, related_report_ids: lines(relatedReports), related_article_ids: lines(relatedArticles),
      ...(reportId ? { expected_revision: revision } : { report_id: id }) };
    try { const value = await api<Report>(reportId ? `reports/${reportId}` : "reports", { method: reportId ? "PATCH" : "POST", body: JSON.stringify(payload) }); router.push(`/reports/${value.report_id}`); }
    catch (e) { setError((e as Error).message + " 충돌한 경우 최신 리포트를 다시 읽고 변경 내용을 확인해 주세요."); }
    finally { setBusy(false); }
  }
  return <main className="editor reports"><nav><Link href={reportId ? `/reports/${reportId}` : "/reports"}>← 돌아가기</Link></nav><h1>{reportId ? "리포트 수정" : "운영 리포트 작성"}</h1>
    <p>실행한 변경과 제안을 구분하고, 수집하지 않은 측정값은 비워 두세요. 시각에는 시간대(Z 또는 +09:00)를 포함합니다.</p>
    {error && <p className="notice" role="alert">{error}</p>}{!loaded && !error && <p>불러오는 중…</p>}
    {loaded && <form onSubmit={save}><section><label>리포트 ID<input required disabled={!!reportId} value={id} maxLength={100} pattern="[A-Z][A-Z0-9]*(-[A-Z0-9]+)+-[0-9]{4,}" placeholder="OPENSTACK-RABBITMQ-0020" onChange={e => setId(e.target.value)} /></label>
      <label>제목<input required maxLength={200} value={fields.title} onChange={e => field("title", e.target.value)} /></label>
      <div className="report-filters">{([['project', '프로젝트'], ['component', '컴포넌트'], ['author', '작성자']] as const).map(([key, label]) => <label key={key}>{label}<input required maxLength={200} value={fields[key]} onChange={e => field(key, e.target.value)} /></label>)}</div>
      <div className="report-filters"><label>출처<select aria-label="출처" value={fields.source} onChange={e => field("source", e.target.value as ReportFields['source'])}>{['Codex', 'ChatGPT', 'Manual'].map(v => <option key={v}>{v}</option>)}</select></label>
        <label>실행 상태<select aria-label="실행 상태" value={fields.status} onChange={e => field("status", e.target.value as ReportFields['status'])}>{statuses.map(s => <option key={s}>{s}</option>)}</select></label></div>
      <label>요약<textarea aria-label="요약" required maxLength={10000} value={fields.summary} onChange={e => field("summary", e.target.value)} /></label>
      {([['environment', '환경·운영 건강'], ['findings', '조사한 내용·확인된 사실'], ['changes', '실제로 적용한 변경'], ['remaining_issues', '미해결 문제'], ['next_actions', '제안·다음 조치']] as const).map(([key, label]) => <label key={key}>{label}<textarea aria-label={label} maxLength={50000} value={fields[key]} onChange={e => field(key, e.target.value)} /></label>)}
    </section><section><Measurements title="변경 전 측정" values={fields.metrics_before} onChange={v => field("metrics_before", v)} /><Measurements title="변경 후 측정" values={fields.metrics_after} onChange={v => field("metrics_after", v)} /></section>
    <section><label>원문 Markdown<textarea aria-label="원문 Markdown" required maxLength={200000} className="report-markdown" value={fields.raw_markdown} onChange={e => field("raw_markdown", e.target.value)} /></label>
      <label>관련 리포트 ID (한 줄에 하나)<textarea value={relatedReports} onChange={e => setRelatedReports(e.target.value)} /></label><label>관련 기술 글 ID (한 줄에 하나)<textarea value={relatedArticles} onChange={e => setRelatedArticles(e.target.value)} /></label></section>
      <button disabled={busy} type="submit">{busy ? "저장 중…" : "비공개 리포트 저장"}</button></form>}
  </main>;
}
