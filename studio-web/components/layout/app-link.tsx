"use client";

/**
 * An in-app link that navigates without reloading the page (#286).
 *
 * Every in-app link used to be a plain `<a href>`, which meant a full page
 * load on every click. That was deliberate — Next's `basePath` is build-time
 * only and Studio runs under a dynamic `/user/<name>` prefix, so `<Link>` with
 * an app-absolute path would drop the prefix — but it threw away everything
 * held in memory each time: the chat panel remounted, terminals were torn
 * down, scroll positions reset.
 *
 * The prefix is recoverable at runtime from `<base href>` (see `appPushPath`),
 * so the path can be reconstructed and pushed through the client router. The
 * middleware rewrite that already serves `/user/<name>/x` as `/x` handles the
 * RSC request the same way it handles the document request.
 *
 * The element stays an `<a>` with a real `href`. That is not decoration: it is
 * what keeps middle-click, cmd/ctrl-click, "open in new tab" and "copy link
 * address" working, and what makes this degrade to the old behaviour if the
 * click handler ever fails to run.
 */

import type { ComponentProps } from "react";
import { useRouter } from "next/navigation";
import { appPushPath, appRelativePath } from "@/lib/urls";

type Props = Omit<ComponentProps<"a">, "href"> & {
  /** App-absolute path, e.g. "/workflows" or "/workflows?run=abc". */
  href: string;
};

/**
 * How long to wait for a client-side push to take effect before giving up and
 * loading the page normally.
 *
 * A navigation that silently does nothing is the worst outcome available — the
 * user is simply stuck, with no error and nothing to click. Falling back costs
 * a page load, which is exactly what this component was built to avoid, but a
 * slow correct navigation beats a fast broken one.
 */
const PUSH_TIMEOUT_MS = 1200;

export function AppLink({ href, onClick, ...rest }: Props) {
  const router = useRouter();

  return (
    <a
      href={appRelativePath(href)}
      onClick={(e) => {
        onClick?.(e);
        // Anything that isn't a plain left click belongs to the browser:
        // modified clicks open new tabs, and taking those over would be a
        // regression dressed as a feature.
        if (
          e.defaultPrevented ||
          e.button !== 0 ||
          e.metaKey ||
          e.ctrlKey ||
          e.shiftKey ||
          e.altKey
        ) {
          return;
        }

        e.preventDefault();
        const target = appPushPath(href);
        router.push(target);

        // Watchdog. If the URL has not moved by now the push did not take, and
        // leaving the user on a page that ignored their click is not an option.
        window.setTimeout(() => {
          // Compare without a trailing slash: Next normalises it away on push
          // (trailingSlash defaults to false), so a literal comparison would
          // report a successful navigation as failed and reload the page.
          const trim = (p: string) => p.replace(/\/+$/, "") || "/";
          if (trim(window.location.pathname) !== trim(target.split(/[?#]/)[0])) {
            window.location.assign(target);
          }
        }, PUSH_TIMEOUT_MS);
      }}
      {...rest}
    />
  );
}
