import { describe, expect, it } from "vitest";
import { formatBytes, formatCount, formatDuration } from "@/lib/utils";

describe("formatCount", () => {
  it("adds thousands separators", () => {
    expect(formatCount(18234)).toBe((18234).toLocaleString());
    expect(formatCount(7)).toBe("7");
  });
  it("renders non-finite values as a dash", () => {
    expect(formatCount(Number.NaN)).toBe("—");
  });
});

describe("formatBytes", () => {
  it("keeps one decimal under ten and none above", () => {
    expect(formatBytes(512)).toBe("512 B");
    expect(formatBytes(1536)).toBe("1.5 KB");
    expect(formatBytes(50 * 1024 * 1024)).toBe("50 MB");
  });
});

describe("formatDuration", () => {
  it("is what the job tables now show instead of raw seconds", () => {
    expect(formatDuration(45)).toBe("45s");
    expect(formatDuration(3700)).toBe("1h 01m");
  });
});
