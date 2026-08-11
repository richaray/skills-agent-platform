/* The one place that talks to the backend.

   Every call goes through `request`, so error handling is consistent
   everywhere: the UI always receives an ApiError with a readable message and,
   when the backend sent them, a list of validation problems to display. */

import type {
  Approval,
  Comparison,
  Execution,
  Health,
  Problem,
  Skill,
  SkillVersion,
  Task,
  Tool,
} from "./types";

export class ApiError extends Error {
  status: number;
  problems: Problem[];

  constructor(message: string, status: number, problems: Problem[] = []) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.problems = problems;
  }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  let response: Response;

  try {
    response = await fetch(`/api${path}`, {
      headers: { "Content-Type": "application/json" },
      ...options,
    });
  } catch {
    // The fetch itself failed: no network, or the server is asleep or restarting.
    throw new ApiError(
      "Could not reach the server. It may be starting up - wait a moment and try again.",
      0,
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }

  let body: any = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }

  if (!response.ok) {
    // FastAPI puts errors in `detail`, which is either a plain string or an
    // object carrying a message plus a list of validation problems.
    const detail = body?.detail;

    if (typeof detail === "string") {
      throw new ApiError(detail, response.status);
    }
    if (detail && typeof detail === "object" && !Array.isArray(detail)) {
      throw new ApiError(
        detail.message ?? "The request was rejected.",
        response.status,
        detail.problems ?? [],
      );
    }
    if (Array.isArray(detail)) {
      // Pydantic's own validation format, when a request body is malformed.
      const messages = detail
        .map((d: any) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`)
        .join("; ");
      throw new ApiError(messages || "The request was not valid.", response.status);
    }

    throw new ApiError(`Request failed (${response.status}).`, response.status);
  }

  return body as T;
}

export const api = {
  health: () => request<Health>("/health"),
  tools: () => request<Tool[]>("/tools"),

  // --- skills ---
  listSkills: () => request<Skill[]>("/skills"),
  getSkill: (id: number) => request<{ skill: Skill; versions: SkillVersion[] }>(`/skills/${id}`),
  createSkill: (name: string, purpose: string) =>
    request<{ skill: Skill; draft_version_id: number }>("/skills", {
      method: "POST",
      body: JSON.stringify({ name, purpose }),
    }),
  deleteSkill: (id: number) => request<void>(`/skills/${id}`, { method: "DELETE" }),

  // --- versions ---
  getVersion: (id: number) =>
    request<{ version: SkillVersion; problems: Problem[] }>(`/versions/${id}`),
  saveVersion: (id: number, definition: Partial<SkillVersion>) =>
    request<{ version: SkillVersion; problems: Problem[] }>(`/versions/${id}`, {
      method: "PUT",
      body: JSON.stringify(definition),
    }),
  publishVersion: (id: number) =>
    request<{ version: SkillVersion; problems: Problem[] }>(`/versions/${id}/publish`, {
      method: "POST",
    }),
  createDraft: (skillId: number, copyFromVersionId?: number) =>
    request<SkillVersion>(
      `/skills/${skillId}/versions${
        copyFromVersionId ? `?copy_from_version_id=${copyFromVersionId}` : ""
      }`,
      { method: "POST" },
    ),
  deleteDraft: (id: number) => request<void>(`/versions/${id}`, { method: "DELETE" }),
  compare: (skillId: number, left: number, right: number) =>
    request<Comparison>(`/skills/${skillId}/compare?left=${left}&right=${right}`),

  // --- running ---
  run: (versionId: number, inputData: Record<string, any>) =>
    request<Execution>(`/versions/${versionId}/run`, {
      method: "POST",
      body: JSON.stringify({ input_data: inputData }),
    }),
  rerun: (executionId: number) =>
    request<Execution>(`/executions/${executionId}/rerun`, { method: "POST" }),
  listExecutions: (skillId?: number) =>
    request<Execution[]>(`/executions${skillId ? `?skill_id=${skillId}` : ""}`),
  getExecution: (id: number) => request<Execution>(`/executions/${id}`),
  cancel: (id: number) => request<Execution>(`/executions/${id}/cancel`, { method: "POST" }),

  // --- approvals ---
  listApprovals: (status = "pending") => request<Approval[]>(`/approvals?status=${status}`),
  approve: (id: number) => request<Execution>(`/approvals/${id}/approve`, { method: "POST" }),
  reject: (id: number, reason: string) =>
    request<Execution>(`/approvals/${id}/reject`, {
      method: "POST",
      body: JSON.stringify({ reason }),
    }),

  // --- tasks ---
  listTasks: () => request<Task[]>("/tasks"),
};
