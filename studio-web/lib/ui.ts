/**
 * Shared class strings for the things every screen draws (#554), so the
 * same element stops looking different from page to page.
 *
 * - CARD: a content card or list. Controls, menus, popovers and code blocks
 *   keep rounded-lg; content gets rounded-xl, as on the Resources page.
 * - EYEBROW: the small uppercase label above a group (chat, command palette,
 *   tour, job files). There were a dozen copies at three sizes and two
 *   trackings.
 */

export const CARD = "rounded-xl border border-line bg-surface";

export const EYEBROW = "text-2xs font-semibold uppercase tracking-wider text-fgsubtle";
