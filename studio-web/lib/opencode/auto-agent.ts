/**
 * "Auto" mode: pick the specialist agent for a message, per message.
 *
 * Deterministic rules on purpose. There are three specialists, and choosing
 * among three labels does not justify an embedding call (latency, plus a
 * gateway that may not serve embeddings) or a router LLM call on every send.
 * The choice is shown on the reply, so a wrong pick is visible and the user
 * can pin a mode instead. If the rules prove too coarse, replace `routeAgent`
 * (an in-memory nearest-description match, or one small classifier call);
 * callers depend only on its signature.
 *
 * Order matters, and each step exists because the one after it misfired:
 *
 * 1. A request to create or change a workflow goes to the architect even when
 *    it mentions failure or data, provided it is phrased as a request (see
 *    REQUEST): a failure report that merely names a workflow part is not. Creation requests are full of both ("if a
 *    gauge fails, note it", "fetch daily CSV from USGS"), and routing those
 *    to the debugger or data engineer took the design away from the agent
 *    that owns it. That holds with a broken run in context too: "add a
 *    plotting job" is design, whatever state the last run is in.
 * 2. Otherwise a run in context that has failed or is held makes it a
 *    debugging question, whatever the words: "what now?" about a broken run
 *    means "fix it".
 * 3. Failure words, then data words, for everything else.
 * 4. No signal at all ("yes, do that") keeps the agent that answered last:
 *    a follow-up belongs to the conversation it follows.
 */

export const ARCHITECT = "workflow-architect";
export const DEBUGGER = "pipeline-debugger";
export const DATA_ENGINEER = "data-engineer";

export interface RouteInput {
  /** What the user typed. */
  text: string;
  /** The Active Context block sent with the turn, if any. */
  scopeBlock?: string | null;
  /** The agent that answered the previous turn, if any. */
  previous?: string | null;
  /** Agents the engine actually has; a pick outside this set is not made. */
  available: readonly string[];
}

export interface Route {
  /** Agent name, or undefined for the engine's default. */
  agent: string | undefined;
  /** Short human reason, for the tooltip and logs. */
  reason: string;
}

// The block is built by studio-api services/chat_scope.py; these match its
// "- Status: <status>", "N failed" and "### Diagnosed failures" lines.
const SCOPE_BROKEN = [
  /^- Status: *(failed|failure|held|error)/im,
  /\b[1-9]\d* failed\b/,
  /^### Diagnosed failures/m,
];

// A creation verb is not enough on its own: "why does the build fail?",
// "write the CSV to S3" and "connection refused on port 8080" all contain one
// and are debugging or data questions. So the verb must govern something a
// workflow is made of, within the same clause.
const CREATE_VERB =
  "(create|build|write|design|scaffold|generate|set ?up|make|convert|port|add|extend|modify|change|update)";
const CREATE_OBJECT =
  "(workflows?|pipelines?|dags?|generators?|wrappers?|dockerfiles?|containers?|jobs?|steps?|stages?|tasks?|transformations?|catalogs?)";
const CREATE = new RegExp(
  `\\b${CREATE_VERB}\\b[^.?!\\n]{0,40}?\\b${CREATE_OBJECT}\\b|\\b(i'?d like|i want|i need|can you make)\\b[^.?!\\n]{0,40}?\\b(workflows?|pipelines?)\\b|\\bnew workflow\\b`,
  "i"
);

// Phrased as a request for something new. Needed only when a message carries
// both creation and failure language: "pegasus-plan failed to generate the
// DAG" names a workflow part after a creation verb and is a failure report,
// while "write a wrapper that retries on failure" and "I'd like a workflow
// that fails if..." are design requests that happen to mention failure.
const REQUEST = new RegExp(
  `^\\s*(please\\s+)?${CREATE_VERB}\\b|\\b(i'?d like|i want|i need)\\b|\\b(can|could|would) you (please )?${CREATE_VERB}\\b`,
  "i"
);

const DEBUG =
  /\b(fail(ed|s|ing|ure)?|errors?|exception|traceback|held|on hold|hold reason|stuck|hang(s|ing)?|crash(ed|es)?|broken|debug|not working|doesn'?t work|won'?t (run|start|submit)|can'?t|cannot|unable to|exit(s|ed)? (with )?(code|status)|exit code|non-?zero|refused|denied|timed? ?out|killed|oom|segfault|no such file|not found|pegasus-analyzer|why did)\b/i;

const DATA =
  /\b(datasets?|download|fetch|ingest|api|endpoint|csv|tsv|json|netcdf|hdf5|parquet|preprocess(ing)?|clean(ing)? (the )?data|schema|file formats?|s3|bucket|data source)\b/i;

export function routeAgent(input: RouteInput): Route {
  const has = (name: string) => input.available.includes(name);
  const pick = (name: string, reason: string): Route | null =>
    has(name) ? { agent: name, reason } : null;

  const block = input.scopeBlock ?? "";
  const text = input.text;

  const routed =
    (CREATE.test(text) &&
      (!DEBUG.test(text) || REQUEST.test(text)) &&
      pick(ARCHITECT, "asks to create or change a workflow")) ||
    (SCOPE_BROKEN.some((re) => re.test(block)) &&
      pick(DEBUGGER, "the run in context has failures")) ||
    (DEBUG.test(text) && pick(DEBUGGER, "mentions a failure")) ||
    (DATA.test(text) && pick(DATA_ENGINEER, "about data acquisition or formats")) ||
    (input.previous && pick(input.previous, "follow-up to the previous reply")) ||
    pick(ARCHITECT, "default");

  return routed || { agent: undefined, reason: "no specialist available" };
}
