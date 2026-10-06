"use client";

import { useCallback, useEffect, useRef, useState } from "react";

type Candidate = { raw_text: string; category: string; subcategory: string | null; title: string;
  summary: string; tags: string[]; importance: number; occurred_at: string };
type Entry = Candidate & { id: string; category_id: string; created_at: string };
type Draft = Candidate & { draftId: string };
type Category = { id: string; name: string };

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/${path}`, { ...options, headers: { "Content-Type": "application/json" }, cache: "no-store" });
  if (response.status === 204) return undefined as T;
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "입력 내용을 확인해 주세요.");
  return data as T;
}

export default function Home() {
  const draftSequence = useRef(0);
  const [rawText, setRawText] = useState("");
  const [candidates, setCandidates] = useState<Draft[]>([]);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [filter, setFilter] = useState("");
  const [page, setPage] = useState(0);
  const [total, setTotal] = useState(0);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [detail, setDetail] = useState<Entry | null>(null);

  const load = useCallback(async (signal?: AbortSignal) => {
    const query = new URLSearchParams({ limit: "20", offset: String(page * 20) });
    if (filter) query.set("category_id", filter);
    const [timeline, cats] = await Promise.all([
      api<{ items: Entry[]; total: number }>(`entries?${query}`, { signal }),
      api<Category[]>("categories", { signal }),
    ]);
    setEntries(timeline.items); setTotal(timeline.total); setCategories(cats);
  }, [filter, page]);

  useEffect(() => {
    const controller = new AbortController();
    load(controller.signal).catch(error => { if (!controller.signal.aborted) setMessage(error.message); });
    return () => controller.abort();
  }, [load]);

  async function parse() {
    setBusy(true); setMessage(""); setCandidates([]);
    try {
      const parsed = await api<Candidate[]>("entries/parse", { method: "POST", body: JSON.stringify({ raw_text: rawText }) });
      setCandidates(parsed.map(item => ({ ...item, draftId: String(++draftSequence.current) })));
    }
    catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  }
  function manual() {
    setCandidates([{ draftId: String(++draftSequence.current), raw_text: rawText, category: "일상", subcategory: null, title: rawText.slice(0, 100),
      summary: rawText.slice(0, 5000), tags: [], importance: 3, occurred_at: new Date().toISOString() }]);
    setMessage("");
  }
  function edit(index: number, changes: Partial<Candidate>) {
    setCandidates(items => items.map((item, i) => i === index ? { ...item, ...changes } : item));
  }
  async function save() {
    setBusy(true); setMessage("");
    try {
      const records = candidates.map(({ draftId, ...entry }) => { void draftId; return entry; });
      await api<Entry[]>("entries/batch", { method: "POST", body: JSON.stringify({ entries: records }) });
      setCandidates([]); setRawText("");
      if (filter || page) { setFilter(""); setPage(0); } else await load();
      setMessage("기록을 저장했습니다.");
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  }
  async function remove(entry: Entry) {
    if (!window.confirm(`“${entry.title}” 기록을 삭제할까요?`)) return;
    setBusy(true);
    try {
      await api(`entries/${entry.id}`, { method: "DELETE" }); setDetail(null);
      if (entries.length === 1 && page > 0) setPage(page - 1); else await load();
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  }

  return <main>
    <header><span className="eyebrow">나의 하루를 모으는 곳</span><h1>Autolog</h1><p>일상을 적으면 AI가 기록으로 정리해 줍니다.</p></header>
    <section aria-labelledby="input-title"><h2 id="input-title">오늘의 기록</h2>
      <label htmlFor="raw">무슨 일이 있었나요?</label>
      <textarea id="raw" value={rawText} maxLength={20000} onChange={e => setRawText(e.target.value)}
        placeholder="벤치프레스 60kg 5x5 했고, OpenStack 장애를 해결했고, Cilium을 공부했어요." />
      <div className="actions"><button disabled={busy || !rawText.trim()} onClick={parse}>{busy ? "처리 중…" : "AI로 정리"}</button>
        <button className="secondary" disabled={busy || !rawText.trim()} onClick={manual}>직접 분류</button></div>
    </section>
    {message && <p className="notice" role="status">{message}</p>}
    {candidates.length > 0 && <section><h2>저장 전 확인 · {candidates.length}개</h2>
      {candidates.map((item, index) => <article className="candidate" key={item.draftId}>
        <label>제목<input value={item.title} maxLength={200} onChange={e => edit(index, { title: e.target.value })} /></label>
        <div className="columns"><label>카테고리<input value={item.category} maxLength={100} onChange={e => edit(index, { category: e.target.value })} /></label>
          <label>하위 분류<input value={item.subcategory || ""} maxLength={100} onChange={e => edit(index, { subcategory: e.target.value || null })} /></label></div>
        <label>요약<textarea value={item.summary} maxLength={5000} onChange={e => edit(index, { summary: e.target.value })} /></label>
        <label>태그 (쉼표로 구분)<input defaultValue={item.tags.join(", ")} onBlur={e => edit(index, { tags: e.target.value.split(",").map(tag => tag.trim()).filter(Boolean) })} /></label>
        <label>중요도<select value={item.importance} onChange={e => edit(index, { importance: Number(e.target.value) })}>{[1,2,3,4,5].map(value => <option key={value}>{value}</option>)}</select></label>
        <button className="secondary" onClick={() => setCandidates(items => items.filter((_, i) => i !== index))}>제외</button>
      </article>)}
      <button disabled={busy || candidates.some(item => !item.title.trim() || !item.category.trim() || !item.summary.trim())} onClick={save}>기록 {candidates.length}개 저장</button>
    </section>}
    <section><div className="section-header"><h2>타임라인 <small>{total}개</small></h2>
      <label>카테고리<select value={filter} onChange={e => { setFilter(e.target.value); setPage(0); }}>
        <option value="">전체</option>{categories.map(category => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label></div>
      {entries.length === 0 && <p className="empty">아직 기록이 없습니다. 첫 기록을 남겨보세요.</p>}
      {entries.map(entry => <article key={entry.id} className="entry"><div className="meta"><span>{entry.category}{entry.subcategory ? ` / ${entry.subcategory}` : ""}</span>
        <time dateTime={entry.occurred_at}>{new Date(entry.occurred_at).toLocaleString("ko-KR")}</time></div>
        <h3><button className="link" onClick={async () => { try { setDetail(await api<Entry>(`entries/${entry.id}`)); } catch (error) { setMessage((error as Error).message); } }}>{entry.title}</button></h3>
        <p>{entry.summary}</p><div className="tags">{entry.tags.map(tag => <span key={tag}>#{tag}</span>)}</div>
        <button className="danger" disabled={busy} onClick={() => remove(entry)}>삭제</button></article>)}
      <div className="actions"><button className="secondary" disabled={page === 0} onClick={() => setPage(page - 1)}>이전</button><span>{page + 1} 페이지</span>
        <button className="secondary" disabled={(page + 1) * 20 >= total} onClick={() => setPage(page + 1)}>다음</button></div>
    </section>
    {detail && <div className="modal" role="dialog" aria-modal="true" aria-labelledby="detail-title"><section><h2 id="detail-title">{detail.title}</h2>
      <p>{detail.category} · 중요도 {detail.importance}</p><p>{detail.summary}</p><h3>원본 입력</h3><p className="raw">{detail.raw_text}</p>
      <button onClick={() => setDetail(null)}>닫기</button></section></div>}
  </main>;
}
