import { randomUUID } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
export const runtime = "nodejs";
const jsonMaxBytes = 1024 * 1024;

class ProxyError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function readBody(request: NextRequest, maxBytes: number, signal: AbortSignal): Promise<ArrayBuffer | undefined> {
  const length = request.headers.get("content-length");
  if (length && (!/^\d+$/.test(length) || !Number.isSafeInteger(Number(length)))) throw new ProxyError(400, "Invalid Content-Length");
  if (length && Number(length) > maxBytes) throw new ProxyError(413, "Request is too large");
  if (!request.body) return undefined;
  let size = 0;
  const limited = new TransformStream<Uint8Array, Uint8Array>({
    transform(chunk, controller) {
      size += chunk.byteLength;
      if (size > maxBytes) throw new ProxyError(413, "Request is too large");
      controller.enqueue(chunk);
    },
  });
  // Keep original multipart bytes and boundary; undici computes the outgoing length.
  // The stream limiter also covers chunked requests without a Content-Length.
  return new Response(request.body.pipeThrough(limited, { signal })).arrayBuffer();
}

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const requestId = randomUUID();
  let route = "unknown";
  let stage = "request";
  const json = (detail: string, status: number) => NextResponse.json({ detail, request_id: requestId }, {
    status, headers: { "Cache-Control": "no-store", "X-Request-ID": requestId },
  });
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 75000);
  try {
    const { path } = await context.params;
    route = path.join("/");
    const articleRoute = /^(articles|articles\/(upsert|[0-9a-f-]{36})|tags)$/.test(route);
    const uploadRoute = route === "uploads";
    const maxBytes = uploadRoute ? 10 * 1024 * 1024 + 64 * 1024 : jsonMaxBytes;
    const contentType = request.headers.get("content-type") || "";
    const authRoute = /^(auth\/(login|logout|me))$/.test(route);
    const allowed = uploadRoute || authRoute || articleRoute || /^(ingest|ingest\/(batch|categories)|capabilities|categories|entries|entries\/(parse|batch|[0-9a-f-]{36}))$/.test(route);
    if (!allowed) return NextResponse.json({ detail: "Not found" }, { status: 404 });
    const authorization = request.headers.get("authorization");
    if (request.method !== "GET") {
      if (uploadRoute ? !/^multipart\/form-data;.*boundary=/i.test(contentType) : !contentType.startsWith("application/json")) {
        return NextResponse.json({ detail: uploadRoute ? "Multipart form-data is required" : "JSON is required" }, { status: 415 });
      }
      // Cookie login/write/logout requests must originate from this site. Bearer clients do not use cookies.
      const origin = request.headers.get("origin");
      if (origin && (!authorization || authRoute)) {
        try {
          if (new URL(origin).host !== request.headers.get("host")) throw new Error("origin mismatch");
        } catch {
          return NextResponse.json({ detail: "Cross-origin writes are not allowed" }, { status: 403 });
        }
      }
    }
    const secure = request.nextUrl.protocol === "https:" || request.headers.get("x-forwarded-proto") === "https";
    if (route === "auth/logout") {
      if (request.method !== "POST") return NextResponse.json({ detail: "Method not allowed" }, { status: 405 });
      const response = NextResponse.json({ status: "logged_out" }, { headers: { "Cache-Control": "no-store" } });
      response.cookies.set("autolog_admin", "", { httpOnly: true, secure, sameSite: "strict", path: "/", maxAge: 0 });
      return response;
    }
    const backend = process.env.BACKEND_URL;
    if (!backend) return NextResponse.json({ detail: "Backend is not configured" }, { status: 503 });
    const session = request.cookies.get("autolog_admin")?.value;
    if (uploadRoute && !authorization && !session) return NextResponse.json({ detail: "Authentication required" }, { status: 401 });
    if (uploadRoute) console.info(JSON.stringify({ event: "upload_proxy_received", request_id: requestId }));
    stage = "request_body";
    const body = request.method === "GET" ? undefined : await readBody(request, maxBytes, controller.signal);
    if (uploadRoute) console.info(JSON.stringify({ event: "upload_proxy_forwarding", request_id: requestId, bytes: body?.byteLength || 0 }));
    stage = "backend_fetch";
    const response = await fetch(`${backend.replace(/\/$/, "")}/api/${route}${request.nextUrl.search}`, {
      method: request.method,
      headers: { "Content-Type": uploadRoute ? contentType : "application/json", Accept: "application/json",
        ...(authorization ? { Authorization: authorization } : {}),
        ...(session ? { Cookie: `autolog_admin=${session}` } : {}), "X-Request-ID": requestId },
      body,
      cache: "no-store",
      redirect: "error",
      signal: controller.signal,
    });
    stage = "backend_response";
    let data: unknown = null;
    if (response.status !== 204) {
      const text = await response.text();
      try { data = JSON.parse(text); }
      catch { throw new ProxyError(502, "Backend returned a non-JSON response"); }
    }
    if (route === "auth/login" && response.ok) {
      const login = data as { session: string; max_age: number };
      if (!login || typeof login.session !== "string" || typeof login.max_age !== "number") throw new ProxyError(502, "Invalid login response");
      const result = NextResponse.json({ status: "logged_in" }, { headers: { "Cache-Control": "no-store" } });
      result.cookies.set("autolog_admin", login.session, {
        httpOnly: true, secure, sameSite: "strict", path: "/", maxAge: login.max_age,
      });
      return result; // Session is set only as an HttpOnly cookie; never return it in JSON.
    }
    return new NextResponse(response.status === 204 ? null : JSON.stringify(data), {
      status: response.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store", "X-Request-ID": requestId,
        ...(response.headers.get("www-authenticate") ? { "WWW-Authenticate": response.headers.get("www-authenticate")! } : {}) },
    });
  } catch (error) {
    // Log stages/IDs only, never cookies, credentials, payloads or raw fetch errors.
    console.warn(JSON.stringify({ event: "api_proxy_failure", request_id: requestId, stage }));
    if (error instanceof ProxyError) return json(error.message, error.status);
    if (controller.signal.aborted) return json(stage === "request_body" ? "Upload request timed out" : "Backend request timed out", stage === "request_body" ? 408 : 504);
    return json(stage === "request_body" ? "Could not read request body" : "Backend is unavailable", stage === "request_body" ? 400 : 502);
  } finally {
    clearTimeout(timer);
  }
}

export { forward as GET, forward as POST, forward as PATCH, forward as DELETE };
