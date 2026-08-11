import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { api } from "../api";
import { EmptyState, ErrorBanner, Loading, StatusBadge } from "../components";
import type { Skill } from "../types";

export default function SkillsPage() {
  const navigate = useNavigate();

  const [skills, setSkills] = useState<Skill[] | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);

  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [purpose, setPurpose] = useState("");
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState<unknown>(null);

  function load() {
    setLoadError(null);
    api
      .listSkills()
      .then(setSkills)
      .catch((e) => {
        setSkills([]);
        setLoadError(e);
      });
  }

  useEffect(load, []);

  async function handleCreate(event: React.FormEvent) {
    event.preventDefault();
    if (!name.trim()) return;

    setCreating(true);
    setCreateError(null);
    try {
      const result = await api.createSkill(name.trim(), purpose.trim());
      // Straight into the editor - a skill with an empty draft is not useful yet.
      navigate(`/versions/${result.draft_version_id}/edit`);
    } catch (e) {
      setCreateError(e);
      setCreating(false);
    }
  }

  return (
    <>
      <div className="page-head">
        <div>
          <h1>Skills</h1>
          <p className="subtitle">
            A skill is a reusable AI capability: instructions, an input and output shape, and the
            tools it is allowed to use.
          </p>
        </div>
        <button className="primary" onClick={() => setShowForm((v) => !v)}>
          {showForm ? "Cancel" : "New skill"}
        </button>
      </div>

      {showForm && (
        <form className="panel" onSubmit={handleCreate}>
          <h2>Create a skill</h2>
          <ErrorBanner error={createError} onDismiss={() => setCreateError(null)} />

          <div className="field">
            <label htmlFor="skill-name">Name</label>
            <div className="hint">Must be unique.</div>
            <input
              id="skill-name"
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Invoice Checker"
              maxLength={120}
              required
            />
          </div>

          <div className="field">
            <label htmlFor="skill-purpose">Purpose</label>
            <div className="hint">One or two sentences on what this skill is for.</div>
            <textarea
              id="skill-purpose"
              value={purpose}
              onChange={(e) => setPurpose(e.target.value)}
              placeholder="Checks an invoice against the payment policy and flags anything unusual."
              rows={3}
            />
          </div>

          <button className="primary" type="submit" disabled={creating || !name.trim()}>
            {creating ? "Creating..." : "Create and edit draft"}
          </button>
        </form>
      )}

      <ErrorBanner error={loadError} onDismiss={() => setLoadError(null)} />

      {skills === null ? (
        <Loading label="Loading skills" />
      ) : skills.length === 0 ? (
        <EmptyState
          title="No skills yet"
          description="Create your first skill to define what the agent can do, which tools it may use, and which actions need your approval."
          action={
            <button className="primary" onClick={() => setShowForm(true)}>
              Create a skill
            </button>
          }
        />
      ) : (
        skills.map((skill) => (
          <Link key={skill.id} to={`/skills/${skill.id}`} className="list-item">
            <div className="spread">
              <div style={{ minWidth: 0 }}>
                <strong>{skill.name}</strong>
                <div className="subtitle">{skill.purpose || "No purpose set."}</div>
              </div>
              <div className="row">
                {skill.has_draft && <StatusBadge status="draft" />}
                {skill.latest_published_version !== null ? (
                  <span className="badge badge-green">
                    Published v{skill.latest_published_version}
                  </span>
                ) : (
                  <span className="badge badge-grey">Never published</span>
                )}
                <span className="muted small">
                  {skill.version_count} version{skill.version_count === 1 ? "" : "s"}
                </span>
              </div>
            </div>
          </Link>
        ))
      )}
    </>
  );
}
