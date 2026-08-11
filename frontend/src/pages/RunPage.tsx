import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { ErrorBanner, JsonField, Loading, ProblemList, StatusBadge } from "../components";
import type { Problem, SkillVersion } from "../types";

/**
 * Runs a skill with sample input.
 *
 * Runs are synchronous, so this request can stay open for a while. The button
 * says what is happening rather than appearing frozen, and explains that the
 * run may stop part-way to ask for approval.
 */
export default function RunPage() {
  const { versionId } = useParams();
  const id = Number(versionId);
  const navigate = useNavigate();

  const [version, setVersion] = useState<SkillVersion | null>(null);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [loading, setLoading] = useState(true);

  const [input, setInput] = useState<Record<string, any>>({});
  const [inputValid, setInputValid] = useState(true);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<unknown>(null);

  useEffect(() => {
    api
      .getVersion(id)
      .then((data) => {
        setVersion(data.version);
        setProblems(data.problems);
        // Prefill with the first example's input if there is one, so the page
        // is immediately usable without hunting for valid input.
        const example = data.version.examples?.[0];
        setInput(example?.input ?? {});
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [id]);

  async function handleRun() {
    setRunning(true);
    setError(null);
    try {
      const execution = await api.run(id, input);
      navigate(`/executions/${execution.id}`);
    } catch (e) {
      setError(e);
      setRunning(false);
    }
  }

  if (loading) return <Loading label="Loading skill version" />;

  if (!version) {
    return (
      <>
        <ErrorBanner error={error} />
        <Link to="/">Back to skills</Link>
      </>
    );
  }

  const blocking = problems.filter((p) => p.severity === "error");
  const canRun = blocking.length === 0 && inputValid && !running;

  return (
    <>
      <div className="page-head">
        <div>
          <div className="small muted">
            <Link to="/">Skills</Link> /{" "}
            <Link to={`/skills/${version.skill_id}`}>{version.skill_name}</Link> / Run
          </div>
          <h1>
            Run {version.skill_name} <span className="muted">v{version.version_number}</span>
          </h1>
          <p className="subtitle">
            Test this version with sample input. Nothing is written to any system unless you
            approve it.
          </p>
        </div>
        <StatusBadge status={version.status} />
      </div>

      <ErrorBanner error={error} onDismiss={() => setError(null)} />

      {blocking.length > 0 && <ProblemList problems={problems} />}

      <div className="grid-2">
        <div className="panel">
          <h2>Input</h2>
          <JsonField
            label="Input JSON"
            hint="Must match the skill's input schema. Checked before the run starts."
            value={input}
            rows={10}
            onChange={(parsed, valid) => {
              setInputValid(valid);
              if (valid) setInput(parsed);
            }}
          />

          {version.examples?.length > 0 && (
            <div className="field">
              <label>Load an example</label>
              <div className="row">
                {version.examples.map((example, index) => (
                  <button
                    key={index}
                    className="small"
                    onClick={() => setInput(example.input ?? {})}
                  >
                    Example {index + 1}
                  </button>
                ))}
              </div>
            </div>
          )}

          <button className="primary" onClick={handleRun} disabled={!canRun}>
            {running ? "Running..." : "Run skill"}
          </button>

          {running && (
            <p className="muted small" style={{ marginTop: 10 }}>
              <span className="spinner" /> The agent is working. This usually takes 5 to 30
              seconds, and it may pause to ask for your approval before doing anything that
              changes data.
            </p>
          )}
          {!inputValid && (
            <p className="small" style={{ color: "var(--danger)", marginTop: 10 }}>
              Fix the JSON above before running.
            </p>
          )}
        </div>

        <div className="panel">
          <h2>What this version can do</h2>

          <h3>Tools it may use</h3>
          {version.allowed_tools.length === 0 ? (
            <p className="muted small">
              None. It must answer from the input and its instructions alone.
            </p>
          ) : (
            <div className="row" style={{ marginBottom: 14 }}>
              {version.allowed_tools.map((tool) => (
                <span
                  key={tool}
                  className={`badge ${
                    version.approval_required_tools.includes(tool) ? "badge-amber" : "badge-grey"
                  }`}
                >
                  {tool}
                  {version.approval_required_tools.includes(tool) ? " · needs approval" : ""}
                </span>
              ))}
            </div>
          )}

          <h3>Step limit</h3>
          <p className="small" style={{ marginTop: 0 }}>
            <span className="mono">{version.max_steps}</span> — the run stops if it has not
            finished by then.
          </p>

          <h3>Expected output shape</h3>
          <pre>{JSON.stringify(version.output_schema, null, 2)}</pre>
        </div>
      </div>
    </>
  );
}
