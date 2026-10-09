/**
 * CSV/TSV parsing for the table preview (#460).
 *
 * The preview used to split each line on the delimiter, which breaks on the
 * first quoted comma: earthquake's USGS catalog has a `place` column like
 * "10 km NE of Ridgecrest, CA", so every later column shifted by one. This is
 * an RFC 4180 reader — quoted fields, doubled quotes, line breaks inside
 * quotes — small enough to own rather than add a dependency for.
 */

/** The delimiter that splits the first line into the most fields, ignoring
 *  anything inside quotes. `.tsv` is always tab. */
export function detectDelimiter(text: string, name = ""): string {
  if (/\.tsv$/i.test(name)) return "\t";
  const firstLine = text.slice(0, text.search(/\r?\n/) >>> 0 || text.length);
  let best = ",";
  let bestCount = 0;
  for (const d of [",", "\t", ";", "|"]) {
    let count = 0;
    let quoted = false;
    for (const ch of firstLine) {
      if (ch === '"') quoted = !quoted;
      else if (ch === d && !quoted) count++;
    }
    if (count > bestCount) {
      best = d;
      bestCount = count;
    }
  }
  return best;
}

/**
 * Rows of fields. Stops after `maxRows` so a huge file cannot stall the tab;
 * `truncated` says whether it did. A final line cut mid-quote (the end of a
 * partial preview) is kept as whatever was read, never thrown away silently.
 */
export function parseCsv(
  text: string,
  delimiter = ",",
  maxRows = 50_000
): { rows: string[][]; truncated: boolean } {
  const rows: string[][] = [];
  let row: string[] = [];
  let field = "";
  let quoted = false;
  let i = 0;
  const n = text.length;

  const endRow = () => {
    row.push(field);
    field = "";
    // Blank lines are not rows.
    if (!(row.length === 1 && row[0] === "")) rows.push(row);
    row = [];
  };

  while (i < n) {
    const ch = text[i];
    if (quoted) {
      if (ch === '"') {
        if (text[i + 1] === '"') {
          field += '"';
          i += 2;
          continue;
        }
        quoted = false;
      } else {
        field += ch;
      }
      i++;
      continue;
    }
    if (ch === '"' && field === "") {
      quoted = true;
    } else if (ch === delimiter) {
      row.push(field);
      field = "";
    } else if (ch === "\n" || ch === "\r") {
      endRow();
      if (rows.length >= maxRows) return { rows, truncated: i + 1 < n };
      if (ch === "\r" && text[i + 1] === "\n") i++;
    } else {
      field += ch;
    }
    i++;
  }
  if (field !== "" || row.length > 0) endRow();
  return { rows, truncated: false };
}

const NUMERIC = /^[-+]?(\d+\.?\d*|\.\d+)([eE][-+]?\d+)?$/;

export function isNumeric(cell: string): boolean {
  return NUMERIC.test(cell.trim());
}

/** Columns whose non-empty cells are (nearly) all numbers: right-aligned and
 *  sorted as numbers. Judged on a sample so a header row of thousands of
 *  lines doesn't cost a full scan. */
export function numericColumns(rows: string[][], sample = 200): boolean[] {
  const width = Math.max(0, ...rows.slice(0, sample).map((r) => r.length));
  const out: boolean[] = [];
  for (let c = 0; c < width; c++) {
    let filled = 0;
    let numeric = 0;
    for (const r of rows.slice(0, sample)) {
      const v = (r[c] ?? "").trim();
      if (!v) continue;
      filled++;
      if (isNumeric(v)) numeric++;
    }
    out.push(filled > 0 && numeric / filled >= 0.9);
  }
  return out;
}

/** Sort comparison: numbers as numbers, text naturally ("run2" < "run10"),
 *  empty cells last whichever way the column is sorted. */
export function compareCells(a: string, b: string, numeric: boolean): number {
  const ea = a.trim() === "";
  const eb = b.trim() === "";
  if (ea || eb) return ea === eb ? 0 : ea ? 1 : -1;
  if (numeric && isNumeric(a) && isNumeric(b)) return Number(a) - Number(b);
  return a.localeCompare(b, undefined, { numeric: true, sensitivity: "base" });
}
