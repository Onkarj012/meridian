import { createServer, type IncomingMessage, type ServerResponse } from "node:http";

import {
  JOB_REQUEST_SCHEMA,
  WORKFLOW_TYPES,
  type JobState
} from "../src/lib/contracts";

export interface FixtureJobScript {
  states: JobState[];
  result?: Record<string, unknown>;
  failure?: Record<string, unknown>;
  submitStatus?: number;
  submitBody?: unknown;
  statusCode?: number;
}

export interface FixtureWorker {
  url: string;
  token: string;
  close(): Promise<void>;
  submissions: { jobType: string; operationId: string }[];
  polls: number;
  script(jobType: string, script: FixtureJobScript): void;
}

interface RequestBody {
  schema_version?: unknown;
  operation_id?: unknown;
  job_type?: unknown;
  requested_by?: unknown;
  parameters?: unknown;
}

interface FixtureJob {
  jobId: string;
  jobType: string;
  operationId: string;
  pollIndex: number;
}

function sendJson(response: ServerResponse, status: number, body: unknown): void {
  response.writeHead(status, { "Content-Type": "application/json" });
  response.end(JSON.stringify(body));
}

function errorBody(
  code: string,
  message: string,
  retryable: boolean,
  jobId: string | null = null
): Record<string, unknown> {
  return { code, message, retryable, job_id: jobId };
}

