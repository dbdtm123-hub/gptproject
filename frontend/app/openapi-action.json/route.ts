export const dynamic = "force-dynamic";

export async function GET() {
  const backend = process.env.BACKEND_URL;
  if (!backend) return Response.json({ detail: "Backend is not configured" }, { status: 503 });
  try {
    const response = await fetch(`${backend.replace(/\/$/, "")}/openapi-action.json`, {
      cache: "no-store", redirect: "error", signal: AbortSignal.timeout(5000),
    });
    if (!response.ok) throw new Error("schema unavailable");
    return Response.json(await response.json(), { headers: { "Cache-Control": "no-store" } });
  } catch { return Response.json({ detail: "Schema is unavailable" }, { status: 502 }); }
}
