// Whether the chat should keep following the tail of a streaming reply.
//
// The old rule decided on every streamed update: "within 160px of the bottom?
// then jump to the bottom". One wheel notch is about 100px, so the first move
// up still counted as "near the bottom" and was undone on the next frame, and
// a trackpad's first few pixels never escaped at all. The reader had to fight
// the stream to read anything above it.
//
// So the decision is made from the scroll events, which is where the reader's
// intent shows, and the stream only reads the result:
//
// - Any move up leaves follow mode, however small. Streaming appends at the
//   bottom and our own follow scrolls only ever move down, so a decrease in
//   scrollTop is the reader.
// - Except when it lands at the very bottom: the browser clamps scrollTop down
//   when content shrinks (a tool part collapsing), and that is not the reader
//   leaving.
// - Moving down to within a short distance of the bottom rejoins. While a
//   reply streams the bottom keeps moving, so demanding the exact bottom would
//   make rejoining by scrolling nearly impossible.

/** Distance from the bottom that counts as "at the bottom" outright. */
export const AT_BOTTOM_PX = 2;
/** Scrolling down to within this distance of the bottom rejoins the tail. */
export const REJOIN_PX = 32;

export interface ScrollSample {
  scrollTop: number;
  scrollHeight: number;
  clientHeight: number;
}

export function nextFollowing(
  following: boolean,
  lastScrollTop: number,
  s: ScrollSample
): boolean {
  const fromBottom = s.scrollHeight - s.scrollTop - s.clientHeight;
  if (fromBottom < AT_BOTTOM_PX) return true;
  if (s.scrollTop < lastScrollTop) return false;
  if (s.scrollTop > lastScrollTop && fromBottom < REJOIN_PX) return true;
  return following;
}
