import { FatalError, RetryableError } from "workflow";

import {
  JOB_REQUEST_SCHEMA,
  parseJobStatus,
  parseJobSubmission,
  type JobStatus,
  type JobSubmission,
  type WorkflowType
} from "../lib/contracts";
import { readWorkerEnv } from "../lib/env";

interface WorkerErrorDetails {
  code: string;
  message: string;
}

async function workerErrorDetails(response: Response): Promise<WorkerErrorDetails> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    return { code: "unknown_error", message: "invalid error response" };
  }

  if (typeof body !== "object" || body === null || Array.isArray(body)) {
    return { code: "unknown_error", message: "invalid error response" };
  }

  const record = body as Record<string, unknown>;
  return {
    code: typeof record.code === "string" ? record.code : "unknown_error",
    message:
      typeof record.message === "string"
        ? record.message
        : "invalid error response"
  };
}

async function throwForWorkerFailure(response: Response): Promise<never> {
  const details = await workerErrorDetails(response);
  const message = `Worker ${response.status} ${details.code}: ${details.message}`;

  if (response.status === 429) {
    throw new RetryableError(message, { retryAfter: "30s" });
  }
  if (
    response.status === 408 ||
    response.status === 423 ||
    response.status === 425 ||
    response.status >= 500
  ) {
    throw new RetryableError(message);
  }

  throw new FatalError(message);
}

async function workerFetch(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch {
    throw new RetryableError("Worker network request failed");
  }
}

async function parseSuccessfulBody<T>(
  response: Response,
  parser: (body: unknown) => T
): Promise<T> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new FatalError("Worker returned a malformed protocol response");
  }

  try {
    return parser(body);
  } catch {
    throw new FatalError("Worker returned a malformed protocol response");
  }
}

export async function submitWorkerJob(request: {
  jobType: WorkflowType;
  operationId: string;
  requestedBy: string;
  parameters: Record<string, unknown>;
}): Promise<JobSubmission> {
  "use step";

  const { workerUrl, workerToken } = readWorkerEnv();
  const response = await workerFetch(`${workerUrl}/internal/v1/jobs`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${workerToken}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      schema_version: JOB_REQUEST_SCHEMA,
      operation_id: request.operationId,
      job_type: request.jobType,
      requested_by: request.requestedBy,
      parameters: request.parameters
    })
  });

  if (response.status !== 200 && response.status !== 201) {
    await throwForWorkerFailure(response);
  }

  return parseSuccessfulBody(response, parseJobSubmission);
}

submitWorkerJob.maxRetries = 5;

export async function fetchWorkerJobStatus(
  jobId: string,
  pollAttempt: number
): Promise<JobStatus> {
  "use step";

  void pollAttempt;
  const { workerUrl, workerToken } = readWorkerEnv();
  const response = await workerFetch(
    `${workerUrl}/internal/v1/jobs/${encodeURIComponent(jobId)}`,
    {
      method: "GET",
      headers: {
        Authorization: `Bearer ${workerToken}`,
        "Content-Type": "application/json"
      }
    }
  );

  if (response.status !== 200) {
    await throwForWorkerFailure(response);
  }

  return parseSuccessfulBody(response, parseJobStatus);
}

fetchWorkerJobStatus.maxRetries = 5;
