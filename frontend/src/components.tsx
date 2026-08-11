/* Small shared UI pieces, all in one file so there are fewer places to look.

   The four states every screen needs - loading, empty, error, success - are
   represented here so that each page uses the same ones rather than inventing
   its own. */

import { useEffect, useState } from "react";

import { ApiError } from "./api";
import type { ExecutionStatus, Problem } from "./types";

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="loading">
      <span className="spinner" /> {label}...
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <h2>{title}</h2>
      <p className="subtitle" style={{ maxWidth: 460, margin: "0 auto 16px" }}>
        {description}
      </p>
      {action}
    </div>
  );
}

/** Shows an error and, when the backend supplied them, the specific problems. */
export function ErrorBanner({ error, onDismiss }: { error: unknown; onDismiss?: () => void }) {
  if (!error) return null;

  const message = error instanceof Error ? error.message : String(error);
  const problems = error instanceof ApiError ? error.problems : [];

  return (
    <div className="banner banner-error">
      <div className="spread">
        <strong>{message}</strong>
        {onDismiss && (
          <button className="small" onClick={onDismiss}>
            Dismiss
          </button>
        )}
      </div>
      {problems.length > 0 && (
        <ul>
          {problems.map((p, i) => (
            <li key={i}>
              <code>{p.field}</code> — {p.message}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** Validation problems for a skill definition, split into errors and warnings. */
export function ProblemList({ problems }: { problems: Problem[] }) {
  if (problems.length === 0) {
    return (
      <div className="banner banner-ok">
        <strong>This definition is valid</strong> and can be published.
      </div>
    );
  }

  const errors = problems.filter((p) => p.severity === "error");
  const warnings = problems.filter((p) => p.severity === "warning");

  return (
    <>
      {errors.length > 0 && (
        <div className="banner banner-error">
          <strong>
            {errors.length} {errors.length === 1 ? "error" : "errors"} — this cannot be published
            or run yet
          </strong>
          <ul>
            {errors.map((p, i) => (
              <li key={i}>
                <code>{p.field}</code> — {p.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      {warnings.length > 0 && (
        <div className="banner banner-warn">
          <strong>
            {warnings.length} {warnings.length === 1 ? "warning" : "warnings"} — allowed, but worth
            checking
          </strong>
          <ul>
            {warnings.map((p, i) => (
              <li key={i}>
                <code>{p.field}</code> — {p.message}
              </li>
            ))}
          </ul>
        </div>
      )}
    </>
  );
}

const STATUS_STYLES: Record<string, { className: string; label: string }> = {
  running: { className: "badge-blue", label: "Running" },
  awaiting_approval: { className: "badge-amber", label: "Awaiting approval" },
  completed: { className: "badge-green", label: "Completed" },
  failed: { className: "badge-red", label: "Failed" },
  cancelled: { className: "badge-grey", label: "Cancelled" },
  max_steps_exceeded: { className: "badge-amber", label: "Step limit reached" },
  draft: { className: "badge-grey", label: "Draft" },
  published: { className: "badge-green", label: "Published" },
  archived: { className: "badge-grey", label: "Archived" },
  pending: { className: "badge-amber", label: "Pending" },
  approved: { className: "badge-green", label: "Approved" },
  rejected: { className: "badge-red", label: "Rejected" },
};

export function StatusBadge({ status }: { status: ExecutionStatus | string }) {
  const style = STATUS_STYLES[status] ?? { className: "badge-grey", label: status };
  return <span className={`badge ${style.className}`}>{style.label}</span>;
}

/** Pretty-printed JSON, or a muted dash when there is nothing to show. */
export function Json({ value }: { value: unknown }) {
  if (value === null || value === undefined) {
    return <span className="muted">—</span>;
  }
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}

/**
 * A textarea that holds JSON.
 *
 * It keeps the raw text the user typed rather than reformatting on every
 * keystroke, so editing is not fought against, and reports a parse error
 * underneath instead of silently discarding invalid input.
 */
export function JsonField({
  label,
  hint,
  value,
  onChange,
  rows = 8,
  disabled = false,
}: {
  label: string;
  hint?: string;
  value: unknown;
  onChange: (parsed: any, isValid: boolean) => void;
  rows?: number;
  disabled?: boolean;
}) {
  const [text, setText] = useState(() => JSON.stringify(value ?? {}, null, 2));
  const [error, setError] = useState<string | null>(null);

  // If the parent loads new data (for example after switching versions),
  // refresh the text - but only when the editor is not mid-edit.
  useEffect(() => {
    setText(JSON.stringify(value ?? {}, null, 2));
    setError(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [JSON.stringify(value ?? {})]);

  function handleChange(next: string) {
    setText(next);
    try {
      const parsed = JSON.parse(next);
      setError(null);
      onChange(parsed, true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Invalid JSON");
      onChange(null, false);
    }
  }

  return (
    <div className="field">
      <label>{label}</label>
      {hint && <div className="hint">{hint}</div>}
      <textarea
        className="code"
        rows={rows}
        value={text}
        disabled={disabled}
        onChange={(e) => handleChange(e.target.value)}
        style={error ? { borderColor: "var(--danger)" } : undefined}
      />
      {error && (
        <div className="small" style={{ color: "var(--danger)", marginTop: 4 }}>
          Not valid JSON: {error}
        </div>
      )}
    </div>
  );
}

/** Formats a timestamp for display, tolerating nulls. */
export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString();
}
