import { NextRequest, NextResponse } from "next/server";

export async function proxy(request: NextRequest) {
  const path = request.nextUrl.pathname;
  const protectedPage = path === "/admin" || path.startsWith("/admin/") || path === "/legacy" ||
    path === "/articles/new" || /^\/articles\/[^/]+\/edit$/.test(path);
  if (!protectedPage) return NextResponse.next();
  const session = request.cookies.get("autolog_admin")?.value;
  try {
    if (session && process.env.BACKEND_URL) {
      const response = await fetch(`${process.env.BACKEND_URL.replace(/\/$/, "")}/api/auth/me`, {
        headers: { Cookie: `autolog_admin=${session}` }, cache: "no-store", signal: AbortSignal.timeout(5000),
      });
      if (response.ok) return NextResponse.next();
    }
  } catch { /* Fail closed if the session cannot be validated. */ }
  return NextResponse.redirect(new URL("/login", request.url));
}

export const config = { matcher: ["/admin/:path*", "/legacy", "/articles/new", "/articles/:id/edit"] };
