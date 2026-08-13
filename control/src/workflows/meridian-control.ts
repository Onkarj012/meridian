import { createHook, FatalError, sleep } from "workflow";

import {
  approvalToken,
  isTerminal,
  operationId,
  WORKFLOW_TYPES,
  type ApprovalPayload,
  type ControlRunInput,
  type ControlRunJob,
  type ControlRunResult,
  type JobStatus
} from "../lib/contracts";
import {
  fetchWorkerJobStatus,
  submitWorkerJob
} from "../steps/worker-client";

function summaryFrom(status: JobStatus): Record<string, unknown> | null {
  const summary = status.result?.summary;
  if (typeof summary !== "object" || summary === null || Array.isArray(summary)) {
    return null;
  }
  return summary as Record<string, unknown>;
}

function failureCode(status: JobStatus): string {
  const code = status.failure?.code;
  return typeof code === "string" && code.length > 0 ? code : "unknown_failure";
}

async function pollWorkerJob(jobId: string, maxPolls: number): Promise<JobStatus> {
  let poll = 0;
  let status = await fetchWorkerJobStatus(jobId, poll);

  while (!isTerminal(status.state) && poll < maxPolls) {
    await sleep("2s");
    poll += 1;
    status = await fetchWorkerJobStatus(jobId, poll);
  }

  if (!isTerminal(status.state)) {
    throw new FatalError("job did not reach a terminal state");
  }
  if (status.state === "FAILED") {
    throw new FatalError(`Worker job failed: ${failureCode(status)}`);
  }

  return status;
}

function jobRecord(role: string, status: JobStatus): ControlRunJob {
  return { role, jobId: status.jobId, state: status.state };
}

export async function meridianControlWorkflow(
  input: ControlRunInput
): Promise<ControlRunResult> {
  "use workflow";

  if (!WORKFLOW_TYPES.some((workflowType) => workflowType === input.workflowType)) {
    throw new FatalError("Unsupported workflowType");
  }

  const bootstrapSubmission = await submitWorkerJob({
    jobType: "governance.bootstrap.v1",
    operationId: operationId(input.idempotencyKey, "bootstrap"),
    requestedBy: input.requestedBy,
    parameters: {
      source_alias: input.sourceAlias,
      study: input.study
    }
  });
  const bootstrapStatus = await pollWorkerJob(
    bootstrapSubmission.jobId,
    input.maxPolls
  );
  const bootstrapSummary = summaryFrom(bootstrapStatus);
  const bootstrapStudyId = bootstrapSummary?.study_id;
  const bootstrapJob = jobRecord("bootstrap", bootstrapStatus);

  if (input.workflowType === "governance.bootstrap.v1") {
    return {
      status: "SUCCEEDED",
      workflowType: input.workflowType,
      studyId: typeof bootstrapStudyId === "string" ? bootstrapStudyId : null,
      jobs: [bootstrapJob],
      summary: bootstrapSummary
    };
  }

  if (input.approvalRequired) {
    using hook = createHook<ApprovalPayload>({
      token: approvalToken(input.controlRequestId)
    });
    const approval = await hook;
    if (!approval.approved) {
      return {
        status: "REJECTED",
        approver: approval.approver,
        reason: approval.reason,
        jobs: [bootstrapJob]
      };
    }
  }

  if (typeof bootstrapStudyId !== "string" || bootstrapStudyId.length === 0) {
    throw new FatalError("Bootstrap result is missing summary.study_id");
  }

  const diagnosticSubmission = await submitWorkerJob({
    jobType: "diagnostic.intraday-v1_1-smoke.v1",
    operationId: operationId(input.idempotencyKey, "intraday-v1_1-smoke"),
    requestedBy: input.requestedBy,
    parameters: { study_id: bootstrapStudyId }
  });
  const diagnosticStatus = await pollWorkerJob(
    diagnosticSubmission.jobId,
    input.maxPolls
  );

  return {
    status: "SUCCEEDED",
    workflowType: input.workflowType,
    studyId: bootstrapStudyId,
    jobs: [bootstrapJob, jobRecord("diagnostic", diagnosticStatus)],
    summary: summaryFrom(diagnosticStatus)
  };
}
