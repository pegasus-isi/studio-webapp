"use client";

import { memo, useState } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import { cn } from "@/lib/utils";

/**
 * Markdown renderer for assistant chat messages.
 *
 * Deliberately dependency-light: react-markdown only (already a project dep),
 * no remark/rehype plugins, so no network install and no raw-HTML pass-through
 * (react-markdown escapes HTML by default, which is the safe choice for
 * untrusted model output). Code blocks get a copy button; links open in a new
 * tab with noreferrer.
 */

function CodeBlock({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (non-secure context) — ignore */
    }
  };
  return (
    <div className="group relative my-2">
      <button
        onClick={copy}
        className="absolute right-2 top-2 rounded border border-line bg-surface px-1.5 py-0.5 text-2xs font-medium text-fgmuted opacity-0 transition-opacity hover:text-fg focus-visible:opacity-100 group-hover:opacity-100"
        title="Copy"
      >
        {copied ? "Copied" : "Copy"}
      </button>
      {/* `text-fg` is load-bearing, not decoration.
          Tailwind Typography styles `pre` with `--tw-prose-pre-code`, a
          near-white tuned for its own dark `--tw-prose-pre-bg`. Overriding
          only the background — as this did — left that near-white text on a
          near-white block: #e5e7eb on the old #f3f6ff ground measured 1.15:1 in light mode,
          which is invisible rather than merely low-contrast. `pre code`
          inherits its colour from `pre`, so setting it here is enough.

          The block also needs to read *as* a block. `bg-base` against the
          message's `bg-surface` is 1.06:1, so the fill alone did nothing; the
          border is what separates it. */}
      <pre className="overflow-x-auto rounded-lg border border-line bg-muted p-3 text-xs leading-relaxed text-fg">
        <code>{text}</code>
      </pre>
    </div>
  );
}

const components: Components = {
  code({ className, children, ...props }) {
    // react-markdown v9: inline code has no language class and no newline;
    // block code is wrapped in <pre>. Treat multi-line / language-tagged as a
    // block so the copy button and horizontal scroll apply.
    const text = String(children ?? "");
    const isBlock = /language-/.test(className || "") || text.includes("\n");
    if (isBlock) return <CodeBlock text={text.replace(/\n$/, "")} />;
    return (
      <code
        className="rounded border border-line bg-muted px-1 py-0.5 text-[0.85em] text-fg"
        {...props}
      >
        {children}
      </code>
    );
  },
  // <pre> is handled inside the code renderer; render children bare so we don't
  // double-wrap in another <pre>.
  pre({ children }) {
    return <>{children}</>;
  },
  a({ href, children, ...props }) {
    return (
      <a
        href={href}
        target="_blank"
        rel="noreferrer noopener"
        className="text-pegasus-600 underline hover:text-pegasus-700 dark:text-pegasus-400"
        {...props}
      >
        {children}
      </a>
    );
  },
};

function ChatMarkdownImpl({ content, className }: { content: string; className?: string }) {
  return (
    <div
      className={cn(
        "prose prose-sm max-w-none [overflow-wrap:anywhere] dark:prose-invert",
        className
      )}
    >
      <ReactMarkdown components={components}>{content}</ReactMarkdown>
    </div>
  );
}

// Memoize: during streaming this re-renders on every token; skip re-render when
// the content string is unchanged.
export const ChatMarkdown = memo(ChatMarkdownImpl);
