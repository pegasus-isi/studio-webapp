import { NextRequest, NextResponse } from "next/server";

export async function POST(req: NextRequest) {
  const body = await req.json().catch(() => ({})) as { cookie?: unknown };
  const cookie = body.cookie;
  if (!cookie || typeof cookie !== "string") {
    return NextResponse.json({ error: "cookie required" }, { status: 400 });
  }
  const res = NextResponse.json({ ok: true });
  res.cookies.set("hub_session", cookie.trim(), {
    httpOnly: true,
    sameSite: "lax",
    path: "/",
    maxAge: 60 * 60 * 8, // 8 hours
  });
  return res;
}

export async function DELETE() {
  const res = NextResponse.json({ ok: true });
  res.cookies.delete("hub_session");
  return res;
}
