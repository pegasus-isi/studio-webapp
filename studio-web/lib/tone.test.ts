import { describe, expect, it } from "vitest";
import { TONE_BADGE, statusTone } from "@/lib/tone";
import { statusColor } from "@/lib/utils";

describe("statusTone", () => {
  it("gives running the brand tone, not a verdict colour", () => {
    expect(statusTone("running")).toBe("active");
  });

  it("treats installed as healthy, not off (#548)", () => {
    expect(statusTone("installed")).toBe("ok");
    expect(statusTone("succeeded")).toBe("ok");
  });

  it("puts waiting and held states in warn", () => {
    for (const s of ["planning", "planned", "pending", "queued", "held"]) {
      expect(statusTone(s)).toBe("warn");
    }
  });

  it("maps failures to error and unknown states to off", () => {
    expect(statusTone("failed")).toBe("error");
    expect(statusTone("draft")).toBe("off");
    expect(statusTone("something-new")).toBe("off");
  });
});

describe("statusColor", () => {
  it("draws badges from the shared palette", () => {
    expect(statusColor("running")).toBe(TONE_BADGE.active);
    expect(statusColor("installed")).toBe(TONE_BADGE.ok);
  });

  it("keeps generated as its own lifecycle colour", () => {
    expect(statusColor("generated")).toContain("violet");
  });
});
