import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
const maxBytes = 1024 * 1024;

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const route = path.join("/");
  const articleRoute = /^(articles|articles\/(upsert|[0-9a-f-]{36})|tags)$/.test(route);
  const external = /^(ingest|ingest\/(batch|categories))$/.test(route) ||
    ((articleRoute || route === "categories") && !/^Basic /i.test(request.headers.get("authorization") || ""));
  const allowed = articleRoute || /^(ingest|ingest\/(batch|categories)|capabilities|categories|entries|entries\/(parse|batch|[0-9a-f-]{36}))$/.test(route);
  if (!allowed) return NextResponse.json({ detail: "Not found" }, { status: 404 });
  if (request.method !== "GET") {
    if (!request.headers.get("content-type")?.startsWith("application/json")) {
      return NextResponse.json({ detail: "JSON is required" }, { status: 415 });
    }
    const origin = request.headers.get("origin");
    if (origin && !external) {
      try {
        if (new URL(origin).host !== request.headers.get("host")) throw new Error("origin mismatch");
      } catch {
        return NextResponse.json({ detail: "Cross-origin writes are not allowed" }, { status: 403 });
      }
    }
  }
  const backend = process.env.BACKEND_URL;
  if (!backend) return NextResponse.json({ detail: "Backend is not configured" }, { status: 503 });
  const authorization = external ? request.headers.get("authorization") :
    (process.env.API_TOKEN ? `Bearer ${process.env.API_TOKEN}` : null);
  if (!external && !authorization) return NextResponse.json({ detail: "API authentication is not configured" }, { status: 503 });
  let body: Uint8Array | undefined;
  if (request.body && request.method !== "GET") {
    const reader = request.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBytes) {
        await reader.cancel();
        return NextResponse.json({ detail: "Request is too large" }, { status: 413 });
      }
      chunks.push(value);
    }
    body = new Uint8Array(size);
    let position = 0;
    for (const chunk of chunks) { body.set(chunk, position); position += chunk.byteLength; }
  }
  try {
    const response = await fetch(`${backend.replace(/\/$/, "")}/api/${route}${request.nextUrl.search}`, {
      method: request.method,
      headers: { "Content-Type": "application/json", Accept: "application/json",
        ...(authorization ? { Authorization: authorization } : {}) },
      body: body as BodyInit | undefined,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(75000),
    });
    return new NextResponse(response.status === 204 ? null : await response.text(), {
      status: response.status,
      headers: { "Content-Type": "application/json", "Cache-Control": "no-store",
        ...(response.headers.get("www-authenticate") ? { "WWW-Authenticate": response.headers.get("www-authenticate")! } : {}) },
    });
  } catch {
    return NextResponse.json({ detail: "Backend is unavailable" }, { status: 502 });
  }
}

export { forward as GET, forward as POST, forward as PATCH, forward as DELETE };
