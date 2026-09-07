import { headers } from "next/headers";
import { normalizeBasePath } from "@/lib/urls";

/**
 * The per-user prefix the current request arrived under, server-side.
 *
 * Behind KubeSpawner the app is served at `/user/<name>/…`; `middleware.ts`
 * strips that prefix before Next routes the request and records it in
 * `x-forwarded-prefix`. Anything a server component emits as an *absolute*
 * path — a `<base href>`, a `redirect()` Location header — has to put the
 * prefix back, or the browser leaves the user's pod entirely: `/welcome`
 * resolves to `studio.pegasus-ai.org/welcome`, which JupyterHub forwards to
 * `/hub/welcome` and answers with a 404 (#368). Returns "" in dev, where there
 * is no prefix.
 *
 * Server-only (it reads request headers); client code derives the same value
 * from `document.baseURI` via `lib/urls.ts`.
 */
export async function requestBasePath(): Promise<string> {
  const requestHeaders = await headers();
  return normalizeBasePath(
    requestHeaders.get("x-forwarded-prefix") ||
      requestHeaders.get("x-script-name") ||
      process.env.NEXT_PUBLIC_BASE_PATH
  );
}
