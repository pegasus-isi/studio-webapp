import type { NextRequest } from "next/server";
import { NextResponse } from "next/server";

const APP_PREFIX_RE = /^\/user\/[^/]+(?=\/|$)/;

export function middleware(request: NextRequest) {
  const prefix = request.nextUrl.pathname.match(APP_PREFIX_RE)?.[0];

  if (!prefix) {
    return NextResponse.next();
  }

  // Both `/user/<name>` and `/user/<name>/` rewrite to `/`; the slice below
  // already yields "/" for either.
  //
  // The bare form used to be answered with a 308 to the trailing-slash form,
  // which broke client-side navigation *to Home only* (#288). `appPushPath("/")`
  // produces `/user/<name>/`, Next normalises the trailing slash away
  // (trailingSlash defaults to false), and the resulting `/user/<name>` hit
  // this redirect — on an RSC navigation request, which the client router does
  // not follow the way it follows a document redirect. Every other route was
  // unaffected because none of them can equal the prefix exactly.
  //
  // The redirect was only ever normalising for the sake of relative URL
  // resolution, and that is already handled explicitly: the root layout emits
  // `<base href="/user/<name>/">`, so relative hrefs resolve identically
  // whether or not the address bar has the trailing slash.
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
