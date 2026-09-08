/**
 * "The chat engine preference changed" — announced across documents (#367).
 *
 * Its own module so the Settings page can announce a change without importing
 * the chat components: `chat-engine.tsx` pulls in both engines, and Settings
 * has no reason to ship either.
 *
 * BroadcastChannel rather than a `storage` event, because the preference lives
 * server-side and there is no localStorage write to observe. It matters
 * because the panel JupyterLab iframes is a separate document with its own
 * query cache: without this it keeps running the engine the user just switched
 * away from — including an event stream left open — until that iframe is torn
 * down.
 */

export const ENGINE_CHANNEL = "studio-chat-engine";

/** Tell every mounted chat panel, in any document, to re-read the preference. */
export function announceEngineChange(): void {
  try {
    const channel = new BroadcastChannel(ENGINE_CHANNEL);
    channel.postMessage("changed");
    channel.close();
  } catch {
    /* unsupported or blocked — the panel picks it up on its next mount */
  }
}

/**
 * Call `onChange` when another document announces an engine switch.
 * Returns an unsubscribe function, or undefined where BroadcastChannel is
 * unavailable.
 */
export function subscribeEngineChange(onChange: () => void): (() => void) | undefined {
  let channel: BroadcastChannel;
  try {
    channel = new BroadcastChannel(ENGINE_CHANNEL);
  } catch {
    return undefined;
  }
  channel.onmessage = () => onChange();
  return () => channel.close();
}
