import { createHash, timingSafeEqual } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";

export function proxy(request: NextRequest) {
  // External ingest is authenticated by the backend Bearer dependency.
  // Do not bypass Basic authentication for any other application route.
  if (["/api/ingest", "/api/ingest/batch", "/api/ingest/categories", "/openapi-action.json"].includes(request.nextUrl.pathname)) {
    return NextResponse.next();
  }
  if (request.nextUrl.pathname === "/api/categories" && /^Bearer /i.test(request.headers.get("authorization") || "")) {
    return NextResponse.next();
  }
  const articleApi = /^\/api\/articles(?:\/.*)?$/.test(request.nextUrl.pathname) || request.nextUrl.pathname === "/api/tags";
  if (articleApi && !/^Basic /i.test(request.headers.get("authorization") || "")) {
    return NextResponse.next(); // Backend authenticates external Article clients, including missing/invalid tokens.
  }
  const username = process.env.APP_USERNAME;
  const password = process.env.APP_PASSWORD;
  if (!username || !password) {
    return new NextResponse("Login is not configured", { status: 503 });
  }
  const authorization = request.headers.get("authorization") || "";
  if (authorization.startsWith("Basic ") && authorization.length < 4096) {
    const supplied = Buffer.from(authorization.slice(6), "base64").toString("utf8");
    const hash = (value: string) => createHash("sha256").update(value).digest();
    if (timingSafeEqual(hash(supplied), hash(`${username}:${password}`))) {
      return NextResponse.next();
    }
  }
  return new NextResponse("Authentication required", {
    status: 401,
    headers: { "WWW-Authenticate": 'Basic realm="Autolog", charset="UTF-8"', "Cache-Control": "no-store" },
  });
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|health/live|health/ready|favicon.ico).*)"],
};
