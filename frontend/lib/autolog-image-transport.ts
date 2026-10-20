import { imageMarkdown, UploadedImage } from "./image-upload";

export class ImageTransportError extends Error {
  constructor(public readonly status: number, message: string) { super(message); }
}

export type ImageConnection = { backendOrigin: string; publicImageBase: string; token: string };

function httpsURL(value: string): URL {
  let url: URL;
  try { url = new URL(value); }
  catch { throw new ImageTransportError(503, "Image connection is invalid"); }
  if (url.protocol !== "https:" || url.username || url.password || url.search || url.hash)
    throw new ImageTransportError(503, "Image connection is invalid");
  return url;
}

/** Server-side adapter for an authenticated host's already-authorized file bytes.
 * Never expose connection/token in a model input schema. This does not resolve FileRefs.
 * The owning Sites server must authorize and read a real platform file before calling it.
 */
export async function uploadAuthorizedImage(
  file: Blob, alt: string, connection: ImageConnection, fetcher: typeof fetch = fetch,
): Promise<UploadedImage & { markdown: string }> {
  if (!connection.token) throw new ImageTransportError(401, "AutoLog authentication is required");
  if (!alt.trim() || alt.length > 200) throw new ImageTransportError(422, "Image description is required (max 200 characters)");
  if (!file.size) throw new ImageTransportError(422, "Image is empty");
  if (file.size > 10 * 1024 * 1024) throw new ImageTransportError(413, "Image must be 10 MiB or smaller");
  const extensions: Record<string, string> = { "image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif" };
  if (!extensions[file.type]) throw new ImageTransportError(415, "Unsupported image MIME type");
  const origin = httpsURL(connection.backendOrigin);
  if (origin.pathname !== "/") throw new ImageTransportError(503, "Backend origin must not contain a path");
  const base = httpsURL(connection.publicImageBase).href.replace(/\/$/, "");
  const form = new FormData(); form.append("file", file, `image.${extensions[file.type]}`);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30_000);
  try {
    const response = await fetcher(new URL("/api/uploads", origin), {
      method: "POST", headers: { Authorization: `Bearer ${connection.token}` }, body: form,
      redirect: "error", credentials: "omit", signal: controller.signal,
    });
    if (response.status !== 201) {
      await response.body?.cancel();
      const messages: Record<number, string> = {
        401: "AutoLog authentication failed", 403: "AutoLog upload is forbidden",
        413: "Image must be 10 MiB or smaller", 415: "Unsupported image MIME type",
        422: "Invalid or unsafe image data", 502: "Image storage is unavailable", 503: "Image storage is not configured",
      };
      throw new ImageTransportError(messages[response.status] ? response.status : 502,
        messages[response.status] || "Image upload failed");
    }
    if (!response.headers.get("content-type")?.includes("application/json")) {
      await response.body?.cancel();
      throw new ImageTransportError(502, "Invalid upload response");
    }
    // Bound the response too; never buffer upstream HTML or arbitrary data unboundedly.
    const reader = response.body?.getReader();
    if (!reader) throw new ImageTransportError(502, "Invalid upload response");
    let text = "", size = 0;
    const decoder = new TextDecoder();
    try {
      while (true) {
        const chunk = await reader.read(); if (chunk.done) break;
        size += chunk.value.byteLength;
        if (size > 16 * 1024) throw new ImageTransportError(502, "Invalid upload response");
        text += decoder.decode(chunk.value, { stream: true });
      }
      text += decoder.decode();
    } finally { await reader.cancel(); reader.releaseLock(); }
    const result = JSON.parse(text) as UploadedImage;
    if (typeof result.key !== "string" || !/^uploads\/\d{4}\/\d{2}\/[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}\.(png|jpg|webp|gif)$/.test(result.key)
        || !result.key.endsWith(`.${extensions[file.type]}`) || result.url !== `${base}/${result.key}`)
      throw new ImageTransportError(502, "Invalid upload response");
    return { url: result.url, key: result.key, markdown: imageMarkdown(alt, result) };
  } catch (error) {
    if (error instanceof ImageTransportError) throw error;
    throw new ImageTransportError(controller.signal.aborted ? 504 : 502,
      controller.signal.aborted ? "Image upload timed out; check before retrying" : "Image upload failed; check before retrying");
  } finally { clearTimeout(timer); }
}
