"use client";

import { useState, useRef, useCallback, useEffect } from "react";
import { useChatStore, genId } from "@/lib/stores/chat-store";
import { usePanelStore } from "@/lib/stores/panel-store";
import { useNotebookContextStore } from "@/lib/stores/notebook-context-store";
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
  const addMessage = useChatStore((s) => s.addMessage);
  const appendToLast = useChatStore((s) => s.appendToLast);
  const addToolCall = useChatStore((s) => s.addToolCall);
  const addToolResult = useChatStore((s) => s.addToolResult);
  const setLastMeta = useChatStore((s) => s.setLastMeta);
  const setLastServerId = useChatStore((s) => s.setLastServerId);
  const setLastError = useChatStore((s) => s.setLastError);
  const setStreaming = useChatStore((s) => s.setStreaming);
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
  // choice — using that provider's saved key/base URL. Empty on the Default
  // provider or when discovery fails (the field then just accepts free text).
  const selectedConfig = savedConfigs.find(
    (c) => c.provider_id === chatProvider
  );
  const { data: modelsData } = useProviderModels(
    chatProvider,
    selectedConfig?.api_key ?? "",
    selectedConfig?.base_url ?? null
  );
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
    // assistant bubble that only carried a transient error) so failures never
    // become part of the model-visible transcript.
    const apiMessages = [
      ...messages
        .filter((m) => m.content.trim().length > 0)
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

      if (!response.body) {
        setLastError("No response body", convId);
        return;
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const data = line.slice(6);
          if (data === "[DONE]") break;

          try {
            const event = JSON.parse(data);
            if (event.content) {
              appendToLast(event.content, convId);
            } else if (event.tool_call) {
              addToolCall(event.tool_call, convId);
            } else if (event.tool_result) {
              addToolResult(event.tool_result, convId);
            } else if (event.meta) {
              setLastMeta(
                {
                  tokens: event.meta.tokens,
                  inputTokens: event.meta.input_tokens,
                  outputTokens: event.meta.output_tokens,
                  toolCalls: event.meta.tool_calls,
                  durationS: event.meta.duration_s,
                },
                convId
              );
            } else if (typeof event.message_id === "number") {
              setLastServerId(event.message_id, convId);
            } else if (event.error) {
              setLastError(event.error, convId);
            }
          } catch {
            // Skip malformed SSE lines
          }
        }
      }
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
    appendToLast,
    addToolCall,
    addToolResult,
    setLastMeta,
    setLastServerId,
    setLastError,
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
        {/* Provider / Model selectors */}
        <div className="flex items-center gap-2 text-xs">
          <label className="text-fgmuted">Provider:</label>
          <select
            value={chatProvider || ""}
            onChange={(e) => {
              const pid = e.target.value || null;
              setChatProvider(pid);
              // Default provider → use the configured default model (no typing
              // needed). A specific provider → auto-fill its default model.
              const p = providers.find((p) => p.id === pid);
              setChatModel(pid && p ? p.default_model : null);
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
          {/* Default provider → the model is chosen for you (disabled
              "default"). A specific provider → an editable chooser: the
              datalist offers the provider's discovered models to pick, and you
              can still type any model id. */}
          <input
            type="text"
            list={chatProvider ? "chat-model-options" : undefined}
            value={chatProvider ? chatModel || "" : "default"}
            onChange={(e) => setChatModel(e.target.value || null)}
            disabled={!chatProvider}
            placeholder={chatProvider ? "choose or type a model" : undefined}
            title={
              chatProvider
                ? "Pick from the provider's models or type a model id"
                : "Uses your configured default — pick a provider to change it"
            }
            className="w-48 rounded border border-line px-2 py-1 text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500 disabled:cursor-not-allowed disabled:bg-muted disabled:text-fgsubtle"
          />
          {chatProvider && modelOptions.length > 0 && (
            <datalist id="chat-model-options">
              {modelOptions.map((m) => (
                <option key={m} value={m} />
              ))}
            </datalist>
          )}
        </div>

        {/* Input area */}
        <div className="flex items-end gap-2">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Ask about Pegasus workflows... (Shift+Enter for newline)"
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
