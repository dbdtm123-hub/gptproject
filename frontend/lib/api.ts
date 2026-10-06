export type ArticleSummary = {
  id: string; title: string; slug: string; category_id: string; category: string; subcategory: string | null;
  summary: string; tags: string[]; status: "draft" | "published"; created_at: string; updated_at: string;
};
export type Article = ArticleSummary & {
  content_markdown: string; source_type: string; source_reference: string | null; related_articles: string[];
};
export type ArticlePage = { items: ArticleSummary[]; total: number; limit: number; offset: number };
export type Category = { id: string; name: string };

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api/${path}`, { ...options, headers: { "Content-Type": "application/json" }, cache: "no-store" });
  if (response.status === 204) return undefined as T;
  const data = await response.json();
  if (!response.ok) throw new Error(typeof data.detail === "string" ? data.detail : "입력 형식과 길이를 확인해 주세요.");
  return data as T;
}

export function date(value: string) {
  return new Date(value).toLocaleString("ko-KR");
}
