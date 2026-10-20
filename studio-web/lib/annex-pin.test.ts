import { describe, expect, it } from "vitest";
import { applyPin, pinState, pinValue } from "@/lib/annex-pin";

const project = { site: "compute", namespace: "condor", key: "+ProjectName", value: '"USC_Deelman"' };

describe("annex pin", () => {
  it("adds the requirements row and the runtime PATH the Expanse test needed", () => {
    const rows = applyPin([project], "compute", "kthare10", true, "/cm/local/apps/singularitypro/3.11/bin/");
    expect(rows).toEqual([
      project,
      { site: "compute", namespace: "condor", key: "requirements", value: 'TARGET.AnnexName =?= "kthare10"' },
      { site: "compute", namespace: "env", key: "PATH", value: "/cm/local/apps/singularitypro/3.11/bin:/usr/local/bin:/usr/bin:/bin" },
    ]);
    expect(pinState(rows, "compute", "kthare10")).toEqual({
      on: true,
      foreign: null,
      runtimeDir: "/cm/local/apps/singularitypro/3.11/bin",
    });
  });

  it("turning it off removes exactly what it added", () => {
    const on = applyPin([project], "compute", "kthare10", true, "/opt/apptainer/bin");
    expect(applyPin(on, "compute", "kthare10", false)).toEqual([project]);
  });

  it("never touches a requirements row the user wrote", () => {
    const mine = { site: "compute", namespace: "condor", key: "requirements", value: "Memory > 8000" };
    expect(pinState([mine], "compute", "a").foreign).toBe("Memory > 8000");
    expect(applyPin([mine], "compute", "a", true)).toEqual([mine]);
  });

  it("leaves the user's own PATH alone and skips a folder that is not a folder", () => {
    const path = { site: "compute", namespace: "env", key: "PATH", value: "/my/bin:/usr/bin" };
    const rows = applyPin([path], "compute", "a", true, "/x/bin");
    expect(rows.filter((r) => r.key === "PATH")).toEqual([path]);
    expect(applyPin([], "compute", "a", true, "relative/bin:/evil").some((r) => r.key === "PATH")).toBe(false);
    expect(applyPin([path], "compute", "a", false)).toEqual([path]);
    expect(pinValue("a")).toBe('TARGET.AnnexName =?= "a"');
  });
});
