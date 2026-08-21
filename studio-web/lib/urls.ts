// Prefix under which KubeSpawner serves the app: /user/<name>. Anchored at the
// start with a lookahead so stripAppBasePath can match "/user/alice/foo"
// without eating the slash. Same regex as middleware.ts.
const APP_PREFIX_RE = /^\/user\/[^/]+(?=\/|$)/;

// Full-match form, for values that must BE a prefix rather than start with
// one: "/user/<name>" plus an optional trailing slash.
const APP_PREFIX_FULL_RE = /^\/user\/[^/]+\/?$/;

// Characters that let a "path" leave the origin once it lands in <base href>:
//   "\"  browsers treat "/\evil.com" as "//evil.com"
//   ":"  a scheme ("https:", "javascript:")
//   whitespace / C0 / DEL  the URL parser strips tab, CR and LF anywhere and
//        spaces at the ends, so "/\t/evil.com" resolves as "//evil.com"
//   "?" "#"  would attach a query/fragment to every relative URL in the page
// "//" is checked separately: it is otherwise valid path syntax, but a
// protocol-relative base ("//evil.com/") is exactly the takeover this guards.
// Caveat: nginx captures the prefix from the decoded URI, so a JupyterHub
// username containing a space would be rejected here and that user would get
// <base href="/">. Relax `\s` to edge-only whitespace if that ever happens.
const UNSAFE_BASE_PATH_CHARS = /[\\:?#\s\u0000-\u001f\u007f]/;

function sanitizeBasePath(value: string): string | null {
  if (UNSAFE_BASE_PATH_CHARS.test(value)) return null;
  if (!value.startsWith("/") || value.includes("//")) return null;
  return value.replace(/\/+$/, "");
}

/**
 * Turn a forwarded prefix into the app's base path ("" or "/user/<name>").
 *
 * Header values (x-forwarded-prefix) are only as trustworthy as the proxy in
 * front: nginx sets the header on every location (docker/nginx/default.conf),
 * but a request reaching Next directly, or through a differently configured
 * proxy, can carry anything — and this value becomes <base href>, i.e. the
 * origin every relative URL in the page resolves against. So a header value
 * must match the one shape KubeSpawner produces; everything else yields ""
 * (app at the origin root), never the raw value. `trusted` is for the
 * operator-set NEXT_PUBLIC_BASE_PATH: it still has to be a clean absolute
 * path, but it may name any prefix.
 */
export function normalizeBasePath(
  value?: string | null,
  opts: { trusted?: boolean } = {}
): string {
  if (!value || value === "/") return "";
  const clean = sanitizeBasePath(value);
  if (clean === null) return "";
  if (opts.trusted) return clean;
  return APP_PREFIX_FULL_RE.test(value) ? clean : "";
}

export function appRelativePath(path: string): string {
  const normalized = path.replace(/^\/+/, "");
  return normalized || ".";
}

export function apiUrl(path: string): string {
  return appRelativePath(`api/${path.replace(/^\/+/, "")}`);
}

export function jupyterUrl(path = "/lab"): string {
  return appRelativePath(`jupyter/${path.replace(/^\/+/, "")}`);
}

export function websocketUrl(path: string): string {
  const relativePath = appRelativePath(`ws/${path.replace(/^\/+/, "")}`);
  // No DOM (SSR): a ws:// URL can't be resolved without an origin, and
  // `new URL(rel, "/")` would throw. Callers only open sockets client-side, so
  // return the relative path as a harmless stub (FE-9 — the old guard checked
  // `document` but still dereferenced `window` right after).
  if (typeof document === "undefined" || typeof window === "undefined") {
    return relativePath;
  }
  const url = new URL(relativePath, document.baseURI);
  url.protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  return url.toString();
}

export function stripAppBasePath(pathname: string | null): string {
  if (!pathname) return "/";

  const match = pathname.match(APP_PREFIX_RE);
  if (!match) return pathname;

  return pathname.slice(match[0].length) || "/";
}
