import { NextRequest, NextResponse } from "next/server";

export const dynamic = "force-dynamic";
const maxBytes = 1024 * 1024;

async function forward(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const { path } = await context.params;
  const route = path.join("/");
  const articleRoute = /^(articles|articles\/(upsert|[0-9a-f-]{36})|tags)$/.test(route);
  const authRoute = /^(auth\/(login|logout|me))$/.test(route);
  const allowed = authRoute || articleRoute || /^(ingest|ingest\/(batch|categories)|capabilities|categories|entries|entries\/(parse|batch|[0-9a-f-]{36}))$/.test(route);
  if (!allowed) return NextResponse.json({ detail: "Not found" }, { status: 404 });
  const authorization = request.headers.get("authorization");
  if (request.method !== "GET") {
    if (!request.headers.get("content-type")?.startsWith("application/json")) {
      return NextResponse.json({ detail: "JSON is required" }, { status: 415 });
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
        ...(authorization ? { Authorization: authorization } : {}),
        ...(session ? { Cookie: `autolog_admin=${session}` } : {}) },
      body: body as BodyInit | undefined,
      cache: "no-store",
      redirect: "error",
      signal: AbortSignal.timeout(75000),
    });
    if (route === "auth/login" && response.ok) {
      const login = await response.json();
      const result = NextResponse.json({ status: "logged_in" }, { headers: { "Cache-Control": "no-store" } });
      result.cookies.set("autolog_admin", login.session, {
        httpOnly: true, secure, sameSite: "strict", path: "/", maxAge: login.max_age,
      });
      return result; // Session is set only as an HttpOnly cookie; never return it in JSON.
    }
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
