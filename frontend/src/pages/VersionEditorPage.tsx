import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api";
import { ErrorBanner, JsonField, Loading, ProblemList, StatusBadge } from "../components";
import type { Problem, SkillVersion, Tool } from "../types";

export default function VersionEditorPage() {
  const { versionId } = useParams();
  const id = Number(versionId);
  const navigate = useNavigate();

  const [version, setVersion] = useState<SkillVersion | null>(null);
  const [tools, setTools] = useState<Tool[]>([]);
  const [problems, setProblems] = useState<Problem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [schemasValid, setSchemasValid] = useState(true);

  // The editable copy. Kept separate from `version` so we always know what the
  // server currently has versus what the user has typed.
  const [draft, setDraft] = useState({
    instructions: "",
    input_schema: {} as Record<string, any>,
    output_schema: {} as Record<string, any>,
    examples: [] as Array<Record<string, any>>,
    allowed_tools: [] as string[],
    approval_required_tools: [] as string[],
    max_steps: 8,
  });

  useEffect(() => {
    Promise.all([api.getVersion(id), api.tools()])
      .then(([data, toolList]) => {
        setVersion(data.version);
        setProblems(data.problems);
        setTools(toolList);
        setDraft({
          instructions: data.version.instructions,
          input_schema: data.version.input_schema,
          output_schema: data.version.output_schema,
          examples: data.version.examples,
          allowed_tools: data.version.allowed_tools,
          approval_required_tools: data.version.approval_required_tools,
          max_steps: data.version.max_steps,
        });
      })
      .catch(setError)
      .finally(() => setLoading(false));
  }, [id]);

  const readOnly = version ? !version.is_editable : true;

  function toggleTool(tool: Tool, checked: boolean) {
    setDraft((current) => {
      const allowed = checked
        ? [...current.allowed_tools, tool.name]
        : current.allowed_tools.filter((t) => t !== tool.name);

      // Platform rule: a tool that writes data always requires approval. We
      // apply it here as well as on the server so the UI never shows a state
      // the backend would reject.
      let approval = current.approval_required_tools.filter((t) => allowed.includes(t));
      if (checked && tool.is_write && !approval.includes(tool.name)) {
        approval = [...approval, tool.name];
      }

      return { ...current, allowed_tools: allowed, approval_required_tools: approval };
    });
  }

  function toggleApproval(tool: Tool, checked: boolean) {
    if (tool.is_write) return; // cannot be turned off
    setDraft((current) => ({
      ...current,
      approval_required_tools: checked
        ? [...current.approval_required_tools, tool.name]
        : current.approval_required_tools.filter((t) => t !== tool.name),
    }));
  }

  async function handleSave() {
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const result = await api.saveVersion(id, draft);
      setVersion(result.version);
      setProblems(result.problems);
      setNotice("Draft saved.");
    } catch (e) {
      setError(e);
    } finally {
      setSaving(false);
    }
  }

  async function handlePublish() {
    setPublishing(true);
    setError(null);
    setNotice(null);
    try {
      // Save first so we never publish something different from what is on screen.
      const saved = await api.saveVersion(id, draft);
      setProblems(saved.problems);

      const result = await api.publishVersion(id);
      setVersion(result.version);
      setProblems(result.problems);
      setNotice(`Version ${result.version.version_number} published and frozen.`);
    } catch (e) {
      setError(e);
    } finally {
      setPublishing(false);
    }
  }

  async function handleDelete() {
    if (!version) return;
    setError(null);
    try {
      await api.deleteDraft(id);
      navigate(`/skills/${version.skill_id}`);
    } catch (e) {
      setError(e);
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

  const hasErrors = problems.some((p) => p.severity === "error");

  return (
    <>
      <div className="page-head">
        <div>
          <div className="small muted">
            <Link to="/">Skills</Link> /{" "}
            <Link to={`/skills/${version.skill_id}`}>{version.skill_name}</Link> / v
            {version.version_number}
          </div>
          <h1>
            {readOnly ? "Viewing" : "Editing"} version {version.version_number}
          </h1>
          <p className="subtitle">
            {readOnly
              ? "This version is published and frozen. Create a new draft to change anything."
              : "Drafts can be saved at any time, even while incomplete. Publishing requires no errors."}
          </p>
        </div>
        <div className="row">
          <StatusBadge status={version.status} />
          <Link to={`/versions/${id}/run`}>
            <button>Run this version</button>
          </Link>
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

      {readOnly && (
        <div className="banner banner-info">
          Published versions are never edited, so that runs recorded against them stay
          reproducible.{" "}
          <Link to={`/skills/${version.skill_id}`}>Create a new draft</Link> to make changes.
        </div>
      )}

      <ProblemList problems={problems} />

      <div className="grid-2">
        <div>
          <div className="panel">
            <h2>Instructions</h2>
            <div className="field">
              <div className="hint">
                What the agent should do, step by step. This becomes the system prompt.
              </div>
              <textarea
                rows={14}
                value={draft.instructions}
                disabled={readOnly}
                onChange={(e) => setDraft({ ...draft, instructions: e.target.value })}
                placeholder="Describe the task, the steps to follow, and what to do when information is missing."
              />
            </div>
          </div>

          <div className="panel">
            <h2>Schemas</h2>
            <JsonField
              label="Input schema"
              hint="JSON Schema. Input is validated against this before a run starts."
              value={draft.input_schema}
              disabled={readOnly}
              onChange={(parsed, valid) => {
                setSchemasValid(valid);
                if (valid) setDraft((d) => ({ ...d, input_schema: parsed }));
              }}
            />
            <JsonField
              label="Output schema"
              hint="JSON Schema. The agent's final answer must match this or the run fails."
              value={draft.output_schema}
              disabled={readOnly}
              onChange={(parsed, valid) => {
                setSchemasValid(valid);
                if (valid) setDraft((d) => ({ ...d, output_schema: parsed }));
              }}
            />
          </div>

          <div className="panel">
            <h2>Examples</h2>
            <JsonField
              label="Examples"
              hint='A list of {"input": ..., "output": ...} pairs shown to the model.'
              value={draft.examples}
              rows={10}
              disabled={readOnly}
              onChange={(parsed, valid) => {
                if (valid && Array.isArray(parsed)) setDraft((d) => ({ ...d, examples: parsed }));
              }}
            />
          </div>
        </div>

        <div>
          <div className="panel">
            <h2>Tools</h2>
            <p className="hint">
              The agent is only told about the tools you tick. If it asks for anything else, the
              platform refuses the call.
            </p>

            {tools.map((tool) => {
              const allowed = draft.allowed_tools.includes(tool.name);
              const needsApproval = draft.approval_required_tools.includes(tool.name);

              return (
                <div key={tool.name} className="checkbox-row">
                  <input
                    type="checkbox"
                    id={`tool-${tool.name}`}
                    checked={allowed}
                    disabled={readOnly}
                    onChange={(e) => toggleTool(tool, e.target.checked)}
                  />
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <label htmlFor={`tool-${tool.name}`}>
                      <code>{tool.name}</code>{" "}
                      {tool.is_write && <span className="badge badge-amber">writes data</span>}
                    </label>
                    <div className="hint" style={{ marginBottom: allowed ? 6 : 0 }}>
                      {tool.description}
                    </div>

                    {allowed && (
                      <label
                        className="row small"
                        style={{ fontWeight: 400, margin: 0, gap: 6 }}
                      >
                        <input
                          type="checkbox"
                          checked={needsApproval}
                          disabled={readOnly || tool.is_write}
                          onChange={(e) => toggleApproval(tool, e.target.checked)}
                          style={{ width: "auto" }}
                        />
                        Require human approval
                        {tool.is_write && (
                          <span className="muted"> — always on for tools that write data</span>
                        )}
                      </label>
                    )}
                  </div>
                </div>
              );
            })}
          </div>

          <div className="panel">
            <h2>Limits</h2>
            <div className="field" style={{ marginBottom: 0 }}>
              <label htmlFor="max-steps">Maximum steps</label>
              <div className="hint">
                How many times the agent may loop before the run is stopped. The platform caps
                this at 15 regardless of what you set.
              </div>
              <input
                id="max-steps"
                type="number"
                min={1}
                max={15}
                value={draft.max_steps}
                disabled={readOnly}
                onChange={(e) => setDraft({ ...draft, max_steps: Number(e.target.value) })}
              />
            </div>
          </div>
        </div>
      </div>

      {!readOnly && (
        <div className="panel">
          <div className="spread">
            <div className="row">
              <button className="primary" onClick={handleSave} disabled={saving || !schemasValid}>
                {saving ? "Saving..." : "Save draft"}
              </button>
              <button
                className="approve"
                onClick={handlePublish}
                disabled={publishing || hasErrors || !schemasValid}
                title={hasErrors ? "Fix the errors above before publishing." : undefined}
              >
                {publishing ? "Publishing..." : "Publish version"}
              </button>
              {hasErrors && (
                <span className="muted small">Publishing is blocked until the errors are fixed.</span>
              )}
            </div>
            <button className="danger" onClick={handleDelete}>
              Delete draft
            </button>
          </div>
        </div>
      )}
    </>
  );
}
