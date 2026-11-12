export type Metric = {
  name: string; value: number | null; unit: string;
  observed_at?: string | null; window_start?: string | null; window_end?: string | null; note?: string;
};
export type ReportFields = {
  title: string; project: string; component: string; author: string; source: "Codex" | "ChatGPT" | "Manual";
  status: "SUCCESS" | "PARTIAL" | "BLOCKED" | "FAILED"; summary: string; environment: string;
  findings: string; changes: string; metrics_before: Metric[]; metrics_after: Metric[];
  remaining_issues: string; next_actions: string; raw_markdown: string;
  related_report_ids: string[]; related_article_ids: string[];
};
export type Report = ReportFields & { report_id: string; created_at: string; updated_at: string; revision: number };
export type ReportSummary = Pick<Report, "report_id" | "title" | "project" | "component" | "source" | "status" | "summary" | "remaining_issues" | "created_at" | "updated_at" | "revision">;
export type ReportPage = { items: ReportSummary[]; total: number; limit: number; offset: number };
export type ReportRevision = { report_id: string; revision: number; recorded_at: string; actor: string; snapshot: Report };
export const statuses = ["SUCCESS", "PARTIAL", "BLOCKED", "FAILED"] as const;

export function compareMetrics(before: Metric[], after: Metric[]) {
  const keys = Array.from(new Set([...before, ...after].map(m => JSON.stringify([m.name, m.unit]))));
  return keys.map(key => {
    const [name, unit] = JSON.parse(key) as [string, string];
    const previous = before.find(m => m.name === name && m.unit === unit);
    const current = after.find(m => m.name === name && m.unit === unit);
    const delta = previous?.value != null && current?.value != null ? current.value - previous.value : null;
    return { name, unit, before: previous, after: current, delta };
  });
}
