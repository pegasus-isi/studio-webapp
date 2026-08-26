"use client";

/**
 * Shared parameter field for the run form.
 *
 * Extracted from `workflow-project-detail.tsx` so the unified Run panel (#241,
 * Phase 1) and the existing Advanced view render identical inputs from one
 * implementation. Deliberately unchanged in behaviour by that move: typed
 * widgets (dates, file pickers, units) are Phase 3, and doing them here would
 * mean two form implementations diverging during the rollout.
 */

import type { WorkflowParam } from "@/lib/api/client";

export type ParamValues = Record<string, string | boolean>;

/** Turn form values into the flag-formatted argv the generator expects. */
export function buildArgs(
  params: WorkflowParam[],
  values: ParamValues
): string[] {
  const args: string[] = [];
  for (const p of params) {
    const v = values[p.dest];
    if (p.is_flag) {
      if (v === true) args.push(p.flag);
    } else {
      const s = (v ?? "").toString().trim();
      if (s !== "") args.push(p.flag, s);
    }
  }
  return args;
}

/**
 * Required parameters the user hasn't filled in, as display labels.
 *
 * Phase 1 gates Run on this so an incomplete form is unreachable rather than
 * an error after the fact: the old page let you press Run with nothing filled
 * and surfaced the failure as an argparse traceback in a black log pane.
 * Flags are excluded — an unchecked boolean is a real value, not a blank.
 */
export function missingRequired(
  params: WorkflowParam[],
  values: ParamValues
): string[] {
  return params
    .filter((p) => p.required && !p.is_flag)
    .filter((p) => String(values[p.dest] ?? "").trim() === "")
    .map(paramLabel);
}

/** Human-ish label for a parameter: the flag, minus dashes. */
export function paramLabel(param: WorkflowParam): string {
  return param.flag.replace(/^-+/, "").replace(/-/g, " ");
}

export function ParamField({
  param,
  value,
  onChange,
}: {
  param: WorkflowParam;
  value: string | boolean | undefined;
  onChange: (v: string | boolean) => void;
}) {
  const label = paramLabel(param);
  // Ties the <label> to its control for screen readers and click-to-focus.
  // The pre-extraction version relied on adjacent text, which left every field
  // in the run form unlabelled programmatically.
  const id = `param-${param.dest}`;
  const helpId = param.help ? `${id}-help` : undefined;

  if (param.is_flag) {
    return (
      <label htmlFor={id} className="flex items-center gap-2 text-sm text-fg">
        <input
          id={id}
          type="checkbox"
          checked={Boolean(value)}
          aria-describedby={helpId}
          onChange={(e) => onChange(e.target.checked)}
        />
        <span title={param.help}>{label}</span>
      </label>
    );
  }

  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={id} className="text-xs font-medium text-fgmuted">
        {label}
        {param.required && (
          <span className="ml-1 text-red-500" aria-hidden="true">
            *
          </span>
        )}
        {param.required && <span className="sr-only"> (required)</span>}
      </label>
      {param.choices ? (
        <select
          id={id}
          value={String(value ?? "")}
          aria-describedby={helpId}
          onChange={(e) => onChange(e.target.value)}
          className="rounded-md border border-line px-2 py-1 text-sm"
        >
          {param.choices.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={id}
          type="text"
          value={String(value ?? "")}
          aria-describedby={helpId}
          aria-required={param.required || undefined}
          // Prefix so a grayed default reads as guidance, not a filled value.
          placeholder={
            param.default == null
              ? param.required
                ? "required"
                : "optional"
              : `default: ${param.default}`
          }
          onChange={(e) => onChange(e.target.value)}
          className="rounded-md border border-line px-2 py-1 text-sm font-mono"
        />
      )}
      {param.help && (
        <span id={helpId} className="text-xs text-fgsubtle">
          {param.help}
        </span>
      )}
    </div>
  );
}
