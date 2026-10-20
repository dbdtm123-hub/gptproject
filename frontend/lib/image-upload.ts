export type UploadedImage = { url: string; key: string };

export function imageMarkdown(description: string, result: UploadedImage): string {
  const url = new URL(result.url);
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash)
    throw new Error("이미지 URL을 확인해 주세요.");
  const alt = description.trim().replace(/[\r\n]+/g, " ").replace(/[\\\[\]]/g, "\\$&");
  if (!alt) throw new Error("이미지 설명을 입력해 주세요.");
  return `![${alt}](${url.href.replace(/\(/g, "%28").replace(/\)/g, "%29")})`;
}

/** Successful uploads only, bounded to this editor instance. Never retries failed requests. */
export class ImageUploadCache {
  private results = new Map<string, UploadedImage>();
  private pending = new Map<string, Promise<UploadedImage>>();
  async upload(file: File, send: (file: File) => Promise<UploadedImage>): Promise<UploadedImage> {
    // WebCrypto requires HTTPS (or localhost). Preserve uploads on older HTTP installs.
    if (!globalThis.crypto?.subtle) return send(file);
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    const hash = Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
    const existing = this.results.get(hash);
    if (existing) return existing;
    const pending = this.pending.get(hash);
    if (pending) return pending;
    const request = Promise.resolve().then(() => send(file)).then(result => {
      // Validate before caching. The server remains responsible for decoding and MIME validation.
      imageMarkdown("검증", result);
      if (this.results.size >= 100) this.results.delete(this.results.keys().next().value!);
      this.results.set(hash, result);
      return result;
    }).finally(() => this.pending.delete(hash));
    this.pending.set(hash, request);
    return request;
  }
}
