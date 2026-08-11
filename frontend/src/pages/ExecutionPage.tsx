import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { ErrorBanner, Json, Loading, StatusBadge, formatTime } from "../components";
import type { Execution, ExecutionStep } from "../types";

/** Picks the left-border colour and heading for one step in the timeline. */
function describeStep(step: ExecutionStep): { className: string; title: string } {
  if (step.kind === "final_output") return { className: "step-final", title: "Final answer" };
  if (step.kind === "error") return { className: "step-error", title: "Error" };
  if (step.kind === "tool_call") {
    if (step.error_message) {
      return { className: "step-error", title: `Tool call refused or failed: ${step.tool_name}` };
    }
    if (step.tool_output === null) {
      return { className: "step-approval", title: `Waiting for approval: ${step.tool_name}` };
    }
    return { className: "step-tool", title: `Tool call: ${step.tool_name}` };
  }
  return { className: "", title: step.kind };
}

export default function ExecutionPage() {
  const { executionId } = useParams();
  const id = Number(executionId);
  const navigate = useNavigate();

  const [execution, setExecution] = useState<Execution | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [rejectReason, setRejectReason] = useState("");

  function load() {
    return api
      .getExecution(id)
      .then((data) => {
        setExecution(data);
        setError(null);
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    load();
  }, [id]);

  async function act(label: string, action: () => Promise<unknown>) {
    setBusy(label);
    setError(null);
    try {
      await action();
      await load();
    } catch (e) {
      setError(e);
    } finally {
      setBusy(null);
    }
  }

  async function handleRerun() {
    setBusy("rerun");
    setError(null);
    try {
      const fresh = await api.rerun(id);
      navigate(`/executions/${fresh.id}`);
    } catch (e) {
      setError(e);
      setBusy(null);
    }
  }

  if (loading) return <Loading label="Loading run" />;

  if (!execution) {
    return (
      <>
        <ErrorBanner error={error} />
        <Link to="/history">Back to history</Link>
      </>
    );
  }

  const pending = execution.pending_approval;
  const canCancel = ["running", "awaiting_approval", "max_steps_exceeded"].includes(
    execution.status,
  );

  return (
    <>
      <div className="page-head">
        <div>
          <div className="small muted">
            <Link to="/history">History</Link> / run #{execution.id}
            {execution.rerun_of_execution_id && (
              <>
                {" "}
                · rerun of{" "}
                <Link to={`/executions/${execution.rerun_of_execution_id}`}>
                  #{execution.rerun_of_execution_id}
                </Link>
              </>
            )}
          </div>
          <h1>
            {execution.skill_name}{" "}
            <span className="muted">v{execution.version_number}</span>
          </h1>
          <p className="subtitle">
            Started {formatTime(execution.created_at)} · {execution.step_count} of{" "}
            {execution.max_steps} steps used
            {execution.finished_at ? ` · finished ${formatTime(execution.finished_at)}` : ""}
          </p>
        </div>
        <div className="row">
          <StatusBadge status={execution.status} />
          <button onClick={handleRerun} disabled={busy !== null}>
            {busy === "rerun" ? "Rerunning..." : "Rerun this version"}
          </button>
          {canCancel && (
            <button
              className="danger"
              disabled={busy !== null}
              onClick={() => act("cancel", () => api.cancel(id))}
            >
              {busy === "cancel" ? "Cancelling..." : "Cancel run"}
            </button>
          )}
        </div>
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {/* The approval gate. Nothing has been written at this point. */}
      {pending && (
        <div className="panel" style={{ borderColor: "var(--warn)", borderWidth: 2 }}>
          <div className="spread" style={{ marginBottom: 12 }}>
            <h2 style={{ margin: 0 }}>This run is paused and needs your decision</h2>
            <span className="badge badge-amber">Approval required</span>
          </div>
          <p className="small" style={{ marginTop: 0 }}>
            The agent wants to call <code>{pending.tool_name}</code>, which changes data. Nothing
            has been written yet.
          </p>

          <h3>Proposed action</h3>
          <Json value={pending.tool_input} />

          <div className="field" style={{ marginTop: 14 }}>
            <label htmlFor="reject-reason">Reason (optional, sent to the agent if you reject)</label>
            <input
              id="reject-reason"
              type="text"
              value={rejectReason}
              onChange={(e) => setRejectReason(e.target.value)}
              placeholder="The amount looks wrong."
            />
          </div>

          <div className="row">
            <button
              className="approve"
              disabled={busy !== null}
              onClick={() => act("approve", () => api.approve(pending.id))}
            >
              {busy === "approve" ? "Approving..." : "Approve and continue"}
            </button>
            <button
              className="danger"
              disabled={busy !== null}
              onClick={() => act("reject", () => api.reject(pending.id, rejectReason))}
            >
              {busy === "reject" ? "Rejecting..." : "Reject"}
            </button>
            {busy && (
              <span className="muted small">
                <span className="spinner" /> The agent carries on working after your decision.
              </span>
            )}
          </div>

          <p className="muted small" style={{ marginBottom: 0, marginTop: 12 }}>
            Duplicate protection key: <code>{pending.idempotency_key}</code>
          </p>
        </div>
      )}

      {execution.error_message && (
        <div className="banner banner-error">
          <strong>This run stopped early.</strong> {execution.error_message}
        </div>
      )}

      {execution.status === "completed" && execution.final_output && (
        <div className="panel">
          <div className="spread" style={{ marginBottom: 10 }}>
            <h2 style={{ margin: 0 }}>Final output</h2>
            <span className="badge badge-green">Matched the output schema</span>
          </div>
          <Json value={execution.final_output} />
        </div>
      )}

      <div className="grid-2">
        <div className="panel">
          <h2>Input</h2>
          <Json value={execution.input_data} />
        </div>
        <div className="panel">
          <h2>Approvals</h2>
          {execution.approvals && execution.approvals.length > 0 ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Tool</th>
                    <th>Status</th>
                    <th>Ran</th>
                    <th>Decided</th>
                  </tr>
                </thead>
                <tbody>
                  {execution.approvals.map((approval) => (
                    <tr key={approval.id}>
                      <td className="mono">{approval.tool_name}</td>
                      <td>
                        <StatusBadge status={approval.status} />
                      </td>
                      <td>{approval.executed ? "yes" : "no"}</td>
                      <td className="small muted">{formatTime(approval.decided_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted small" style={{ margin: 0 }}>
              This run never asked to change anything, so no approval was needed.
            </p>
          )}
        </div>
      </div>

      <h2 style={{ marginTop: 8 }}>What the agent did</h2>

      {execution.steps && execution.steps.length > 0 ? (
        execution.steps.map((step) => {
          const { className, title } = describeStep(step);
          return (
            <div key={step.id} className={`step ${className}`}>
              <div className="step-head">
                <span className="step-number">{step.step_number}</span>
                <strong>{title}</strong>
                {step.duration_ms !== null && (
                  <span className="muted small">{step.duration_ms} ms</span>
                )}
              </div>

              {step.llm_text && (
                <>
                  <h3>Agent reasoning</h3>
                  <pre>{step.llm_text}</pre>
                </>
              )}

              {step.tool_input && (
                <>
                  <h3>Tool input</h3>
                  <Json value={step.tool_input} />
                </>
              )}

              {step.tool_output && (
                <>
                  <h3>Tool result</h3>
                  <Json value={step.tool_output} />
                </>
              )}

              {step.error_message && (
                <div className="banner banner-error" style={{ margin: "10px 0 0" }}>
                  {step.error_message}
                </div>
              )}
            </div>
          );
        })
      ) : (
        <div className="panel">
          <p className="muted small" style={{ margin: 0 }}>
            No steps were recorded for this run.
          </p>
        </div>
      )}
    </>
  );
}
