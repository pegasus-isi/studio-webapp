import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

const APP_PREFIX_RE = /^\/user\/[^/]+(?=\/|$)/;

export function middleware(request: NextRequest) {
  const prefix = request.nextUrl.pathname.match(APP_PREFIX_RE)?.[0];

  if (!prefix) {
    return NextResponse.next();
  }

  if (request.nextUrl.pathname === prefix) {
    const redirectUrl = request.nextUrl.clone();
    redirectUrl.pathname = `${prefix}/`;
    return NextResponse.redirect(redirectUrl, 308);
  }

  const rewrittenUrl = request.nextUrl.clone();
  rewrittenUrl.pathname = request.nextUrl.pathname.slice(prefix.length) || "/";

  const requestHeaders = new Headers(request.headers);
  requestHeaders.set("x-forwarded-prefix", prefix);

  return NextResponse.rewrite(rewrittenUrl, {
    request: {
      headers: requestHeaders,
    },
  });
}

export const config = {
  matcher: ["/user/:path*"],
};
