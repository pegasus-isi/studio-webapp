import { describe, expect, it } from "vitest";
import { nextFollowing } from "./scroll-follow";

// A 500px viewport over 2000px of transcript: the bottom is scrollTop 1500.
const at = (scrollTop: number, scrollHeight = 2000) => ({
  scrollTop,
  scrollHeight,
  clientHeight: 500,
});

describe("nextFollowing", () => {
  it("lets go on the first small move up, well inside the old 160px zone", () => {
    // The reported bug: one wheel notch up was still "near the bottom" and
    // was scrolled straight back down by the next streamed token.
    expect(nextFollowing(true, 1500, at(1400))).toBe(false);
    expect(nextFollowing(true, 1500, at(1497))).toBe(false);
  });

  it("stays let go while content grows underneath the reader", () => {
    // Streaming grows scrollHeight without moving scrollTop; no scroll event
    // fires, but if one does (layout), it must not rejoin on its own.
    expect(nextFollowing(false, 1400, at(1400, 2600))).toBe(false);
  });

  it("rejoins when the reader scrolls back down near the bottom", () => {
    expect(nextFollowing(false, 1400, at(1480))).toBe(true);
    expect(nextFollowing(false, 1400, at(1450))).toBe(false);
  });

  it("rejoins at the bottom however it got there", () => {
    expect(nextFollowing(false, 0, at(1500))).toBe(true);
  });

  it("does not treat a clamp from shrinking content as the reader leaving", () => {
    // Content shrank from 2000 to 1800, so the browser pulled scrollTop from
    // 1500 down to the new bottom, 1300.
    expect(nextFollowing(true, 1500, at(1300, 1800))).toBe(true);
  });

  it("keeps following through its own scrolls to the bottom", () => {
    expect(nextFollowing(true, 1500, at(2100, 2600))).toBe(true);
  });
});
