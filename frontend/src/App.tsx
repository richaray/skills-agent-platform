import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";

import { api } from "./api";
import ApprovalsPage from "./pages/ApprovalsPage";
import ComparePage from "./pages/ComparePage";
import ExecutionPage from "./pages/ExecutionPage";
import HistoryPage from "./pages/HistoryPage";
import RunPage from "./pages/RunPage";
import SkillDetailPage from "./pages/SkillDetailPage";
import SkillsPage from "./pages/SkillsPage";
import TasksPage from "./pages/TasksPage";
import VersionEditorPage from "./pages/VersionEditorPage";
import type { Health } from "./types";

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [pendingCount, setPendingCount] = useState(0);

  // Checked once on load so we can warn about missing configuration up front
  // rather than letting the user discover it when a run fails.
  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth(null));
  }, []);

  // Keeps the badge on the Approvals tab current.
  useEffect(() => {
    function refresh() {
      api
        .listApprovals("pending")
        .then((list) => setPendingCount(list.length))
        .catch(() => undefined);
    }
    refresh();
    const timer = setInterval(refresh, 15000);
    return () => clearInterval(timer);
  }, []);

  return (
    <>
      <header className="app-header">
        <div className="app-header-inner">
          <span className="brand">Skills Agent Platform</span>
          <nav className="nav">
            <NavLink to="/" end>
              Skills
            </NavLink>
            <NavLink to="/approvals">
              Approvals{pendingCount > 0 ? ` (${pendingCount})` : ""}
            </NavLink>
            <NavLink to="/history">History</NavLink>
            <NavLink to="/tasks">Tasks</NavLink>
          </nav>
        </div>
      </header>

      <main className="page">
        {health && !health.llm_configured && (
          <div className="banner banner-warn">
            <strong>No AI provider key is configured on this server.</strong> You can create,
            edit, validate and compare skills, but running one will fail until{" "}
            <code>GEMINI_API_KEY</code> is set.
          </div>
        )}

        <Routes>
          <Route path="/" element={<SkillsPage />} />
          <Route path="/skills/:skillId" element={<SkillDetailPage />} />
          <Route path="/skills/:skillId/compare" element={<ComparePage />} />
          <Route path="/versions/:versionId/edit" element={<VersionEditorPage />} />
          <Route path="/versions/:versionId/run" element={<RunPage />} />
          <Route path="/executions/:executionId" element={<ExecutionPage />} />
          <Route path="/history" element={<HistoryPage />} />
          <Route path="/approvals" element={<ApprovalsPage />} />
          <Route path="/tasks" element={<TasksPage />} />
          <Route
            path="*"
            element={
              <div className="empty">
                <h2>Page not found</h2>
                <p className="subtitle">That address does not match anything in the app.</p>
              </div>
            }
          />
        </Routes>
      </main>
    </>
  );
}
