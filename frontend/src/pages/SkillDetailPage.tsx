import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { ErrorBanner, Loading, StatusBadge, formatTime } from "../components";
import type { Execution, Skill, SkillVersion } from "../types";

export default function SkillDetailPage() {
  const { skillId } = useParams();
  const id = Number(skillId);
  const navigate = useNavigate();

  const [skill, setSkill] = useState<Skill | null>(null);
  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [runs, setRuns] = useState<Execution[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);

  function load() {
    setLoading(true);
    Promise.all([api.getSkill(id), api.listExecutions(id)])
      .then(([detail, executions]) => {
        setSkill(detail.skill);
        setVersions(detail.versions);
        setRuns(executions);
        setError(null);
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }

  useEffect(load, [id]);

  async function handleNewDraft() {
    setBusy(true);
    setError(null);
    try {
      const newest = versions[versions.length - 1];
      const draft = await api.createDraft(id, newest?.id);
      navigate(`/versions/${draft.id}/edit`);
    } catch (e) {
      setError(e);
      setBusy(false);
    }
  }

  if (loading) return <Loading label="Loading skill" />;

  if (!skill) {
    return (
      <>
        <ErrorBanner error={error} />
        <Link to="/">Back to skills</Link>
      </>
    );
  }

  const draft = versions.find((v) => v.status === "draft");
  const published = versions.filter((v) => v.status === "published");

  return (
    <>
      <div className="page-head">
        <div>
          <div className="small muted">
            <Link to="/">Skills</Link> / {skill.name}
          </div>
          <h1>{skill.name}</h1>
          <p className="subtitle">{skill.purpose || "No purpose set."}</p>
        </div>
        <div className="row">
          {versions.length >= 2 && (
            <Link to={`/skills/${id}/compare`}>
              <button>Compare versions</button>
            </Link>
          )}
          <button className="primary" onClick={handleNewDraft} disabled={busy || !!draft}>
            {busy ? "Creating..." : "New draft"}
          </button>
        </div>
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {draft && (
        <div className="banner banner-info">
          There is an open draft (version {draft.version_number}).{" "}
          <Link to={`/versions/${draft.id}/edit`}>Continue editing it</Link> — a skill can only
          have one draft at a time.
        </div>
      )}

      <div className="panel">
        <h2>Versions</h2>
        <div className="table-scroll">
          <table>
            <thead>
              <tr>
                <th>Version</th>
                <th>Status</th>
                <th>Tools</th>
                <th>Steps</th>
                <th>Published</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {versions.map((version) => (
                <tr key={version.id}>
                  <td>
                    <strong>v{version.version_number}</strong>
                  </td>
                  <td>
                    <StatusBadge status={version.status} />
                  </td>
                  <td>
                    {version.allowed_tools.length === 0 ? (
                      <span className="muted">none</span>
                    ) : (
                      <div className="row">
                        {version.allowed_tools.map((tool) => (
                          <span
                            key={tool}
                            className={`badge ${
                              version.approval_required_tools.includes(tool)
                                ? "badge-amber"
                                : "badge-grey"
                            }`}
                          >
                            {tool}
                            {version.approval_required_tools.includes(tool) ? " (approval)" : ""}
                          </span>
                        ))}
                      </div>
                    )}
                  </td>
                  <td className="mono">{version.max_steps}</td>
                  <td className="small muted">{formatTime(version.published_at)}</td>
                  <td>
                    <div className="row">
                      <Link to={`/versions/${version.id}/run`}>
                        <button className="small primary">Run</button>
                      </Link>
                      <Link to={`/versions/${version.id}/edit`}>
                        <button className="small">
                          {version.is_editable ? "Edit" : "View"}
                        </button>
                      </Link>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {published.length === 0 && (
          <p className="muted small" style={{ marginTop: 12, marginBottom: 0 }}>
            Nothing is published yet. You can still run a draft to test it.
          </p>
        )}
      </div>

      <div className="panel">
        <h2>Recent runs</h2>
        {runs.length === 0 ? (
          <p className="muted small" style={{ margin: 0 }}>
            This skill has not been run yet.
          </p>
        ) : (
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>Run</th>
                  <th>Version</th>
                  <th>Status</th>
                  <th>Steps</th>
                  <th>Started</th>
                </tr>
              </thead>
              <tbody>
                {runs.slice(0, 10).map((run) => (
                  <tr key={run.id}>
                    <td>
                      <Link to={`/executions/${run.id}`}>#{run.id}</Link>
                    </td>
                    <td className="mono">v{run.version_number}</td>
                    <td>
                      <StatusBadge status={run.status} />
                    </td>
                    <td className="mono">
                      {run.step_count}
                      {run.max_steps ? ` / ${run.max_steps}` : ""}
                    </td>
                    <td className="small muted">{formatTime(run.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}
