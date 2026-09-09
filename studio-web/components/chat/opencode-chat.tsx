"use client";

/**
 * The chat panel, backed by the OpenCode agent server (#367).
 *
 * Transcript and composer in one component because the state they share lives
 * in `useOpenCodeChat`, not in a store: OpenCode is the source of truth and
 * this is a view of it. The builtin engine splits them (ChatPanel +
 * ChatInput) because its state does sit in a store.
 *
 * What this component decides is *how to draw* OpenCode's data. What it must
 * never do is interpret it: parts go to the registry in opencode-parts.tsx,
 * which has a fallback, so an OpenCode release that adds a part type or an
 * event type still renders and still completes the turn.
 */

import { memo, useEffect, useMemo, useRef, useState } from "react";
import type { AssistantMessage } from "@opencode-ai/sdk";
import { PartView } from "@/components/chat/opencode-parts";
import { ChatContextChip } from "@/components/chat/context-chip";
import { PegasusLogo } from "@/components/ui/pegasus-logo";
import {
  errorMessage,
  isAbortError,
  useOpenCodeChat,
  type RenderedMessage,
} from "@/lib/hooks/use-opencode-chat";
import * as oc from "@/lib/opencode/client";
import type {
  PermissionRequest,
  QuestionRequest,
} from "@/lib/opencode/client";
import { usePanelStore } from "@/lib/stores/panel-store";
import { cn } from "@/lib/utils";

const STARTERS = [
  "Build me a workflow that",
  "Why did my run fail",
  "Explain what this workflow does",
];

interface ModelChoice {
  providerID: string;
  modelID: string;
  label: string;
}