async function readJson(request: IncomingMessage): Promise<unknown> {
  const chunks: Buffer[] = [];
  for await (const chunk of request) {
    chunks.push(Buffer.isBuffer(chunk) ? chunk : Buffer.from(chunk));
  }
  return JSON.parse(Buffer.concat(chunks).toString("utf8"));
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function validParameters(
  jobType: string,
  parameters: unknown,
  expectedStudyId: string | null
): boolean {
  if (!isRecord(parameters)) {
    return false;
  }
  if (jobType === "governance.bootstrap.v1") {
    return (
      typeof parameters.source_alias === "string" &&
      isRecord(parameters.study)
    );
  }
  if (jobType === "diagnostic.intraday-v1_1-smoke.v1") {
    return (
      typeof parameters.study_id === "string" &&
      parameters.study_id.length > 0 &&
      (expectedStudyId === null || parameters.study_id === expectedStudyId)
    );
  }
  return false;
}

function jobIdFor(operationId: string): string {
  return `job-${Buffer.from(operationId, "utf8").toString("base64url")}`;
}

export async function startFixtureWorker(
  options: { token?: string } = {}
): Promise<FixtureWorker> {
  const token = options.token ?? "fixture-worker-token";
  const scripts = new Map<string, FixtureJobScript>();
  const jobsByOperation = new Map<string, FixtureJob>();
  const jobsById = new Map<string, FixtureJob>();
  const submissions: { jobType: string; operationId: string }[] = [];
  let polls = 0;

  const server = createServer(async (request, response) => {
    const authorization = request.headers.authorization;
    if (authorization === undefined) {
      sendJson(response, 401, errorBody("unauthorized", "authorization required", false));
      return;
    }
    if (authorization !== `Bearer ${token}`) {
      sendJson(response, 403, errorBody("forbidden", "invalid credentials", false));
      return;
    }

    const url = new URL(request.url ?? "/", "http://127.0.0.1");

    if (request.method === "POST" && url.pathname === "/internal/v1/jobs") {
      let unknownBody: unknown;
      try {
        unknownBody = await readJson(request);
      } catch {
        sendJson(response, 422, errorBody("invalid_json", "invalid JSON", false));
        return;
      }

      if (!isRecord(unknownBody)) {
        sendJson(response, 422, errorBody("validation_error", "invalid request", false));
        return;
      }
      const body: RequestBody = unknownBody;
      const jobType = body.job_type;
      const operationId = body.operation_id;
      const bootstrapSummary = scripts.get("governance.bootstrap.v1")?.result?.summary;
      const expectedStudyId =
        isRecord(bootstrapSummary) && typeof bootstrapSummary.study_id === "string"
          ? bootstrapSummary.study_id
          : null;
      if (
        body.schema_version !== JOB_REQUEST_SCHEMA ||
        typeof jobType !== "string" ||
        !WORKFLOW_TYPES.some((supported) => supported === jobType) ||
        typeof operationId !== "string" ||
        operationId.length === 0 ||
        typeof body.requested_by !== "string" ||
        !validParameters(jobType, body.parameters, expectedStudyId)
      ) {
        sendJson(response, 422, errorBody("validation_error", "invalid request", false));
        return;
      }

      submissions.push({ jobType, operationId });
      const script = scripts.get(jobType) ?? { states: ["SUCCEEDED"] };
      if (script.submitStatus !== undefined && ![200, 201].includes(script.submitStatus)) {
        const retryable =
          script.submitStatus === 408 ||
          script.submitStatus === 423 ||
          script.submitStatus === 425 ||
          script.submitStatus === 429 ||
          script.submitStatus >= 500;
        sendJson(
          response,
          script.submitStatus,
          script.submitBody ??
            errorBody(
              `forced_${script.submitStatus}`,
              "forced submission failure",
              retryable
            )
        );
        return;
      }

      const existing = jobsByOperation.get(operationId);
      const job =
        existing ??
        {
          jobId: jobIdFor(operationId),
          jobType,
          operationId,
          pollIndex: 0
        };
      if (!existing) {
        jobsByOperation.set(operationId, job);
        jobsById.set(job.jobId, job);
      }

      const status = existing ? 200 : (script.submitStatus ?? 201);
      sendJson(
        response,
        status,
        script.submitBody ?? {
          schema_version: "meridian.job-submission.v1",
          job_id: job.jobId,
          created: existing === undefined,
          state: "PENDING"
        }
      );
      return;
    }

    const statusMatch = url.pathname.match(/^\/internal\/v1\/jobs\/([^/]+)$/);
    if (request.method === "GET" && statusMatch) {
      const jobId = decodeURIComponent(statusMatch[1]);
      const job = jobsById.get(jobId);
      if (!job) {
        sendJson(response, 404, errorBody("job_not_found", "job not found", false, jobId));
        return;
      }

      polls += 1;
      const script = scripts.get(job.jobType) ?? { states: ["SUCCEEDED"] };
      if (script.statusCode !== undefined && script.statusCode !== 200) {
        const retryable =
          script.statusCode === 408 ||
          script.statusCode === 423 ||
          script.statusCode === 425 ||
          script.statusCode === 429 ||
          script.statusCode >= 500;
        sendJson(
          response,
          script.statusCode,
          errorBody(
            `forced_${script.statusCode}`,
            "forced status failure",
            retryable,
            jobId
          )
        );
        return;
      }

      const states = script.states.length > 0 ? script.states : ["SUCCEEDED"];
      const state = states[Math.min(job.pollIndex, states.length - 1)];
      job.pollIndex += 1;
      const timestamp = "2026-01-01T00:00:00.000Z";
      sendJson(response, 200, {
        schema_version: "meridian.job-status.v1",
        job_id: job.jobId,
        job_type: job.jobType,
        operation_id: job.operationId,
        state,
        attempt: job.pollIndex,
        not_before: null,
        created_at: timestamp,
        updated_at: timestamp,
        result: state === "SUCCEEDED" ? (script.result ?? {}) : null,
        failure: state === "FAILED" ? (script.failure ?? {}) : null
      });
      return;
    }

    sendJson(response, 404, errorBody("not_found", "route not found", false));
  });

  await new Promise<void>((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      server.off("error", reject);
      resolve();
    });
  });

  const address = server.address();
  if (address === null || typeof address === "string") {
    throw new Error("Fixture worker failed to bind a TCP port");
  }

  return {
    url: `http://127.0.0.1:${address.port}`,
    token,
    submissions,
    get polls() {
      return polls;
    },
    script(jobType: string, script: FixtureJobScript): void {
      scripts.set(jobType, script);
    },
    close(): Promise<void> {
      return new Promise((resolve, reject) => {
        server.close((error) => {
          if (error) {
            reject(error);
          } else {
            resolve();
          }
        });
      });
    }
  };
}
