"use client";

/**
 * Shared parameter field for the run form.
 *
 * Renders from the Phase 3 hints (#241) when the backend supplies them — date
 * pickers, numeric inputs, file/folder pickers — and from nothing but the flag
 * name when it doesn't.
 *
 * ## The hints are advisory, always
 *
 * Type inference reads argparse's `type=` where it can and guesses from the
 * parameter's *name* where it can't, because `type=str` says nothing about
 * whether a string is a path, a date, or free text. Guessing from names is
 * right most of the time and wrong some of the time, so every widget here has
 * a way to type an arbitrary value: the file picker keeps its text box, the
 * date field accepts free text when the value isn't ISO, and an unrecognised
 * type falls through to a plain input.
 *
 * argparse remains the validator. A wrong guess must cost the user a moment,
 * never their ability to run the workflow.
 */

import type { WorkflowParam } from "@/lib/api/client";
import { FilePicker } from "@/components/workflows/file-picker";

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
      if (s === "") continue;
      if (p.multiple) {
        // argparse wants one token per value. Commas are accepted as well as
        // spaces because "1,7,30" is what people type into a single field.
        args.push(p.flag, ...s.split(/[\s,]+/).filter(Boolean));
      } else {
        args.push(p.flag, s);
      }
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

/**
 * Human label: the backend's inferred one (from the author's help text) when
 * present, else the flag with its dashes stripped.
 */
export function paramLabel(param: WorkflowParam): string {
  if (param.label) return param.label;
  return param.flag.replace(/^-+/, "").replace(/-/g, " ");
}

/** True for plumbing the backend flagged as not-the-science. */
export function isAdvancedParam(param: WorkflowParam): boolean {
  return param.advanced === true;
}

/** ISO date (what <input type="date"> requires) — anything else stays text. */
function isIsoDate(value: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(value);
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
  const id = `param-${param.dest}`;
  // Show the author's help as the description only when it isn't already the
  // label — otherwise the same sentence appears twice.
  const description = param.help && param.help !== param.label ? param.help : "";
  const helpId = description ? `${id}-help` : undefined;
  const text = String(value ?? "");

  // An empty-string default is *no* default: rendering it as "default: "
  // promises a value and then shows none, which reads as a broken field.
  const hasDefault =
    param.default != null && String(param.default).trim() !== "";
  const placeholder = hasDefault
    ? `default: ${param.default}`
    : param.required
      ? "required"
      : "optional";

  if (param.is_flag || param.type === "boolean") {
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

  const field = (() => {
    if (param.choices) {
      return (
        <select
          id={id}
          value={text}
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
      );
    }

    if (param.type === "file" || param.type === "directory") {
      return (
        <FilePicker
          id={id}
          describedBy={helpId}
          mode={param.type}
          value={text}
          placeholder={placeholder}
          onChange={onChange}
        />
      );
    }

    if (param.type === "integer" || param.type === "number") {
      return (
        <input
          id={id}
          type="number"
          step={param.type === "integer" ? 1 : "any"}
          value={text}
          aria-describedby={helpId}
          aria-required={param.required || undefined}
          placeholder={placeholder}
          onChange={(e) => onChange(e.target.value)}
          className="rounded-md border border-line px-2 py-1 text-sm"
        />
      );
    }

    // A date input silently discards a value it can't parse, which would eat
    // whatever the generator actually wanted (a relative date, a keyword like
    // "latest", a range). So only use the picker once the value already looks
    // like a date, and let anything else stay editable as text.
    if (param.type === "date" && (text === "" || isIsoDate(text))) {
      return (
        <input
          id={id}
          type="date"
          value={text}
          aria-describedby={helpId}
          aria-required={param.required || undefined}
          onChange={(e) => onChange(e.target.value)}
          className="rounded-md border border-line px-2 py-1 text-sm"
        />
      );
    }

    return (
      <input
        id={id}
        type="text"
        value={text}
        aria-describedby={helpId}
        aria-required={param.required || undefined}
        // Prefix so a grayed default reads as guidance, not a filled value.
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        // Not monospace: a text parameter is usually a name or an ID, not
        // code; file and directory parameters have their own picker (#550).
        className="rounded-md border border-line px-2 py-1 text-sm"
      />
    );
  })();

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
      {field}
      {description && (
        <span id={helpId} className="text-xs text-fgsubtle">
          {description}
        </span>
      )}
    </div>
  );
}
