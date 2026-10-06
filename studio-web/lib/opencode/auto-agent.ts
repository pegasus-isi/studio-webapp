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
 *    it mentions failure or data. Creation requests are full of both ("if a
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

const CREATE =
  /\b(create|build|write|design|scaffold|generate|set ?up|make (me )?an?|i'?d like an?|i want an?|new workflow|add (an? )?(\w+ )?(job|step|stage|task)s?|(change|modify|update|extend) the (workflow|dag|generator|pipeline)|convert|port)\b/i;

const DEBUG =
  /\b(fail(ed|s|ing|ure)?|errors?|exception|traceback|held|on hold|hold reason|stuck|hang(s|ing)?|crash(ed|es)?|broken|debug|not working|doesn'?t work|exit code|pegasus-analyzer|why did)\b/i;

const DATA =
  /\b(datasets?|download|fetch|ingest|api|endpoint|csv|tsv|json|netcdf|hdf5|parquet|preprocess(ing)?|clean(ing)? (the )?data|schema|file formats?|s3|bucket|data source)\b/i;

export function routeAgent(input: RouteInput): Route {
  const has = (name: string) => input.available.includes(name);
  const pick = (name: string, reason: string): Route | null =>
    has(name) ? { agent: name, reason } : null;

  const block = input.scopeBlock ?? "";
  const text = input.text;

  const routed =
    (CREATE.test(text) && pick(ARCHITECT, "asks to create or change a workflow")) ||
    (SCOPE_BROKEN.some((re) => re.test(block)) &&
      pick(DEBUGGER, "the run in context has failures")) ||
    (DEBUG.test(text) && pick(DEBUGGER, "mentions a failure")) ||
    (DATA.test(text) && pick(DATA_ENGINEER, "about data acquisition or formats")) ||
    (input.previous && pick(input.previous, "follow-up to the previous reply")) ||
    pick(ARCHITECT, "default");

  return routed || { agent: undefined, reason: "no specialist available" };
}
