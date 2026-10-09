import { describe, expect, it } from "vitest";
import {
  compareCells,
  detectDelimiter,
  isNumeric,
  numericColumns,
  parseCsv,
} from "./csv";

describe("parseCsv", () => {
  it("keeps a quoted comma inside its field (the USGS catalog case)", () => {
    const text =
      'time,mag,place,type\n2026-10-01,3.2,"10 km NE of Ridgecrest, CA",earthquake\n';
    const { rows } = parseCsv(text);
    expect(rows).toEqual([
      ["time", "mag", "place", "type"],
      ["2026-10-01", "3.2", "10 km NE of Ridgecrest, CA", "earthquake"],
    ]);
  });

  it("handles doubled quotes, line breaks in quotes, CRLF and blank lines", () => {
    const text = 'a,b\r\n"say ""hi""","line1\nline2"\r\n\r\n3,4';
    expect(parseCsv(text).rows).toEqual([
      ["a", "b"],
      ['say "hi"', "line1\nline2"],
      ["3", "4"],
    ]);
  });

  it("stops at maxRows and says so", () => {
    const text = "h\n" + "1\n".repeat(10);
    const { rows, truncated } = parseCsv(text, ",", 3);
    expect(rows).toHaveLength(3);
    expect(truncated).toBe(true);
  });

  it("keeps a final row cut mid-quote rather than dropping it", () => {
    const { rows } = parseCsv('a,b\n1,"unfinished');
    expect(rows[1]).toEqual(["1", "unfinished"]);
  });
});

describe("detectDelimiter", () => {
  it("prefers the delimiter that splits the header most, ignoring quotes", () => {
    expect(detectDelimiter('a,"b;c",d\n')).toBe(",");
    expect(detectDelimiter("a\tb\tc\n1\t2\t3")).toBe("\t");
    expect(detectDelimiter("a;b;c\n")).toBe(";");
  });
  it("trusts a .tsv name", () => {
    expect(detectDelimiter("a,b,c", "x.tsv")).toBe("\t");
  });
});

describe("sorting", () => {
  it("sorts numbers numerically and text naturally, empties last", () => {
    expect(["10", "9", "", "1.5"].sort((a, b) => compareCells(a, b, true))).toEqual([
      "1.5",
      "9",
      "10",
      "",
    ]);
    expect(["run10", "run2"].sort((a, b) => compareCells(a, b, false))).toEqual([
      "run2",
      "run10",
    ]);
  });

  it("detects numeric columns from a sample", () => {
    const rows = [
      ["1", "a", "2e3"],
      ["2", "b", ""],
      ["-3.5", "c", "4"],
    ];
    expect(numericColumns(rows)).toEqual([true, false, true]);
    expect(isNumeric(" 42 ")).toBe(true);
    expect(isNumeric("4 2")).toBe(false);
  });
});