export function OpenCodeChat() {
  const chat = useOpenCodeChat();
  const [input, setInput] = useState("");
  const [models, setModels] = useState<ModelChoice[] | null>(null);
  const [agents, setAgents] = useState<oc.AgentInfo[] | null>(null);
  const [showCapabilities, setShowCapabilities] = useState(false);
  const [picked, setPicked] = useState<string>("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [engineDown, setEngineDown] = useState<oc.EngineStatus | null>(null);
  const [restarting, setRestarting] = useState(false);

  // Bumped by the recovery button so the catalog is re-fetched after a restart;
  // without it the picker stayed on "Configured default" alone for the life of
  // the page once the first fetch had failed.
  const [catalogAttempt, setCatalogAttempt] = useState(0);

  // Model list from the engine's own provider catalog, so the picker offers
  // exactly what the generated config declared — no second source of truth
  // about which models exist.
  useEffect(() => {
    let cancelled = false;
    oc.getProviders()
      .then((catalog) => {
        if (cancelled) return;
        const choices: ModelChoice[] = [];
        for (const provider of catalog.providers ?? []) {
          for (const [modelID, model] of Object.entries(provider.models ?? {})) {
            choices.push({
              providerID: provider.id,
              modelID,
              label: `${provider.name || provider.id} / ${model?.name || modelID}`,
            });
          }
        }
        setModels(choices);
      })
      .catch(() => setModels([]));
    return () => {
      cancelled = true;
    };
  }, [catalogAttempt]);

  // The agents this engine offers. Read from the server rather than hardcoded,
  // so an agent added by a marketplace update or by the user's own config
  // appears without a change here.
  useEffect(() => {
    let cancelled = false;
    oc.listAgents(chat.sessionDirectory)
      .then((list) => {
        if (!cancelled) setAgents(list);
      })
      .catch(() => {
        if (!cancelled) setAgents([]);
      });
    return () => {
      cancelled = true;
    };
    // Re-read when the session moves: a workflow project can define its own
    // agents under .opencode/, and the picker should offer them.
  }, [catalogAttempt, chat.sessionDirectory]);

  // Why the panel is empty, when it is. A failed fetch alone leaves the user
  // looking at a blank transcript with nothing to act on; "the engine isn't
  // running, here is the button" is actionable.
  useEffect(() => {
    if (chat.connection !== "retrying" && !chat.error) {
      setEngineDown(null);
      return;
    }
    let cancelled = false;
    oc.getEngineStatus()
      .then((status) => {
        if (!cancelled) setEngineDown(status.running ? null : status);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [chat.connection, chat.error]);

  // Only follow the tail when already near it, so a burst of tool output
  // doesn't yank someone away from what they were reading.
  //
  // Coalesced to one scroll per frame, and instant rather than smooth while a
  // reply streams: a long answer produces hundreds of state updates, and
  // restarting a smooth animation on each one meant the proximity test below
  // was reading a scrollTop that the previous animation had not finished
  // moving — so auto-follow would quietly give up partway through.
  const followRef = useRef<number | null>(null);
  useEffect(() => {
    if (followRef.current !== null) return;
    followRef.current = requestAnimationFrame(() => {
      followRef.current = null;
      const el = scrollRef.current;
      if (!el) return;
      if (el.scrollHeight - el.scrollTop - el.clientHeight < 160) {
        el.scrollTop = el.scrollHeight;
      }
    });
  }, [chat.messages, chat.busy]);
  useEffect(
    () => () => {
      if (followRef.current !== null) cancelAnimationFrame(followRef.current);
    },
    []
  );

  // A one-shot draft handed over from elsewhere in the app (a dashboard card).
  const chatDraft = usePanelStore((s) => s.chatDraft);
  const setChatDraft = usePanelStore((s) => s.setChatDraft);
  useEffect(() => {
    if (chatDraft == null) return;
    setInput(chatDraft);
    setChatDraft(null);
    const el = textareaRef.current;
    if (el) {
      el.focus();
      requestAnimationFrame(() => {
        el.selectionStart = el.selectionEnd = el.value.length;
      });
    }
  }, [chatDraft, setChatDraft]);

  const model = useMemo(() => {
    if (!picked) return undefined;
    const [providerID, ...rest] = picked.split("/");
    return { providerID, modelID: rest.join("/") };
  }, [picked]);

  const submit = () => {
    const text = input.trim();
    if (!text || chat.busy) return;
    setInput("");
    void chat.send(text, model);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (
      e.key === "Enter" &&
      !e.shiftKey &&
      !(e.nativeEvent as { isComposing?: boolean }).isComposing
    ) {
      e.preventDefault();
      submit();
    }
  };

  const restart = async () => {
    setRestarting(true);
    try {
      await oc.restartEngine();
      // Give supervisord time to bring it back before anything re-probes, so
      // the retry below doesn't just fail against a process still starting.
      await new Promise((resolve) => setTimeout(resolve, 1500));
    } catch {
      /* the status line reports the outcome on the next probe */
    } finally {
      setRestarting(false);
      // Restarting the engine is only half a recovery: if this panel never got
      // a session (the engine was down when it mounted), nothing else would
      // ever try again and the card would keep offering a button that fixed
      // the server and left the panel dead.
      chat.retry();
      setCatalogAttempt((n) => n + 1);
    }
  };

  // Keyed on whether anything is actually rendered yet, not on the last
  // message's role: an assistant message exists as soon as the turn starts, so
  // a role test hid the indicator while the only parts were an invisible
  // step-start and an empty text part — several seconds of a blank bubble
  // where the "Thinking" dots had been, on any model that pauses before its
  // first token.
  const selectable = useMemo(
    () => (agents ? oc.selectableAgents(agents) : []),
    [agents]
  );
  const activeAgent = useMemo(
    () => selectable.find((a) => a.name === chat.agent),
    [selectable, chat.agent]
  );
  const turns = useMemo(() => groupTurns(chat.messages), [chat.messages]);
  const lastMessage = chat.messages[chat.messages.length - 1];
  const showThinking =
    chat.busy &&
    chat.permissions.length === 0 &&
    chat.questions.length === 0 &&
    (lastMessage?.info.role !== "assistant" ||
      !hasRenderableContent(lastMessage));

  return (
    <>
      <div ref={scrollRef} className="flex-1 overflow-auto p-4">
        {engineDown && (
          <div className="mx-auto mb-3 max-w-3xl rounded-md border border-amber-500/50 bg-amber-500/10 p-3 text-xs">
            <div className="font-medium text-amber-700 dark:text-amber-400">
              The chat engine is not responding
            </div>
            <p className="mt-1 text-fgmuted">
              {engineDown.detail ||
                (engineDown.reason === "not_listening"
                  ? "It is not listening yet — it may still be starting."
                  : "OpenCode is unreachable in this session.")}
            </p>
            <button
              onClick={restart}
              disabled={restarting}
              className="mt-2 rounded-md border border-line px-2.5 py-1 font-medium text-fgmuted hover:bg-muted disabled:opacity-50"
            >
              {restarting ? "Restarting…" : "Restart the engine"}
            </button>
          </div>
        )}

        {/* A failure that leaves the panel with no session, while the engine
            itself reports healthy — a transient 500 on create, a network blip.
            The engine-down card above does not apply, the transcript is empty
            so the error box below never renders, and Send is disabled for want
            of a session: without this the panel is simply dead and silent. */}
        {!engineDown && chat.error && !chat.sessionID && (
          <div className="mx-auto mb-3 max-w-3xl rounded-md border border-rose-500/40 bg-rose-500/10 p-3 text-xs">
            <div className="font-medium text-rose-700 dark:text-rose-400">
              Could not start a chat session
            </div>
            <p className="mt-1 text-fgmuted [overflow-wrap:anywhere]">
              {chat.error}
            </p>
            <button
              onClick={() => {
                // Whatever stopped the session from being created stopped the
                // catalogs too, so recovery reloads both rather than leaving
                // the capability lists stale behind a working chat.
                chat.retry();
                setCatalogAttempt((n) => n + 1);
              }}
              className="mt-2 rounded-md border border-line px-2.5 py-1 font-medium text-fgmuted hover:bg-muted"
            >
              Try again
            </button>
          </div>
        )}

        {chat.messages.length === 0 && !engineDown ? (
          <div className="flex h-full items-center justify-center">
            <div className="text-center text-fgsubtle">
              <div className="mb-3 flex justify-center">
                <PegasusLogo size={48} />
              </div>
              <p className="text-lg">Welcome to PegasusAI Chat</p>
              <p className="mt-1 text-sm">
                Describe what you want to do and I&apos;ll build or fix it for
                you.
              </p>
              <div className="mt-4 space-y-1.5">
                {STARTERS.map((starter) => (
                  <button
                    key={starter}
                    onClick={() => setInput(starter + " ")}
                    className="block w-full rounded-md border border-line bg-surface px-3 py-2 text-left text-sm text-fgmuted transition-colors hover:border-pegasus-400 hover:text-fg"
                  >
                    {starter}…
                  </button>
                ))}
              </div>
            </div>
          </div>
        ) : (
          <div className="mx-auto max-w-3xl space-y-4">
            {turns.map((turn) => (
              <TurnBubble key={turn.key} turn={turn} />
            ))}

            {chat.permissions.map((request) => (
              <PermissionCard
                key={request.id}
                request={request}
                onReply={chat.answerPermission}
              />
            ))}
            {chat.questions.map((request) => (
              <QuestionCard
                key={request.id}
                request={request}
                onAnswer={chat.answerQuestion}
                onReject={chat.rejectQuestion}
              />
            ))}

            {chat.error && (
              <div className="flex items-start gap-1.5 rounded border border-rose-500/40 bg-rose-500/10 px-2 py-1.5 text-xs text-rose-600 dark:text-rose-400">
                <span className="font-semibold">Error:</span>
                <span className="[overflow-wrap:anywhere]">{chat.error}</span>
              </div>
            )}
            {showThinking && <ThinkingIndicator />}
            <div ref={bottomRef} />
          </div>
        )}
      </div>

      <div className="border-t border-line bg-surface p-4">
        <div className="mx-auto max-w-3xl space-y-2">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <ChatContextChip />

            {/* Plan mode is an agent, and OpenCode enforces it: the `plan`
                agent denies every edit tool server-side, so this is a real
                guarantee rather than an instruction the model may ignore. */}
            {selectable.length > 1 && (
              <>
                <label className="text-fgmuted" htmlFor="opencode-agent">
                  Mode:
                </label>
                <select
                  id="opencode-agent"
                  value={chat.agent ?? ""}
                  onChange={(e) => chat.setAgent(e.target.value)}
                  title={activeAgent?.description || "Which agent answers"}
                  className="rounded border border-line px-2 py-1 text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500"
                >
                  <option value="">Default</option>
                  {selectable.map((a) => (
                    <option key={a.name} value={a.name}>
                      {a.name}
                    </option>
                  ))}
                </select>
              </>
            )}

            <label className="text-fgmuted" htmlFor="opencode-model">
              Model:
            </label>
            <select
              id="opencode-model"
              value={picked}
              onChange={(e) => setPicked(e.target.value)}
              className="max-w-[18rem] rounded border border-line px-2 py-1 text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500"
            >
              {/* Empty means "whatever the generated config says", which is the
                  provider the user activated in Settings. */}
              <option value="">Configured default</option>
              {(models ?? []).map((m) => (
                <option
                  key={`${m.providerID}/${m.modelID}`}
                  value={`${m.providerID}/${m.modelID}`}
                >
                  {m.label}
                </option>
              ))}
            </select>
            <button
              onClick={() => setShowCapabilities((v) => !v)}
              title="Skills, subagents and commands this session can use"
              aria-expanded={showCapabilities}
              className={cn(
                "ml-auto rounded border px-2 py-1 transition-colors",
                showCapabilities
                  ? "border-pegasus-400 text-pegasus-700 dark:text-pegasus-300"
                  : "border-line text-fgmuted hover:border-pegasus-300 hover:text-fg"
              )}
            >
              What it can do
            </button>
            {chat.connection === "retrying" && (
              <span className="w-full text-amber-600 dark:text-amber-400">
                Reconnecting…
              </span>
            )}
          </div>

          {/* Why this is a panel and not a set of controls: skills and
              subagents are not chosen, they are *matched* — the agent picks a
              skill by its description and delegates to a subagent through its
              task tool. So the useful thing to show is what is loaded and
              where it came from, which is also the fastest way to answer "are
              my Pegasus skills actually here?". */}
          {showCapabilities && (
            <CapabilitiesPanel
              agents={agents}
              directory={chat.sessionDirectory}
              directoryStatus={chat.directoryStatus}
              attempt={catalogAttempt}
              onRetry={() => {
                // Re-resolves the session's directory as well as the lists:
                // if that lookup was what failed, refetching the catalogs
                // alone would just ask about the wrong directory again.
                chat.retry();
                setCatalogAttempt((n) => n + 1);
              }}
            />
          )}

          {activeAgent?.description && (
            <p className="text-xs text-fgmuted">{activeAgent.description}</p>
          )}

          <div className="flex items-end gap-2">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Ask about Pegasus workflows... (Shift+Enter for newline)"
              rows={3}
              className="min-h-[72px] max-h-56 flex-1 resize-none rounded-md border border-line px-3 py-2 text-sm shadow-sm focus:border-pegasus-500 focus:ring-pegasus-500"
            />
            {chat.busy ? (
              <button
                onClick={() => void chat.stop()}
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
                onClick={submit}
                disabled={!input.trim() || !chat.sessionID}
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
    </>
  );
}

/**
 * Whether a message has anything to draw yet.
 *
 * Turn bookkeeping (`step-start`, `step-finish`, `snapshot`) is invisible, and
 * a text part exists before its first delta lands, so "has parts" is not the
 * same question as "has content".
 */
function hasRenderableContent(message: RenderedMessage): boolean {
  return message.parts.some((part) => {
    if (
      part.type === "step-start" ||
      part.type === "step-finish" ||
      part.type === "snapshot"
    ) {
      return false;
    }
    if (part.type === "text" || part.type === "reasoning") {
      return part.text.trim().length > 0;
    }
    return true;
  });
}

/**
 * What the engine has loaded: subagents, skills, commands.
 *
 * Read-only on purpose. None of these are things a user invokes from here — a
 * skill is matched by the agent against its description, and a subagent is
 * delegated to through the task tool — so a picker would misrepresent how they
 * work. What is genuinely useful is knowing they are *there*, and where they
 * came from: Studio's own skills arrive from a marketplace checkout that has
 * gone missing before (#179), and "is my Pegasus skill loaded?" was previously
 * only answerable from a terminal.
 */
type Catalog<T> =
  | { status: "loading" }
  | { status: "ok"; items: T[] }
  | { status: "error" };

function CapabilitiesPanel({
  agents,
  directory,
  directoryStatus,
  attempt,
  onRetry,
}: {
  agents: oc.AgentInfo[] | null;
  directory?: string;
  directoryStatus: "none" | "loading" | "ok" | "error";
  attempt: number;
  onRetry: () => void;
}) {
  const [skills, setSkills] = useState<Catalog<oc.SkillInfo>>({
    status: "loading",
  });
  const [commands, setCommands] = useState<Catalog<oc.CommandInfo>>({
    status: "loading",
  });

  useEffect(() => {
    let cancelled = false;
    setSkills({ status: "loading" });
    setCommands({ status: "loading" });
    void oc
      .listSkills(directory)
      .then((items) => !cancelled && setSkills({ status: "ok", items }))
      // A failed lookup is not an empty one. This panel exists to answer "are
      // my skills loaded?", so reporting "none" when we could not ask is the
      // one wrong answer it must never give.
      .catch(() => !cancelled && setSkills({ status: "error" }));
    void oc
      .listCommands(directory)
      .then((items) => !cancelled && setCommands({ status: "ok", items }))
      .catch(() => !cancelled && setCommands({ status: "error" }));
    return () => {
      cancelled = true;
    };
    // `attempt` as well as `directory`: recovering the engine has to clear an
    // error state here too, or the panel keeps saying it could not check long
    // after it could.
  }, [directory, attempt]);

  const delegates = agents ? oc.subagents(agents) : [];
  // A skill is also offered as a command of the same name, so listing both in
  // full would show everything twice.
  const skillNames = new Set(
    skills.status === "ok" ? skills.items.map((s) => s.name) : []
  );
  const ownCommands =
    commands.status === "ok"
      ? commands.items.filter((c) => !skillNames.has(c.name))
      : [];

  // A failed *directory* lookup counts as a failure even when every list came
  // back, because those lists then describe the engine's default directory
  // rather than this session's — the case that reads as perfectly normal and
  // is simply wrong.
  const failed =
    directoryStatus === "error" ||
    skills.status === "error" ||
    commands.status === "error" ||
    (agents?.length ?? 0) === 0;

  return (
    <div className="max-h-56 space-y-3 overflow-auto rounded-md border border-line bg-muted p-2.5 text-xs">
      {directoryStatus === "error" && (
        <p className="rounded border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-amber-700 dark:text-amber-400">
          Could not tell which folder this chat is working in, so this list may
          be for the wrong one — anything the workflow itself defines would be
          missing.
        </p>
      )}
      {failed && (
        <button
          onClick={onRetry}
          className="w-full rounded border border-line bg-surface px-2 py-1 font-medium text-fgmuted hover:text-fg"
        >
          Check again
        </button>
      )}
      <Capability
        title="Subagents"
        status={agents === null ? "loading" : "ok"}
        empty="None — this engine runs every step itself."
        items={delegates.map((a) => ({
          name: a.name,
          detail: a.description,
        }))}
        note="The assistant delegates to these on its own; you don't pick one."
      />
      <Capability
        title="Skills"
        status={skills.status}
        empty="No skills loaded."
        items={
          skills.status === "ok"
            ? skills.items.map((s) => ({
                name: s.name,
                detail: s.description,
                // "<built-in>" or the SKILL.md path — the answer to "is the
                // marketplace checkout actually being read?".
                source: s.location,
              }))
            : []
        }
        note="Matched by description when they apply; ask in plain language."
      />
      <Capability
        title="Commands"
        status={commands.status}
        empty="No commands."
        items={ownCommands.map((c) => ({ name: c.name, detail: c.description }))}
      />
    </div>
  );
}

function Capability({
  title,
  items,
  empty,
  note,
  status,
}: {
  title: string;
  items: { name: string; detail?: string; source?: string }[];
  empty: string;
  note?: string;
  status: "loading" | "ok" | "error";
}) {
  const loading = status === "loading";
  return (
    <div>
      <div className="flex items-baseline gap-2">
        <h4 className="text-[10px] font-semibold uppercase tracking-wider text-fgsubtle">
          {title}
        </h4>
        {status === "ok" && (
          <span className="text-fgsubtle">{items.length}</span>
        )}
      </div>
      {note && <p className="mt-0.5 text-fgsubtle">{note}</p>}
      {status === "error" ? (
        <p className="mt-1 text-amber-600 dark:text-amber-400">
          Could not check — the engine did not answer.
        </p>
      ) : loading ? (
        <p className="mt-1 text-fgsubtle">Loading…</p>
      ) : items.length === 0 ? (
        <p className="mt-1 text-fgsubtle">{empty}</p>
      ) : (
        <ul className="mt-1 space-y-1">
          {items.map((item) => (
            <li key={item.name}>
              <span className="font-mono font-medium text-fg">{item.name}</span>
              {item.detail && (
                <span className="ml-1.5 text-fgmuted">
                  {item.detail.length > 140
                    ? `${item.detail.slice(0, 140)}…`
                    : item.detail}
                </span>
              )}
              {item.source && (
                <span
                  className="ml-1.5 block truncate text-[10px] text-fgsubtle"
                  title={item.source}
                >
                  {item.source}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/**
 * One visual turn: a user prompt, or every assistant step that answered it.
 *
 * OpenCode starts a **new assistant message per step**, so a reply that calls
 * three tools arrives as four messages. Rendered one bubble each, a single
 * answer became a stack of cards that each repeated the "AI" label and its own
 * token footer — the transcript read as four separate replies rather than one
 * train of thought. Consecutive user messages are *not* merged: those are
 * separate things the person said.
 */
interface Turn {
  key: string;
  role: "user" | "assistant";
  messages: RenderedMessage[];
}

function groupTurns(messages: RenderedMessage[]): Turn[] {
  const turns: Turn[] = [];
  for (const message of messages) {
    const role = message.info.role === "user" ? "user" : "assistant";
    const open = turns[turns.length - 1];
    if (open && role === "assistant" && open.role === "assistant") {
      open.messages.push(message);
    } else {
      turns.push({ key: message.info.id, role, messages: [message] });
    }
  }
  return turns;
}

function assistantError(message: RenderedMessage) {
  return message.info.role === "assistant"
    ? (message.info as AssistantMessage).error
    : undefined;
}

/**
 * True when two turns hold the same messages and the same parts.
 *
 * Needed because the transcript selector rebuilds its wrapper objects on every
 * state change — and a delta is a state change — so an identity check on the
 * prop would never match and the memo would never hit. The `info` and `part`
 * objects inside *are* stable (the reducer replaces only what changed), which
 * is what makes this comparison both cheap and effective.
 */
function sameTurn(a: { turn: Turn }, b: { turn: Turn }): boolean {
  const left = a.turn.messages;
  const right = b.turn.messages;
  if (left.length !== right.length) return false;
  for (let i = 0; i < left.length; i += 1) {
    if (left[i].info !== right[i].info) return false;
    const lp = left[i].parts;
    const rp = right[i].parts;
    if (lp.length !== rp.length) return false;
    for (let j = 0; j < lp.length; j += 1) {
      if (lp[j] !== rp[j]) return false;
    }
  }
  return true;
}

const TurnBubble = memo(function TurnBubble({ turn }: { turn: Turn }) {
  const isUser = turn.role === "user";
  const shown = turn.messages.filter(
    (m) => hasRenderableContent(m) || assistantError(m)
  );
  if (shown.length === 0) return null;

  const assistants = turn.messages
    .map((m) => m.info)
    .filter((info): info is AssistantMessage => info.role === "assistant");

  return (
    <div className={cn("flex flex-col", isUser ? "items-end" : "items-start")}>
      <span className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wider text-fgsubtle">
        {isUser ? "You" : "AI"}
      </span>
      <div
        className={cn(
          "max-w-[85%] space-y-2 rounded-lg px-4 py-3 text-sm",
          isUser
            ? "bg-pegasus-600 text-white"
            : "border border-line bg-surface text-fg"
        )}
      >
        {shown.map((message) => (
          <div key={message.info.id} className="space-y-2">
            {message.parts.map((part) => (
              <PartView key={part.id} part={part} />
            ))}
            {/* A stop the user asked for is not a failure. OpenCode records it
                as MessageAbortedError, so without this a red "Error:" box is
                the reward for pressing the Stop button. */}
            {assistantError(message) && isAbortError(assistantError(message)) && (
              <div className="text-xs text-fgsubtle">Stopped</div>
            )}
            {assistantError(message) && !isAbortError(assistantError(message)) && (
              <div className="flex items-start gap-1.5 rounded border border-rose-500/40 bg-rose-500/10 px-2 py-1.5 text-xs text-rose-600 dark:text-rose-400">
                <span className="font-semibold">Error:</span>
                <span className="[overflow-wrap:anywhere]">
                  {errorMessage(assistantError(message))}
                </span>
              </div>
            )}
          </div>
        ))}

        {/* One footer for the whole turn, summed across its steps. Per-step
            footers turned a three-tool answer into three token counts. */}
        {assistants.length > 0 && <UsageFooter messages={assistants} />}
      </div>
    </div>
  );
}, sameTurn);

function UsageFooter({ messages }: { messages: AssistantMessage[] }) {
  const summary = formatTurnUsage(messages);
  if (!summary) return null;
  return (
    <div className="border-t border-line pt-1.5 text-xs text-fgmuted">
      {summary}
    </div>
  );
}

/**
 * Tokens, cost and wall-clock for a whole turn.
 *
 * Tokens are summed across steps, which is what the turn actually cost; the
 * elapsed time spans the first step's start to the last one's completion, so it
 * includes the gaps where a tool was running rather than only model time.
 */
function formatTurnUsage(messages: AssistantMessage[]): string | null {
  let tokens = 0;
  let cost = 0;
  for (const message of messages) {
    const { input, output, cache } = message.tokens;
    tokens += input + output + (cache?.read ?? 0) + (cache?.write ?? 0);
    cost += message.cost;
  }
  if (tokens === 0) return null;

  const parts = [`~${tokens.toLocaleString()} tokens`];
  if (messages.length > 1) {
    parts.push(`${messages.length} steps`);
  }
  if (cost > 0) parts.push(`$${cost.toFixed(4)}`);

  const started = messages[0].time.created;
  const finished = messages[messages.length - 1].time.completed;
  if (finished && finished > started) {
    parts.push(`${((finished - started) / 1000).toFixed(1)}s`);
  }

  const model = messages[messages.length - 1].modelID;
  if (model) parts.push(model);
  return parts.join(", ");
}

function ThinkingIndicator() {
  return (
    <div className="flex justify-start">
      <div className="mr-2 mt-1 shrink-0">
        <PegasusLogo size={24} />
      </div>
      <div className="flex items-center gap-1 rounded-lg border border-line bg-surface px-4 py-3">
        <span className="text-xs text-fgmuted">Thinking</span>
        <span className="flex gap-1">
          {[0, 150, 300].map((d) => (
            <span
              key={d}
              className="inline-block h-1.5 w-1.5 animate-bounce rounded-full bg-fgsubtle"
              style={{ animationDelay: `${d}ms` }}
            />
          ))}
        </span>
      </div>
    </div>
  );
}

/**
 * One dialog for every permission OpenCode can ask about, now and later.
 *
 * The request names what it wants (`permission`), what it wants it for
 * (`patterns`, the command itself for bash) and what an "always" would allow
 * (`always`), so nothing here is per-tool and a new gated tool needs no new
 * component.
 */
function PermissionCard({
  request,
  onReply,
}: {
  request: PermissionRequest;
  onReply: (id: string, reply: oc.PermissionReply) => Promise<void>;
}) {
  const [busy, setBusy] = useState<oc.PermissionReply | null>(null);
  const decide = (reply: oc.PermissionReply) => {
    setBusy(reply);
    void onReply(request.id, reply);
  };
  const detail =
    (typeof request.metadata?.command === "string"
      ? request.metadata.command
      : null) ?? request.patterns.join("\n");
  const alwaysPattern = request.always?.[0];

  return (
    <div className="rounded-md border border-amber-500/50 bg-amber-500/10 p-2.5 text-xs">
      <div className="flex items-center gap-1.5 font-medium text-amber-700 dark:text-amber-400">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
          <line x1="12" y1="9" x2="12" y2="13" />
          <line x1="12" y1="17" x2="12.01" y2="17" />
        </svg>
        Allow {request.permission}?
      </div>
      {/* Full contrast, deliberately: this is the command being authorised, and
          it is the one string in the app that must not be a muted grey. */}
      {detail && (
        <pre className="mt-1.5 max-h-32 overflow-auto whitespace-pre-wrap rounded border border-line bg-surface px-2 py-1 font-mono text-fg [overflow-wrap:anywhere]">
          {detail}
        </pre>
      )}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          onClick={() => decide("once")}
          disabled={busy !== null}
          className="rounded-md bg-pegasus-600 px-3 py-1 font-medium text-white hover:bg-pegasus-700 disabled:opacity-50"
        >
          Allow once
        </button>
        {alwaysPattern && (
          <button
            onClick={() => decide("always")}
            disabled={busy !== null}
            title={`Allow ${alwaysPattern} for the rest of this session`}
            className="rounded-md border border-line px-3 py-1 font-medium text-fgmuted hover:bg-muted disabled:opacity-50"
          >
            Always allow <code className="font-mono">{alwaysPattern}</code>
          </button>
        )}
        <button
          onClick={() => decide("reject")}
          disabled={busy !== null}
          className="rounded-md border border-line px-3 py-1 font-medium text-fgmuted hover:bg-muted disabled:opacity-50"
        >
          Deny
        </button>
      </div>
    </div>
  );
}

/** The agent asking the user something mid-turn. Same shape for every tool. */
function QuestionCard({
  request,
  onAnswer,
  onReject,
}: {
  request: QuestionRequest;
  onAnswer: (id: string, answers: string[][]) => Promise<void>;
  onReject: (id: string) => Promise<void>;
}) {
  const [chosen, setChosen] = useState<string[][]>(
    request.questions.map(() => [])
  );
  // Free-text answers, for a question flagged `custom`. Without these a
  // custom question with no preset options could not be answered at all: the
  // only enabled control was Skip, which rejects the question and derails the
  // turn the agent was asking about.
  const [typed, setTyped] = useState<string[]>(request.questions.map(() => ""));
  const [busy, setBusy] = useState(false);

  const toggle = (index: number, label: string, multiple?: boolean) => {
    setChosen((current) =>
      current.map((answer, i) => {
        if (i !== index) return answer;
        if (!multiple) return [label];
        return answer.includes(label)
          ? answer.filter((l) => l !== label)
          : [...answer, label];
      })
    );
  };

  // An answer is whatever was picked, plus anything typed for a custom
  // question.
  const answers = chosen.map((picked, index) => {
    const extra = typed[index]?.trim();
    return extra ? [...picked, extra] : picked;
  });
  const complete =
    request.questions.length > 0 && answers.every((answer) => answer.length > 0);

  return (
    <div className="rounded-md border border-pegasus-400/60 bg-pegasus-50 p-2.5 text-xs dark:bg-pegasus-400/10">
      {request.questions.map((question, index) => (
        <div key={question.header || index} className="mb-2">
          <div className="font-medium text-fg">{question.question}</div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {question.options.map((option) => (
              <button
                key={option.label}
                onClick={() => toggle(index, option.label, question.multiple)}
                title={option.description}
                className={cn(
                  "rounded-md border px-2.5 py-1 text-left",
                  chosen[index]?.includes(option.label)
                    ? "border-pegasus-500 bg-pegasus-600 text-white"
                    : "border-line bg-surface text-fgmuted hover:border-pegasus-300 hover:text-fg"
                )}
              >
                {option.label}
              </button>
            ))}
          </div>
          {question.custom && (
            <input
              type="text"
              value={typed[index] ?? ""}
              onChange={(e) =>
                setTyped((current) =>
                  current.map((v, i) => (i === index ? e.target.value : v))
                )
              }
              placeholder="Or type your own answer"
              className="mt-1.5 w-full rounded border border-line px-2 py-1 text-xs text-fg focus:border-pegasus-500 focus:ring-pegasus-500"
            />
          )}
        </div>
      ))}
      <div className="flex items-center gap-2">
        <button
          onClick={() => {
            setBusy(true);
            void onAnswer(request.id, answers);
          }}
          disabled={busy || !complete}
          className="rounded-md bg-pegasus-600 px-3 py-1 font-medium text-white hover:bg-pegasus-700 disabled:opacity-50"
        >
          Answer
        </button>
        <button
          onClick={() => {
            setBusy(true);
            void onReject(request.id);
          }}
          disabled={busy}
          className="rounded-md border border-line px-3 py-1 font-medium text-fgmuted hover:bg-muted disabled:opacity-50"
        >
          Skip
        </button>
      </div>
    </div>
  );
}
