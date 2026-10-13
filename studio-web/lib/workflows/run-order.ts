/**
 * Runs, newest first (#502).
 *
 * By planning time, then run name, the same key the backend sorts by
 * (workflow_scanner.label_runs). Sorting here rather than trusting the
 * response's order is the point: the graph pane used to reverse the list on
 * the assumption it arrived oldest first, and when the backend began sending
 * it newest first (2c56feb) the pane quietly started showing the oldest run
 * as "(latest)".
 */
export interface Orderable {
  run_name: string;
  planned_at?: number;
}

export function newestFirst<T extends Orderable>(runs: readonly T[]): T[] {
  return [...runs].sort(
    (a, b) =>
      (b.planned_at ?? 0) - (a.planned_at ?? 0) ||
      b.run_name.localeCompare(a.run_name, undefined, { numeric: true })
  );
}
