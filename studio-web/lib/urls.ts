const APP_PREFIX_RE = /^\/user\/[^/]+(?=\/|$)/;

export function normalizeBasePath(value?: string | null): string {
  if (!value || value === "/") return "";

  const withLeadingSlash = value.startsWith("/") ? value : `/${value}`;
  return withLeadingSlash.replace(/\/+$/, "");
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
