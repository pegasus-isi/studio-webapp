"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import Fuse from "fuse.js";
import { useChatStore, genId } from "@/lib/stores/chat-store";
import { useConsumeChatStream } from "@/lib/hooks/use-chat-stream";
import { usePanelStore } from "@/lib/stores/panel-store";
import { useChatContextStore } from "@/lib/stores/chat-context-store";
import { ChatContextChip } from "@/components/chat/context-chip";
import {
  useLLMConfig,
  useProviderConfigs,
  useProviders,
  useProviderModels,
} from "@/lib/hooks/use-llm";
import * as api from "@/lib/api/client";
import { apiUrl } from "@/lib/urls";
import { chatFeatures } from "@/lib/chat-features";

export function ChatInput() {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionResults, setMentionResults] = useState<{ path: string; type: string }[]>([]);
  const [mentionIndex, setMentionIndex] = useState(0);
  const [mentionCache, setMentionCache] = useState<Map<string, { type: string; snippet: string }>>(
    () => new Map()
  );
  const isStreaming = useChatStore((s) => s.isStreaming);
  const activeConvId = useChatStore((s) => s.activeId);
  const scopeFromStore = useChatContextStore(
    (s) => s.scopeByConv[activeConvId] ?? null
  );
  // Scope comes from localStorage-persisted state, which the server render
  // can't see — show the neutral placeholder until mounted (same hydration
  // guard the conversation bar uses).
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const activeScope = mounted ? scopeFromStore : null;
  const addMessage = useChatStore((s) => s.addMessage);
  const appendToLast = useChatStore((s) => s.appendToLast);
  const addToolCall = useChatStore((s) => s.addToolCall);
  const addToolResult = useChatStore((s) => s.addToolResult);
  const addPermissionRequest = useChatStore((s) => s.addPermissionRequest);
  const clearPermissionRequest = useChatStore((s) => s.clearPermissionRequest);
  const setLastMeta = useChatStore((s) => s.setLastMeta);
  const setLastServerId = useChatStore((s) => s.setLastServerId);
  const setLastError = useChatStore((s) => s.setLastError);
  const setStreaming = useChatStore((s) => s.setStreaming);
  const consumeStream = useConsumeChatStream();
  const messages = useChatStore((s) => s.messages);
  const agentId = useChatStore((s) => s.agentId);

  // Provider/model overrides from store
  const chatProvider = useChatStore((s) => s.provider);
  const chatModel = useChatStore((s) => s.model);
  const setChatProvider = useChatStore((s) => s.setProvider);
  const setChatModel = useChatStore((s) => s.setModel);

  // Load saved configs for defaults
  const { data: config } = useLLMConfig();
  const { data: providerData } = useProviders();
  const { data: configData } = useProviderConfigs();
  const presetProviders = providerData?.providers ?? [];
  // Use saved provider configs if available, fall back to presets
  const savedConfigs = configData?.configs ?? [];
  const providers = savedConfigs.length > 0
    ? savedConfigs.map((c) => ({
        id: c.provider_id,
        name: c.name,
        default_model: c.default_model,
        base_url: c.base_url,
        api_key_env: null as string | null,
      }))
    : presetProviders;

  // Discover the selected provider's models so the model field offers a real
  // choice — resolved server-side against the provider's saved key/base URL
  // (keys never reach the browser). Empty on the Default provider or when
  // discovery fails (the field then just accepts free text).
  const { data: modelsData } = useProviderModels(chatProvider);
  const providerDefaultModel = providers.find(
    (p) => p.id === chatProvider
  )?.default_model;
  const modelOptions = Array.from(
    new Set(
      [
        chatModel ?? "",
        providerDefaultModel ?? "",
        ...(modelsData?.models ?? []),
      ].filter(Boolean)
    )
  );

  // Initialize from saved config on first load
  const [initialized, setInitialized] = useState(false);
  useEffect(() => {
    if (!initialized && config) {
      if (!chatProvider) setChatProvider(config.provider || null);
      if (!chatModel) setChatModel(config.model || null);
      setInitialized(true);
    }
  }, [config, initialized, chatProvider, chatModel, setChatProvider, setChatModel]);

  // @ mention handling — fetch search results when query changes
  useEffect(() => {
    if (mentionQuery === null) {
      setMentionResults([]);
      return;
    }
    let cancelled = false;
    const q = mentionQuery;
    fetch(apiUrl(`/files/search?q=${encodeURIComponent(q)}`))
      .then((r) => r.json())
      .then((data: { path: string; type: string }[]) => {
        if (cancelled) return;
        if (q && data.length > 0) {
          const fuse = new Fuse(data, { keys: ["path"], threshold: 0.4 });
          const ranked = fuse.search(q).map((r) => r.item);
          const seen = new Set(ranked.map((r) => r.path));
          const rest = data.filter((d) => !seen.has(d.path));
          setMentionResults([...ranked, ...rest].slice(0, 20));
        } else {
          setMentionResults(data.slice(0, 20));
        }
        setMentionIndex(0);
      })
      .catch(() => {
        if (!cancelled) setMentionResults([]);
      });
    return () => {
      cancelled = true;
    };
  }, [mentionQuery]);

  const updateMentionQuery = useCallback((value: string, cursorPos: number) => {
    const before = value.slice(0, cursorPos);
    const m = before.match(/@([^\s]*)$/);
    if (m) setMentionQuery(m[1]);
    else setMentionQuery(null);
  }, []);

  const insertMention = useCallback(
    async (item: { path: string; type: string }) => {
      const el = textareaRef.current;
      const cursor = el?.selectionStart ?? input.length;
      const before = input.slice(0, cursor);
      const after = input.slice(cursor);
      const m = before.match(/@([^\s]*)$/);
      const start = m ? before.length - m[0].length : before.length;
      const atText = `@${item.path} `;
      const next = before.slice(0, start) + atText + after;
      setInput(next);
      setMentionQuery(null);
      setMentionResults([]);
      // fetch snippet for cache (visible @path stays lean, snippet attached invisibly at send)
      let snippet = "";
      try {
        if (item.type === "dir") {
          const res = await fetch(apiUrl(`/files?path=${encodeURIComponent(item.path)}`));
          const data = await res.json();
          const entries: { name: string; type: string }[] = data.entries ?? [];
          snippet = entries.slice(0, 30).map((e) => `${e.type}\t${e.name}`).join("\n");
          if (entries.length > 30) snippet += `\n[... ${entries.length - 30} more entries ...]`;
        } else {
          const res = await fetch(apiUrl(`/files/read?path=${encodeURIComponent(item.path)}`));
          const data = await res.json();
          const content: string = data.content ?? "";
          snippet = content.slice(0, 4000);
          if (content.length > 4000) snippet += "\n[... truncated, 4000 chars shown ...]";
        }
      } catch {}
      setMentionCache((prev) => {
        const nextMap = new Map(prev);
        nextMap.set(item.path, { type: item.type, snippet });
        return nextMap;
      });
      requestAnimationFrame(() => {
        if (el) {
          const pos = start + atText.length;
          el.focus();
          el.selectionStart = el.selectionEnd = pos;
          updateMentionQuery(next, pos);
        }
      });
    },
    [input, updateMentionQuery]
  );

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || isStreaming) return;

    // derive file context from final @ tokens (correct on delete, deduped)
    const mentionPaths = [...text.matchAll(/(?<![\w`])@([^\s]+)/g)].map((m) =>
      m[1].trim().replace(/[,.;:)]$/, "")
    );
    const deduped = [...new Set(mentionPaths)];
    const attachmentsBlock = deduped
      .filter((p) => mentionCache.has(p))
      .map((p) => {
        const cached = mentionCache.get(p)!;
        return `\n\n--- @${p}\n\`\`\`${p}\n${cached.snippet}\n\`\`\``;
      })
      .join("");
    const displayText = text;
    const apiText = text + attachmentsBlock;

    setInput("");
    // prune cache to only paths still mentioned (next turn lean)
    setMentionCache((prev) => {
      const next = new Map<string, { type: string; snippet: string }>();
      for (const p of deduped) if (prev.has(p)) next.set(p, prev.get(p)!);
      return next;
    });

    // Pin the conversation this turn belongs to, so streamed tokens keep
    // landing here even if the user switches conversations mid-response.
    const convId = useChatStore.getState().activeId;

    // Add user message to store — display lean @path only
    const userMsg = {
      id: genId(),
      role: "user" as const,
      content: displayText,
    };
    addMessage(userMsg);

    // Build message history for the API. Skip empty-content messages (e.g. an
    // assistant bubble that only carried a transient error) and the local
    // context-switch dividers (system role, display-only) so neither becomes
    // part of the model-visible transcript.
    // Note: file/dir context is now provided via @ mentions with inline
    // snippets (opencode parity), not via the notebook checkbox.
    const apiMessages = [
      ...messages
        .filter((m) => m.role !== "system" && m.content.trim().length > 0)
        .map((m) => ({ role: m.role, content: m.content })),
      { role: "user", content: apiText },
    ];

    const requestId = `req-${genId()}`;
    setStreaming(true, requestId);

    const controller = new AbortController();
    abortRef.current = controller;

    try {
      const body: Record<string, unknown> = {
        messages: apiMessages,
        display_text: displayText,
        agent: agentId,
        request_id: requestId,
        conversation_id: convId,
      };

      // Workflow/run scope from the context chip. Only {type, id} goes over
      // the wire — the backend resolves it fresh (status, diagnosis) per turn.
      const scope = useChatContextStore.getState().scopeByConv[convId] ?? null;
      if (scope) {
        body.context = { type: scope.type, id: scope.id };
      }

      // Include provider/model overrides if set. With the Default provider,
      // send neither so the server uses the configured default model.
      // Bot mode hides the selectors: never leak stale persisted store
      // values as overrides for a hidden control.
      if (chatFeatures.showProviderSelect && chatProvider) {
        body.provider = chatProvider;
        if (chatFeatures.showModelSelect && chatModel) body.model = chatModel;
      }

      const response = await fetch(apiUrl("/chat/stream"), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        signal: controller.signal,
      });

      if (!response.ok) {
        // A 500 ({"detail":…}) or a 502/504 HTML page from nginx/Envoy still
        // has a body, so without this check the SSE reader consumes it,
        // appends nothing, and the spinner clears — chat looks silently
        // broken with the actual cause discarded.
        let detail = "";
        try {
          const raw = await response.text();
          try {
            detail = String(JSON.parse(raw)?.detail ?? raw);
          } catch {
            detail = raw; // not JSON — likely a gateway HTML error page
          }
        } catch {
          // body unreadable — fall through to the bare status line
        }
        detail = detail.replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
        setLastError(
          `HTTP ${response.status}${detail ? `: ${detail.slice(0, 300)}` : ""}`,
          convId
        );
        return;
      }

      if (!response.body) {
        setLastError("No response body", convId);
        return;
      }

      await consumeStream(response.body, convId);

    } catch (err) {
      // A user-initiated stop aborts the fetch — that's not an error to show.
      if ((err as Error).name !== "AbortError") {
        setLastError(`Connection error: ${(err as Error).message}`, convId);
      }
    } finally {
      abortRef.current = null;
      setStreaming(false);
    }
  }, [
    input,
    isStreaming,
    messages,
    agentId,
    chatProvider,
    chatModel,
    mentionCache,
    addMessage,
    setLastError,
    consumeStream,
    setStreaming,
  ]);

  // Abort any in-flight stream if the component unmounts mid-response.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Consume a one-shot draft (e.g. a "/scaffold " prefix from a dashboard
  // capability card): drop it into the input, focus, and clear the draft.
  const chatDraft = usePanelStore((s) => s.chatDraft);
  const setChatDraft = usePanelStore((s) => s.setChatDraft);
  useEffect(() => {
    if (chatDraft == null) return;
    setInput(chatDraft);
    setChatDraft(null);
    const el = textareaRef.current;
    if (el) {
      el.focus();
      // Cursor at end so the user can keep typing after the command.
      requestAnimationFrame(() => {
        el.selectionStart = el.selectionEnd = el.value.length;
      });
    }
  }, [chatDraft, setChatDraft]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    // @ mention dropdown navigation
    if (mentionQuery !== null && mentionResults.length > 0) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setMentionIndex((i) => (i + 1) % mentionResults.length);
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setMentionIndex((i) => (i - 1 + mentionResults.length) % mentionResults.length);
        return;
      }
      if (e.key === "Enter" && !e.shiftKey) {
        // if dropdown open, select instead of sending
        e.preventDefault();
        const item = mentionResults[mentionIndex];
        if (item) void insertMention(item);
        return;
      }
      if (e.key === "Tab") {
        e.preventDefault();
        const item = mentionResults[mentionIndex];
        if (item) void insertMention(item);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        setMentionQuery(null);
        return;
      }
    }
    // Don't send while an IME composition is active (CJK input commits with
    // Enter) — nativeEvent.isComposing is the reliable signal.
    if (
      e.key === "Enter" &&
      !e.shiftKey &&
      !(e.nativeEvent as { isComposing?: boolean }).isComposing
    ) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleStop = () => {
    // Abort the client-side fetch first so the reader loop stops consuming and
    // no trailing error event gets appended, then tell the server to cancel.
    abortRef.current?.abort();
    abortRef.current = null;
    api.stopChat(useChatStore.getState().requestId ?? undefined);
    setStreaming(false);
  };

  return (
    <div className="border-t border-line bg-surface p-4">
      <div className="mx-auto max-w-3xl space-y-2">
        {/* Context scope / Provider / Model selectors */}
        <div className="flex items-center gap-2 text-xs">
          <ChatContextChip />
          {chatFeatures.showProviderSelect && (
          <>
          <label className="ml-1 text-fgmuted">Provider:</label>
          <select
            value={chatProvider || ""}
            onChange={(e) => {
              const pid = e.target.value || null;
              setChatProvider(pid);
              // Clear the model rather than pinning the provider's default.
              // Choosing a model is optional: null means "let the provider's
              // configured default answer", and the picker beside this lists
              // everything the provider actually offers if you want to change it.
              setChatModel(null);
            }}
            className="rounded border border-line px-2 py-1 text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500"
          >
            <option value="">Default</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          </>
          )}

          {chatFeatures.showModelSelect && (
          <>
          <label className="ml-2 text-fgmuted">Model:</label>
          {/* Every model the provider offers, listed. Choosing one is optional —
              the first entry leaves it to the provider's configured default — so
              picking a provider is enough to start chatting.

              A dropdown only where there is something to drop down: an endpoint
              that does not implement /models discovers nothing, and a select
              listing one item would take away the ability to name a model that
              exists. That case falls back to a text field. */}
          {modelOptions.length > 0 ? (
            <select
              value={chatModel || ""}
              onChange={(e) => setChatModel(e.target.value || null)}
              disabled={!chatProvider}
              title={`${modelOptions.length} model${
                modelOptions.length === 1 ? "" : "s"
              } available`}
              className="max-w-[16rem] rounded border border-line px-2 py-1 text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500 disabled:cursor-not-allowed disabled:bg-muted disabled:text-fgsubtle"
            >
              <option value="">
                {providerDefaultModel
                  ? `Default (${providerDefaultModel})`
                  : "Provider default"}
              </option>
              {modelOptions.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))}
            </select>
          ) : (
            <input
              type="text"
              value={chatProvider ? chatModel || "" : "default"}
              onChange={(e) => setChatModel(e.target.value || null)}
              disabled={!chatProvider}
              placeholder={chatProvider ? "provider default" : undefined}
              title={
                chatProvider
                  ? "This endpoint listed no models — type a model id, or leave blank for the default"
                  : "Uses your configured default — pick a provider to change it"
              }
              className="w-48 rounded border border-line px-2 py-1 text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500 disabled:cursor-not-allowed disabled:bg-muted disabled:text-fgsubtle"
            />
          )}
          </>
          )}
        </div>

        {/* Input area */}
        <div className="relative flex items-end gap-2">
          {mentionQuery !== null && mentionResults.length > 0 && (
            <div className="absolute bottom-full left-0 right-12 z-10 mb-1 max-h-48 overflow-auto rounded-md border border-line bg-surface shadow-lg">
              {mentionResults.map((item, idx) => (
                <button
                  key={item.path}
                  onMouseDown={(e) => {
                    e.preventDefault();
                    void insertMention(item);
                  }}
                  className={`flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs hover:bg-muted ${idx === mentionIndex ? "bg-muted text-fg" : "text-fgmuted"}`}
                >
                  <span className="font-mono truncate">{item.path}</span>
                  <span className="ml-auto text-[10px] opacity-60">{item.type}</span>
                </button>
              ))}
            </div>
          )}
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => {
              setInput(e.target.value);
              updateMentionQuery(e.target.value, e.target.selectionStart ?? e.target.value.length);
            }}
            onSelect={(e) =>
              updateMentionQuery(
                (e.target as HTMLTextAreaElement).value,
                (e.target as HTMLTextAreaElement).selectionStart ?? 0
              )
            }
            onKeyDown={handleKeyDown}
            onBlur={() => setTimeout(() => setMentionQuery(null), 150)}
            placeholder={
              activeScope
                ? `Ask about ${activeScope.label}... (Shift+Enter for newline) — type @ for files`
                : "Ask about Pegasus workflows... (Shift+Enter for newline) — type @ for files"
            }
            rows={3}
            className="min-h-[72px] max-h-56 flex-1 resize-none rounded-md border border-line px-3 py-2 text-sm shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
          />
          {isStreaming ? (
            <button
              onClick={handleStop}
              title="Stop"
              aria-label="Stop"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-red-600 text-white hover:bg-red-700"
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor">
                <rect x="6" y="6" width="12" height="12" rx="1.5" />
              </svg>
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!input.trim()}
              title="Send"
              aria-label="Send"
              className="flex h-10 w-10 shrink-0 items-center justify-center rounded-full bg-pegasus-600 text-white hover:bg-pegasus-700 disabled:opacity-50"
            >
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                <line x1="12" y1="19" x2="12" y2="5" />
                <polyline points="5 12 12 5 19 12" />
              </svg>
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
