"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { useChatStore, genId } from "@/lib/stores/chat-store";
import { useConsumeChatStream } from "@/lib/hooks/use-chat-stream";
import { usePanelStore } from "@/lib/stores/panel-store";
import { useNotebookContextStore } from "@/lib/stores/notebook-context-store";
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

export function ChatInput() {
  const [input, setInput] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const abortRef = useRef<AbortController | null>(null);
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

  const handleSend = useCallback(async () => {
    const text = input.trim();
    if (!text || isStreaming) return;

    setInput("");

    // Pin the conversation this turn belongs to, so streamed tokens keep
    // landing here even if the user switches conversations mid-response.
    const convId = useChatStore.getState().activeId;

    // Add user message to store
    const userMsg = {
      id: genId(),
      role: "user" as const,
      content: text,
    };
    addMessage(userMsg);

    // When embedded in JupyterLab with a focused notebook, ride its path along
    // in the API copy of the message (not the displayed one). Provider-safe:
    // it's user text, not a system role.
    const { activeNotebookPath, include } =
      useNotebookContextStore.getState();
    const apiText =
      activeNotebookPath && include
        ? `${text}\n\n[Active Jupyter notebook: ${activeNotebookPath} — when I refer to "this notebook", read this file with your tools.]`
        : text;

    // Build message history for the API. Skip empty-content messages (e.g. an
    // assistant bubble that only carried a transient error) and the local
    // context-switch dividers (system role, display-only) so neither becomes
    // part of the model-visible transcript.
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
      if (chatProvider) {
        body.provider = chatProvider;
        if (chatModel) body.model = chatModel;
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
        </div>

        {/* Input area */}
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={
              activeScope
                ? `Ask about ${activeScope.label}... (Shift+Enter for newline)`
                : "Ask about Pegasus workflows... (Shift+Enter for newline)"
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
