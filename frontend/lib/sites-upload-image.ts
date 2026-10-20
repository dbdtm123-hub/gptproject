// Server-only integration module. Do not import into a client component.
import { ImageConnection, ImageTransportError, uploadAuthorizedImage } from "./autolog-image-transport";

export type PlatformFileRef = {
  download_url: string; file_id: string; mime_type?: string; file_name?: string;
};
export type UploadImageDependencies = {
  /** Reuse the existing Sites identity and per-user AutoLog connection lookup. */
  requireConnection(request: Request): Promise<ImageConnection>;
  /** REQUIRED host integration, not a built-in Sites API.
   * Authorize this file for this caller/plugin BEFORE opening it. Return a response
   * from the verified platform file reader, or an egress-restricted download.
   * Never forward AutoLog tokens/cookies. Honor signal throughout reading.
   * A caller-supplied URL/file_id or authenticated user alone is not authorization.
   */
  openAuthorizedFile(request: Request, file: PlatformFileRef, signal: AbortSignal): Promise<Response>;
  backendFetch?: typeof fetch;
};

const LIMIT = 10 * 1024 * 1024;
function invalid(): never { throw new ImageTransportError(422, "Select a real image file and provide its description"); }
function parse(value: unknown): { image: PlatformFileRef; alt: string } {
  if (!value || typeof value !== "object" || Array.isArray(value)) return invalid();
  const input = value as Record<string, unknown>;
  if (Object.keys(input).some(key => !["image", "alt"].includes(key))) return invalid();
  if (typeof input.alt !== "string" || !input.alt.trim() || input.alt.length > 200) return invalid();
  if (!input.image || typeof input.image !== "object" || Array.isArray(input.image)) return invalid();
  const image = input.image as Record<string, unknown>;
  if (Object.keys(image).some(key => !["download_url", "file_id", "mime_type", "file_name"].includes(key))) return invalid();
  if (typeof image.download_url !== "string" || image.download_url.length > 8192
      || typeof image.file_id !== "string" || !image.file_id.trim() || image.file_id.length > 1024) return invalid();
  for (const field of ["mime_type", "file_name"]) {
    if (image[field] !== undefined && (typeof image[field] !== "string" || (image[field] as string).length > 1024)) return invalid();
  }
  let url: URL;
  try { url = new URL(image.download_url); } catch { return invalid(); }
  if (url.protocol !== "https:" || url.username || url.password || url.hash || (url.port && url.port !== "443")) return invalid();
  // This syntax check is NOT SSRF protection. The mandatory host reader enforces policy.
  return { image: image as PlatformFileRef, alt: input.alt };
}

async function readImage(response: Response): Promise<Blob> {
  if (response.status !== 200 || !response.body) {
    await response.body?.cancel();
    throw new ImageTransportError(502, "Selected image could not be read; select it again");
  }
  const length = response.headers.get("content-length");
  if (length && (!/^\d+$/.test(length) || Number(length) > LIMIT)) {
    await response.body.cancel();
    throw new ImageTransportError(413, "Image must be 10 MiB or smaller");
  }
  const reader = response.body.getReader(); const chunks: Uint8Array<ArrayBuffer>[] = []; let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read(); if (done) break;
      total += value.byteLength;
      if (total > LIMIT) throw new ImageTransportError(413, "Image must be 10 MiB or smaller");
      chunks.push(new Uint8Array(value));
    }
  } finally { await reader.cancel(); reader.releaseLock(); }
  const bytes = new Uint8Array(await new Blob(chunks).arrayBuffer());
  if (!bytes.length) throw new ImageTransportError(422, "Image is empty");
  // FileRef MIME/name are optional hints. Sniff only to choose the declared MIME;
  // the existing backend performs full Pillow format/decode/pixel/frame validation.
  const head = Array.from(bytes.subarray(0, 12), byte => String.fromCharCode(byte)).join("");
  const mime = head.startsWith("\x89PNG\r\n\x1a\n") ? "image/png"
    : bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255 ? "image/jpeg"
    : /^(GIF87a|GIF89a)/.test(head) ? "image/gif"
    : head.startsWith("RIFF") && head.slice(8, 12) === "WEBP" ? "image/webp" : null;
  if (!mime) throw new ImageTransportError(415, "Only PNG, JPEG, WebP and GIF are supported");
  return new Blob([bytes], { type: mime });
}

export function createUploadImageTool(deps: UploadImageDependencies) {
  if (typeof deps.requireConnection !== "function" || typeof deps.openAuthorizedFile !== "function")
    throw new Error("Do not register upload_image until identity and authorized file reading are connected");
  let active = 0;
  return async (request: Request, args: unknown) => {
    let acquired = false;
    try {
      const connection = await deps.requireConnection(request);
      if (!connection.token) throw new ImageTransportError(401, "AutoLog authentication is required");
      const { image, alt } = parse(args);
      if (active >= 1) throw new ImageTransportError(429, "Image upload is busy; wait before retrying");
      active++; acquired = true;
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15_000);
      let file: Blob;
      try {
        const response = await deps.openAuthorizedFile(request, image, controller.signal);
        file = await readImage(response);
        controller.signal.throwIfAborted();
      } catch (error) {
        if (controller.signal.aborted) throw new ImageTransportError(504, "Selected image read timed out");
        throw error;
      } finally { clearTimeout(timer); }
      const result = await uploadAuthorizedImage(file, alt, connection, deps.backendFetch);
      return { content: [{ type: "text" as const, text: JSON.stringify(result) }], structuredContent: result, isError: false };
    } catch (error) {
      const status = error instanceof ImageTransportError ? error.status : 502;
      const message = error instanceof ImageTransportError ? error.message : "Image upload failed; no article was changed";
      return { content: [{ type: "text" as const, text: JSON.stringify({ error: { status, message } }) }], isError: true };
    } finally { if (acquired) active--; }
  };
}
