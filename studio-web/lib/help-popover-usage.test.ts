import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/**
 * A HelpPopover is a <button>. Inside a <label> it becomes the label's
 * control (the first labelable descendant), so the field loses its
 * accessible name and clicking the label no longer focuses it (#590).
 * There is no DOM in this suite, so check the sources: no HelpPopover may
 * open between a <label> and its </label>.
 */
function tsxFiles(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return name === "node_modules" ? [] : tsxFiles(path);
    return path.endsWith(".tsx") ? [path] : [];
  });
}

describe("HelpPopover placement", () => {
  it("is never inside a <label>", () => {
    const offenders: string[] = [];
    for (const file of [...tsxFiles(join(__dirname, "..", "components")), ...tsxFiles(join(__dirname, "..", "app"))]) {
      const src = readFileSync(file, "utf8");
      const re = /<label\b[^>]*>([\s\S]*?)<\/label>/g;
      for (let m = re.exec(src); m; m = re.exec(src)) {
        if (m[1].includes("<HelpPopover")) offenders.push(file);
      }
    }
    expect(offenders).toEqual([]);
  });
});
