export const CONTROL_REQUEST_SCHEMA = "meridian.control-request.v1";
export const JOB_REQUEST_SCHEMA = "meridian.job-request.v1";
export const WORKFLOW_TYPES = [
  "governance.bootstrap.v1",
  "diagnostic.intraday-v1_1-smoke.v1"
] as const;

const JOB_SUBMISSION_SCHEMA = "meridian.job-submission.v1";
const JOB_STATUS_SCHEMA = "meridian.job-status.v1";
const JOB_STATES = ["PENDING", "RUNNING", "SUCCEEDED", "FAILED"] as const;
const IDEMPOTENCY_KEY_PATTERN = /^[A-Za-z0-9._:-]+$/;

export type WorkflowType = (typeof WORKFLOW_TYPES)[number];
export type JobState = "PENDING" | "RUNNING" | "SUCCEEDED" | "FAILED";

export interface ControlRunInput {
  schemaVersion: string;
  controlRequestId: string;
  idempotencyKey: string;
  workflowType: WorkflowType;
  sourceAlias: string;
  requestedBy: string;
  study: Record<string, unknown>;
  approvalRequired: boolean;
  maxPolls: number;
}

export interface JobSubmission {
  jobId: string;
  created: boolean;
  state: JobState;
}

export interface JobStatus {
  jobId: string;
  jobType: string;
  state: JobState;
  attempt: number;
  notBefore: string | null;
  result: Record<string, unknown> | null;
  failure: Record<string, unknown> | null;
}

export interface ApprovalPayload {
  approved: boolean;
  approver: string;
  reason: string;
}

export interface ControlRunJob {
  role: string;
  jobId: string;
  state: JobState;
}

export type ControlRunResult =
  | {
      status: "SUCCEEDED";
      workflowType: WorkflowType;
      studyId: string | null;
      jobs: ControlRunJob[];
      summary: Record<string, unknown> | null;
    }
  | {
      status: "REJECTED";
      approver: string;
      reason: string;
      jobs: ControlRunJob[];
    };

export const DEFAULT_MAX_POLLS = 900;

function isPlainObject(value: unknown): value is Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }

  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function isJobState(value: unknown): value is JobState {
  return typeof value === "string" && JOB_STATES.some((state) => state === value);
}

function requireString(
  body: Record<string, unknown>,
  field: string,
  message: string
): string {
  const value = body[field];
  if (typeof value !== "string" || value.length === 0) {
    throw new Error(message);
  }
  return value;
}

function requireNullableObject(
  body: Record<string, unknown>,
  field: string,
  message: string
): Record<string, unknown> | null {
  const value = body[field];
  if (value === null) {
    return null;
  }
  if (!isPlainObject(value)) {
    throw new Error(message);
  }
  return value;
}

export function isTerminal(state: JobState): boolean {
  return state === "SUCCEEDED" || state === "FAILED";
}

export function approvalToken(controlRequestId: string): string {
  return `meridian:approval:v1:${controlRequestId}`;
}

export function operationId(idempotencyKey: string, suffix: string): string {
  return `${idempotencyKey}:${suffix}`;
}

