import { useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { api } from "../api";
import { EmptyState, ErrorBanner, Loading, formatTime } from "../components";
import type { Task } from "../types";

/**
 * Everything the create_task tool has written.
 *
 * This page exists to make the approval rules checkable rather than taken on
 * trust: an approved action appears here exactly once, and a rejected one
 * never appears at all.
 */
export default function TasksPage() {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api
      .listTasks()
      .then(setTasks)
      .catch((e) => {
        setTasks([]);
        setError(e);
      });
  }, []);

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Tasks</h1>
          <p className="subtitle">
            Created by the <code>create_task</code> tool. This is the only data the agent can
            write, and only ever after a human approves it.
          </p>
        </div>
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {tasks === null ? (
        <Loading label="Loading tasks" />
      ) : tasks.length === 0 ? (
        <EmptyState
          title="No tasks have been created"
          description="When a run asks to create a task and you approve it, the result will appear here."
        />
      ) : (
        <div className="panel table-scroll">
          <table>
            <thead>
              <tr>
                <th>Title</th>
                <th>Assignee</th>
                <th>Created by</th>
                <th>When</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr key={task.id}>
                  <td>
                    <strong>{task.title}</strong>
                    {task.description && <div className="muted small">{task.description}</div>}
                  </td>
                  <td className="mono">{task.assignee}</td>
                  <td>
                    {task.created_by_execution_id ? (
                      <Link to={`/executions/${task.created_by_execution_id}`}>
                        run #{task.created_by_execution_id}
                      </Link>
                    ) : (
                      <span className="muted">—</span>
                    )}
                  </td>
                  <td className="small muted">{formatTime(task.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
