/* Shapes returned by the backend.
   These mirror the serializer functions in app/schemas.py. */

export interface Tool {
  name: string;
  description: string;
  parameters: Record<string, unknown>;
  is_write: boolean;
}

export interface Skill {
  id: number;
  name: string;
  purpose: string;
  created_at: string;
  version_count: number;
  latest_published_version: number | null;
  has_draft: boolean;
}

export interface SkillVersion {
  id: number;
  skill_id: number;
  skill_name: string | null;
  version_number: number;
  status: "draft" | "published" | "archived";
  instructions: string;
  input_schema: Record<string, any>;
  output_schema: Record<string, any>;
  examples: Array<Record<string, any>>;
  allowed_tools: string[];
  approval_required_tools: string[];
  max_steps: number;
  created_at: string;
  published_at: string | null;
  is_editable: boolean;
}

export interface Problem {
  field: string;
  message: string;
  severity: "error" | "warning";
}

export interface ExecutionStep {
  id: number;
  step_number: number;
  kind: "llm_call" | "tool_call" | "approval" | "final_output" | "invalid_output" | "error";
  llm_text: string | null;
  tool_name: string | null;
  tool_input: Record<string, any> | null;
  tool_output: Record<string, any> | null;
  error_message: string | null;
  duration_ms: number | null;
  created_at: string;
}

export interface Approval {
  id: number;
  execution_id: number;
  step_number: number;
  tool_name: string;
  tool_input: Record<string, any>;
  status: "pending" | "approved" | "rejected";
  executed: boolean;
  tool_output: Record<string, any> | null;
  idempotency_key: string;
  created_at: string;
  decided_at: string | null;
  skill_name?: string | null;
  version_number?: number | null;
}

export type ExecutionStatus =
  | "running"
  | "awaiting_approval"
  | "completed"
  | "failed"
  | "cancelled"
  | "max_steps_exceeded";

export interface Execution {
  id: number;
  status: ExecutionStatus;
  skill_version_id: number;
  skill_id: number | null;
  skill_name: string | null;
  version_number: number | null;
  input_data: Record<string, any>;
  final_output: Record<string, any> | null;
  error_message: string | null;
  step_count: number;
  max_steps: number | null;
  rerun_of_execution_id: number | null;
  created_at: string;
  finished_at: string | null;
  steps?: ExecutionStep[];
  approvals?: Approval[];
  pending_approval?: Approval | null;
}

export interface Difference {
  field: string;
  left: any;
  right: any;
  changed: boolean;
}

export interface Comparison {
  left: SkillVersion;
  right: SkillVersion;
  differences: Difference[];
  changed_count: number;
}

export interface Task {
  id: number;
  title: string;
  description: string;
  assignee: string;
  created_by_execution_id: number | null;
  created_at: string;
}

export interface Health {
  status: string;
  llm_configured: boolean;
  model: string;
}
