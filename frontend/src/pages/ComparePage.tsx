import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api";
import { ErrorBanner, Loading, StatusBadge } from "../components";
import type { Comparison, SkillVersion } from "../types";

/** Renders a value the same way on both sides so differences are easy to spot. */
function Value({ value }: { value: any }) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  if (typeof value === "string") {
    return value.trim() === "" ? (
      <span className="muted">(empty)</span>
    ) : (
      <pre>{value}</pre>
    );
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return <span className="mono">{String(value)}</span>;
  }
  if (Array.isArray(value) && value.length === 0) return <span className="muted">(none)</span>;
  return <pre>{JSON.stringify(value, null, 2)}</pre>;
}

export default function ComparePage() {
  const { skillId } = useParams();
  const id = Number(skillId);

  const [versions, setVersions] = useState<SkillVersion[]>([]);
  const [left, setLeft] = useState<number | null>(null);
  const [right, setRight] = useState<number | null>(null);
  const [comparison, setComparison] = useState<Comparison | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [onlyChanged, setOnlyChanged] = useState(true);

  // Load the version list, then default to comparing the two most recent.
  useEffect(() => {
    api
      .getSkill(id)
      .then((detail) => {
        setVersions(detail.versions);
        if (detail.versions.length >= 2) {
          setLeft(detail.versions[detail.versions.length - 2].id);
          setRight(detail.versions[detail.versions.length - 1].id);
        }
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [id]);

  useEffect(() => {
    if (left === null || right === null) return;
    setError(null);
    api.compare(id, left, right).then(setComparison).catch(setError);
  }, [id, left, right]);

  if (loading) return <Loading label="Loading versions" />;

  if (versions.length < 2) {
    return (
      <>
        <h1>Compare versions</h1>
        <div className="banner banner-info">
          This skill has only one version, so there is nothing to compare yet.{" "}
          <Link to={`/skills/${id}`}>Back to the skill</Link>.
        </div>
      </>
    );
  }

  const rows = comparison
    ? comparison.differences.filter((d) => (onlyChanged ? d.changed : true))
    : [];

  return (
    <>
      <div className="page-head">
        <div>
          <div className="small muted">
            <Link to="/">Skills</Link> / <Link to={`/skills/${id}`}>Back to skill</Link> / Compare
          </div>
          <h1>Compare versions</h1>
          <p className="subtitle">
            Published versions are frozen, so a comparison always shows exactly what changed
            between two definitions that really ran.
          </p>
        </div>
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      <div className="panel">
        <div className="grid-2">
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Left</label>
            <select value={left ?? ""} onChange={(e) => setLeft(Number(e.target.value))}>
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version_number} ({v.status})
                </option>
              ))}
            </select>
          </div>
          <div className="field" style={{ marginBottom: 0 }}>
            <label>Right</label>
            <select value={right ?? ""} onChange={(e) => setRight(Number(e.target.value))}>
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  v{v.version_number} ({v.status})
                </option>
              ))}
            </select>
          </div>
        </div>
      </div>

      {!comparison ? (
        <Loading label="Comparing" />
      ) : (
        <>
          <div className="panel panel-tight">
            <div className="spread">
              <div className="row">
                <strong>
                  v{comparison.left.version_number} → v{comparison.right.version_number}
                </strong>
                <StatusBadge status={comparison.left.status} />
                <span className="muted">vs</span>
                <StatusBadge status={comparison.right.status} />
              </div>
              <div className="row">
                <span
                  className={`badge ${
                    comparison.changed_count === 0 ? "badge-grey" : "badge-blue"
                  }`}
                >
                  {comparison.changed_count} field
                  {comparison.changed_count === 1 ? "" : "s"} changed
                </span>
                <label className="row small" style={{ margin: 0, fontWeight: 400 }}>
                  <input
                    type="checkbox"
                    checked={onlyChanged}
                    onChange={(e) => setOnlyChanged(e.target.checked)}
                    style={{ width: "auto" }}
                  />
                  Only show changes
                </label>
              </div>
            </div>
          </div>

          {rows.length === 0 ? (
            <div className="banner banner-info">
              These two versions are identical in every compared field.
            </div>
          ) : (
            rows.map((row) => (
              <div
                key={row.field}
                className={`diff-row ${row.changed ? "diff-changed" : ""}`}
              >
                <div className="diff-head">
                  <code>{row.field}</code>
                  <span className={`badge ${row.changed ? "badge-blue" : "badge-grey"}`}>
                    {row.changed ? "changed" : "same"}
                  </span>
                </div>
                <div className="diff-body">
                  <div className="diff-side">
                    <div className="small muted" style={{ marginBottom: 6 }}>
                      v{comparison.left.version_number}
                    </div>
                    <Value value={row.left} />
                  </div>
                  <div className="diff-side">
                    <div className="small muted" style={{ marginBottom: 6 }}>
                      v{comparison.right.version_number}
                    </div>
                    <Value value={row.right} />
                  </div>
                </div>
              </div>
            ))
          )}
        </>
      )}
    </>
  );
}
