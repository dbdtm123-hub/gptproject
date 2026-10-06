export const dynamic = "force-dynamic";
export async function GET() {
  if (!process.env.APP_USERNAME || !process.env.APP_PASSWORD || !process.env.BACKEND_URL || !process.env.API_TOKEN) {
    return Response.json({ status: "not_ready" }, { status: 503 });
  }
  try {
    const signal = AbortSignal.timeout(2500);
    const response = await fetch(`${process.env.BACKEND_URL}/health/ready`, {
      cache: "no-store", signal,
    });
    if (!response.ok) throw new Error("not ready");
    const authenticated = await fetch(`${process.env.BACKEND_URL}/api/capabilities`, {
      headers: { Authorization: `Bearer ${process.env.API_TOKEN}` },
      cache: "no-store", signal,
    });
    if (!authenticated.ok) throw new Error("token mismatch");
    return Response.json({ status: "ready" });
  } catch { return Response.json({ status: "not_ready" }, { status: 503 }); }
}
