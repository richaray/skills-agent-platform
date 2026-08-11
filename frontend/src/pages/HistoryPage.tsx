import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { EmptyState, ErrorBanner, Loading, StatusBadge, formatTime } from "../components";
import type { Execution } from "../types";

export default function HistoryPage() {
  const [executions, setExecutions] = useState<Execution[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api
      .listExecutions()
      .then(setExecutions)
      .catch((e) => {
        setExecutions([]);
        setError(e);
      });
  }, []);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Run history</h1>
          <p className="subtitle">
            Every run is kept, including failures, refusals and cancelled runs. Nothing is deleted.
          </p>
        </div>
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {executions === null ? (
        <Loading label="Loading history" />
      ) : executions.length === 0 ? (
        <EmptyState
          title="No runs yet"
          description="Open a skill and run it with sample input. Every step the agent takes will be recorded here."
          action={
            <Link to="/">
              <button className="primary">Go to skills</button>
            </Link>
          }
        />
      ) : (
        <div className="panel table-scroll">
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Skill</th>
                <th>Status</th>
                <th>Steps</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {executions.map((execution) => (
                <tr key={execution.id}>
                  <td>
                    <Link to={`/executions/${execution.id}`}>#{execution.id}</Link>
                    {execution.rerun_of_execution_id && (
                      <div className="muted small">rerun of #{execution.rerun_of_execution_id}</div>
                    )}
                  </td>
                  <td>
                    {execution.skill_name ?? "—"}
                    <div className="muted small">v{execution.version_number}</div>
                  </td>
                  <td>
                    <StatusBadge status={execution.status} />
                  </td>
                  <td className="mono">
                    {execution.step_count}
                    {execution.max_steps ? ` / ${execution.max_steps}` : ""}
                  </td>
                  <td className="small muted">{formatTime(execution.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
