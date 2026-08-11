import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { EmptyState, ErrorBanner, Json, Loading, formatTime } from "../components";
import type { Approval } from "../types";

/**
 * The inbox of write actions waiting on a human.
 *
 * Approving here resumes the paused run, which can take several seconds
 * because the agent continues working after the action completes. The button
 * shows that state rather than appearing to hang.
 */
export default function ApprovalsPage() {
  const [approvals, setApprovals] = useState<Approval[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  function load() {
    api
      .listApprovals("pending")
      .then(setApprovals)
      .catch((e) => {
        setApprovals([]);
        setError(e);
      });
  }

  useEffect(load, []);

  async function decide(approval: Approval, action: "approve" | "reject") {
    setBusyId(approval.id);
    setError(null);
    setNotice(null);
    try {
      if (action === "approve") {
        await api.approve(approval.id);
        setNotice(`Approved. Run #${approval.execution_id} has continued.`);
      } else {
        await api.reject(approval.id, "Rejected from the approvals inbox.");
        setNotice(`Rejected. Run #${approval.execution_id} continued without that action.`);
      }
      load();
    } catch (e) {
      setError(e);
    } finally {
      setBusyId(null);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Approvals</h1>
          <p className="subtitle">
            Runs pause here before any action that changes data. Nothing is written until you
            approve it.
          </p>
        </div>
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />
      {notice && (
        <div className="banner banner-ok">
          <div className="spread">
            <span>{notice}</span>
            <button className="small" onClick={() => setNotice(null)}>
              Dismiss
            </button>
          </div>
        </div>
      )}

      {approvals === null ? (
        <Loading label="Loading approvals" />
      ) : approvals.length === 0 ? (
        <EmptyState
          title="Nothing is waiting for you"
          description="When a run wants to perform a write action, it will pause and appear here for your decision."
        />
      ) : (
        approvals.map((approval) => (
          <div key={approval.id} className="panel">
            <div className="spread" style={{ marginBottom: 12 }}>
              <div>
                <h2 style={{ marginBottom: 2 }}>
                  <code>{approval.tool_name}</code>
                </h2>
                <div className="subtitle">
                  {approval.skill_name} v{approval.version_number} ·{" "}
                  <Link to={`/executions/${approval.execution_id}`}>
                    run #{approval.execution_id}
                  </Link>{" "}
                  · step {approval.step_number} · requested {formatTime(approval.created_at)}
                </div>
              </div>
              <span className="badge badge-amber">Waiting for you</span>
            </div>

            <h3>What the agent wants to do</h3>
            <Json value={approval.tool_input} />

            <div className="row" style={{ marginTop: 14 }}>
              <button
                className="approve"
                disabled={busyId === approval.id}
                onClick={() => decide(approval, "approve")}
              >
                {busyId === approval.id ? "Working..." : "Approve and continue"}
              </button>
              <button
                className="danger"
                disabled={busyId === approval.id}
                onClick={() => decide(approval, "reject")}
              >
                Reject
              </button>
              {busyId === approval.id && (
                <span className="muted small">
                  <span className="spinner" /> The run continues after this action, so it may take
                  a few seconds.
                </span>
              )}
            </div>
          </div>
        ))
      )}
    </>
  );
}
