"use client";

/**
 * Consume a chat SSE stream into the chat store.
 *
 * Extracted from `chat-input.tsx` so the two ways a stream can arrive share one
 * implementation:
 *
 * 1. the POST that starts a turn, and
 * 2. reattaching to a turn already in flight (#282) — a panel that reloaded
 *    while the assistant was typing, which on Studio's full-page-load nav is
 *    every click of the left sidebar.
 *
 * Two copies of this event mapping would drift, and the copy that drifted would
 * be the reattach path — the one nobody exercises while developing.
 */

import { useCallback } from "react";
import { useChatStore } from "@/lib/stores/chat-store";

export function useConsumeChatStream() {
  const appendToLast = useChatStore((s) => s.appendToLast);
  const addToolCall = useChatStore((s) => s.addToolCall);
  const addToolResult = useChatStore((s) => s.addToolResult);
  const addPermissionRequest = useChatStore((s) => s.addPermissionRequest);
  const addNotice = useChatStore((s) => s.addNotice);
  const clearPermissionRequest = useChatStore((s) => s.clearPermissionRequest);
  const setLastMeta = useChatStore((s) => s.setLastMeta);
  const setLastServerId = useChatStore((s) => s.setLastServerId);
  const setLastError = useChatStore((s) => s.setLastError);

  return useCallback(
    async (body: ReadableStream<Uint8Array>, convId: string | undefined) => {
      const reader = body.getReader();
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
            if (event.notice) {
              addNotice(event.notice, convId);
            } else if (event.content) {
              appendToLast(event.content, convId);
            } else if (event.permission_request) {
              addPermissionRequest(event.permission_request, convId);
            } else if (event.tool_call) {
              // The tool ran (approved or auto): the pending card, if any,
              // is now resolved — drop it so only the call/result show.
              clearPermissionRequest(event.tool_call.id, convId);
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
                  changedFiles: event.meta.changed_files,
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
    },
    [
      appendToLast,
      addToolCall,
      addToolResult,
      addPermissionRequest,
      clearPermissionRequest,
      addNotice,
      setLastMeta,
      setLastServerId,
      setLastError,
    ]
  );
}
