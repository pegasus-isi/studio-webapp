import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

const APP_PREFIX_RE = /^\/user\/[^/]+(?=\/|$)/;

// When HUB_COOKIE is set (local dev pointing at a remote JupyterHub-hosted
// studio), forward it so the hub proxy authenticates the request.
// Set in .env.local as: HUB_COOKIE=jupyterhub-session-id=<value>
const HUB_COOKIE = process.env.HUB_COOKIE;

export function middleware(request: NextRequest) {
  const prefix = request.nextUrl.pathname.match(APP_PREFIX_RE)?.[0];

  if (prefix) {
    // KubeSpawner path — strip /user/<name> prefix and forward x-forwarded-prefix
    if (request.nextUrl.pathname === prefix) {
      const redirectUrl = request.nextUrl.clone();
      redirectUrl.pathname = `${prefix}/`;
      return NextResponse.redirect(redirectUrl, 308);
    }

    const rewrittenUrl = request.nextUrl.clone();
    rewrittenUrl.pathname = request.nextUrl.pathname.slice(prefix.length) || "/";

    const requestHeaders = new Headers(request.headers);
    requestHeaders.set("x-forwarded-prefix", prefix);
    if (HUB_COOKIE) requestHeaders.set("Cookie", HUB_COOKIE);

    return NextResponse.rewrite(rewrittenUrl, { request: { headers: requestHeaders } });
  }

  // Local dev against a remote hub: inject the cookie for proxied API/WS/Jupyter
  // calls. Env var only — there is no in-app login (auth is upstream, in
  // studio-system), so this is inert in the image and only ever set in .env.local.
  if (HUB_COOKIE && (
    request.nextUrl.pathname.startsWith("/api/") ||
    request.nextUrl.pathname.startsWith("/ws/") ||
    request.nextUrl.pathname.startsWith("/jupyter/")
  )) {
    const requestHeaders = new Headers(request.headers);
    requestHeaders.set("Cookie", HUB_COOKIE);
    return NextResponse.next({ request: { headers: requestHeaders } });
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/user/:path*", "/api/:path*", "/ws/:path*", "/jupyter/:path*"],
};
