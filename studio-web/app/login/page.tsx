"use client";

import { useState } from "react";
import { PegasusLogo } from "@/components/ui/pegasus-logo";

export default function LoginPage() {
  const [cookie, setCookie] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    const value = cookie.trim();
    if (!value) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch("/api/auth", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ cookie: value }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error((d as { error?: string }).error || `Error ${res.status}`);
      }
      window.location.href = "/dashboard";
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save session");
      setLoading(false);
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-base p-6">
      <div className="w-full max-w-md space-y-6">

        <div className="flex flex-col items-center gap-3 text-center">
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-accent/10">
            <PegasusLogo size={32} />
          </div>
          <h1 className="text-xl font-bold text-fg">Connect to Studio</h1>
          <p className="text-sm text-fgmuted">
            Paste your JupyterHub session cookie to connect to the remote backend.
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4 rounded-2xl border border-line bg-surface p-6">
          <div className="space-y-1.5">
            <label className="text-xs font-semibold text-fgmuted">Session Cookie</label>
            <textarea
              rows={3}
              placeholder="jupyterhub-session-id=abc123..."
              value={cookie}
              onChange={(e) => setCookie(e.target.value)}
              className="w-full resize-none rounded-xl border border-line bg-base px-3 py-2.5 font-mono text-xs text-fg placeholder:text-fgsubtle focus:border-accent focus:outline-none focus:ring-1 focus:ring-accent/30"
            />
            <p className="text-[10px] text-fgsubtle">
              DevTools → Application → Cookies → <code className="font-mono">jupyterhub-session-id</code>
              {" "}— paste the full <code className="font-mono">name=value</code> string.
            </p>
          </div>

          {error && (
            <p className="rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-600">
              {error}
            </p>
          )}

          <button
            type="submit"
            disabled={!cookie.trim() || loading}
            className="w-full rounded-xl bg-accent px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-accent/90 disabled:opacity-50"
          >
            {loading ? "Starting…" : "Start Studio"}
          </button>
        </form>

      </div>
    </div>
  );
}
