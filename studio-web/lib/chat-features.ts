/**
 * Chat composer feature flags.
 *
 * Branch bot-mode: the Pegasus bot server owns model and provider selection,
 * so the composer hides those dropdowns. Main keeps them visible.
 *
 * Hide, don't delete: every component, the store shape, and the send logic
 * stay intact — these flags only gate rendering (and the send-guards in
 * chat-input.tsx), so future main merges touch at most the guard lines.
 * Main defaults would both be `true`. Bot-mode target is both `false`.
 */
export const chatFeatures = {
  showProviderSelect: false,
  showModelSelect: false,
} as const;