export function parseControlRequest(
  body: unknown,
  idempotencyKey: string | null
): ControlRunInput {
  if (idempotencyKey === null) {
    throw new Error("Idempotency-Key header is required");
  }
  if (idempotencyKey.length < 8 || idempotencyKey.length > 128) {
    throw new Error("Idempotency-Key must be between 8 and 128 characters");
  }
  if (!IDEMPOTENCY_KEY_PATTERN.test(idempotencyKey)) {
    throw new Error("Idempotency-Key contains invalid characters");
  }
  if (!isPlainObject(body)) {
    throw new Error("Request body must be an object");
  }
  if (body.schemaVersion !== CONTROL_REQUEST_SCHEMA) {
    throw new Error("Unsupported schemaVersion");
  }
  if (
    typeof body.workflowType !== "string" ||
    !WORKFLOW_TYPES.some((workflowType) => workflowType === body.workflowType)
  ) {
    throw new Error("Unsupported workflowType");
  }
  if (typeof body.sourceAlias !== "string" || body.sourceAlias.trim() === "") {
    throw new Error("sourceAlias must be a non-empty string");
  }
  if (typeof body.requestedBy !== "string" || body.requestedBy.trim() === "") {
    throw new Error("requestedBy must be a non-empty string");
  }
  if (!isPlainObject(body.study)) {
    throw new Error("study must be a plain object");
  }
  if (typeof body.approvalRequired !== "boolean") {
    throw new Error("approvalRequired must be a boolean");
  }

  const maxPolls = body.maxPolls ?? DEFAULT_MAX_POLLS;
  if (!Number.isInteger(maxPolls) || (maxPolls as number) < 1 || (maxPolls as number) > 5000) {
    throw new Error("maxPolls must be an integer between 1 and 5000");
  }

  return {
    schemaVersion: CONTROL_REQUEST_SCHEMA,
    controlRequestId: "",
    idempotencyKey,
    workflowType: body.workflowType as WorkflowType,
    sourceAlias: body.sourceAlias,
    requestedBy: body.requestedBy,
    study: body.study,
    approvalRequired: body.approvalRequired,
    maxPolls: maxPolls as number
  };
}

export function parseJobSubmission(body: unknown): JobSubmission {
  if (!isPlainObject(body)) {
    throw new Error("Invalid worker job submission response");
  }
  if (body.schema_version !== JOB_SUBMISSION_SCHEMA) {
    throw new Error("Invalid worker job submission schema_version");
  }

  const jobId = requireString(
    body,
    "job_id",
    "Invalid worker job submission job_id"
  );
  if (typeof body.created !== "boolean") {
    throw new Error("Invalid worker job submission created");
  }
  if (!isJobState(body.state)) {
    throw new Error("Invalid worker job submission state");
  }

  return { jobId, created: body.created, state: body.state };
}

export function parseJobStatus(body: unknown): JobStatus {
  if (!isPlainObject(body)) {
    throw new Error("Invalid worker job status response");
  }
  if (body.schema_version !== JOB_STATUS_SCHEMA) {
    throw new Error("Invalid worker job status schema_version");
  }

  const jobId = requireString(body, "job_id", "Invalid worker job status job_id");
  const jobType = requireString(
    body,
    "job_type",
    "Invalid worker job status job_type"
  );
  requireString(body, "operation_id", "Invalid worker job status operation_id");
  requireString(body, "created_at", "Invalid worker job status created_at");
  requireString(body, "updated_at", "Invalid worker job status updated_at");

  if (!isJobState(body.state)) {
    throw new Error("Invalid worker job status state");
  }
  if (!Number.isInteger(body.attempt) || (body.attempt as number) < 0) {
    throw new Error("Invalid worker job status attempt");
  }
  if (body.not_before !== null && typeof body.not_before !== "string") {
    throw new Error("Invalid worker job status not_before");
  }

  const result = requireNullableObject(
    body,
    "result",
    "Invalid worker job status result"
  );
  const failure = requireNullableObject(
    body,
    "failure",
    "Invalid worker job status failure"
  );

  return {
    jobId,
    jobType,
    state: body.state,
    attempt: body.attempt as number,
    notBefore: body.not_before,
    result,
    failure
  };
}

export function parseApprovalPayload(body: unknown): ApprovalPayload {
  if (!isPlainObject(body)) {
    throw new Error("Approval payload must be an object");
  }
  if (typeof body.approved !== "boolean") {
    throw new Error("approved must be a boolean");
  }
  if (typeof body.approver !== "string" || body.approver.trim() === "") {
    throw new Error("approver must be a non-empty string");
  }
  if (typeof body.reason !== "string") {
    throw new Error("reason must be a string");
  }

  return {
    approved: body.approved,
    approver: body.approver,
    reason: body.reason
  };
}
