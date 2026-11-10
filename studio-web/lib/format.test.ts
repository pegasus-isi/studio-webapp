import { describe, expect, it } from "vitest";
import {
  formatBytes,
  formatCount,
  formatDuration,
  formatExitCode,
} from "@/lib/utils";

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

describe("formatExitCode", () => {
  it("shows an ordinary exit code as the number", () => {
    expect(formatExitCode(0)).toBe("0");
    expect(formatExitCode(137)).toBe("137");
    expect(formatExitCode("1")).toBe("1");
  });
  it("names the signal instead of showing a negative number", () => {
    expect(formatExitCode(-9)).toBe("signal 9");
    expect(formatExitCode(-128)).toBe("unknown");
  });
  it("renders a missing or unreadable code without breaking", () => {
    expect(formatExitCode(null)).toBe("\u2014");
    expect(formatExitCode(undefined)).toBe("\u2014");
    expect(formatExitCode("n/a")).toBe("n/a");
  });
});
