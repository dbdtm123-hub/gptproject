export const dynamic = "force-dynamic";
export async function GET() {
  if (!process.env.BACKEND_URL) {
    return Response.json({ status: "not_ready" }, { status: 503 });
  }
  try {
    const signal = AbortSignal.timeout(2500);
    const response = await fetch(`${process.env.BACKEND_URL}/health/ready`, {
      cache: "no-store", signal,
    });
    if (!response.ok) throw new Error("not ready");
    return Response.json({ status: "ready" });
  } catch { return Response.json({ status: "not_ready" }, { status: 503 }); }
}
