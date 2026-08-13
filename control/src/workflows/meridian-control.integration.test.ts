import { getRun, resumeHook, start } from "workflow/api";
import { waitForHook, waitForSleep } from "@workflow/vitest";
import { afterAll, beforeAll, describe, expect, it } from "vitest";

import {
  approvalToken,
  CONTROL_REQUEST_SCHEMA,
  type ControlRunInput,
  type WorkflowType
} from "../lib/contracts";
import {
  startFixtureWorker,
  type FixtureWorker
} from "../../test/fixture-worker-server";
import { meridianControlWorkflow } from "./meridian-control";

let inputSequence = 0;

function makeInput(
  workflowType: WorkflowType = "governance.bootstrap.v1",
  overrides: Partial<ControlRunInput> = {}
): ControlRunInput {
  inputSequence += 1;
  return {
    schemaVersion: CONTROL_REQUEST_SCHEMA,
    controlRequestId: `control-integration-${inputSequence}`,
    idempotencyKey: `integration-key-${inputSequence}`,
    workflowType,
    sourceAlias: "nse-primary",
    requestedBy: "workflow-integration-test",
    study: { universe: "NIFTY-I" },
    approvalRequired: false,
    maxPolls: 10,
    ...overrides
  };
}

describe("meridianControlWorkflow", () => {
  let worker: FixtureWorker;

  beforeAll(async () => {
    worker = await startFixtureWorker();
    process.env.MERIDIAN_WORKER_URL = worker.url;
    process.env.MERIDIAN_WORKER_TOKEN = worker.token;
  });

  afterAll(async () => {
    await worker.close();
    delete process.env.MERIDIAN_WORKER_URL;
    delete process.env.MERIDIAN_WORKER_TOKEN;
  });

  it("completes bootstrap and returns its study id", async () => {
    worker.script("governance.bootstrap.v1", {
      states: ["SUCCEEDED"],
      result: { summary: { study_id: "study-bootstrap-1", rows: 120 } }
    });
    const run = await start(meridianControlWorkflow, [makeInput()]);

    await expect(run.returnValue).resolves.toEqual({
      status: "SUCCEEDED",
      workflowType: "governance.bootstrap.v1",
      studyId: "study-bootstrap-1",
      jobs: [
        {
          role: "bootstrap",
          jobId: expect.stringMatching(/^job-/),
          state: "SUCCEEDED"
        }
      ],
      summary: { study_id: "study-bootstrap-1", rows: 120 }
    });
  });

  it("polls PENDING and RUNNING states until SUCCEEDED", async () => {
    worker.script("governance.bootstrap.v1", {
      states: ["PENDING", "RUNNING", "SUCCEEDED"],
      result: { summary: { study_id: "study-polled" } }
    });
    const pollsBefore = worker.polls;
    const run = await start(meridianControlWorkflow, [makeInput()]);

    const firstSleepId = await waitForSleep(run);
    await getRun(run.runId).wakeUp({ correlationIds: [firstSleepId] });
    const secondSleepId = await waitForSleep(run);
    await getRun(run.runId).wakeUp({ correlationIds: [secondSleepId] });

    await expect(run.returnValue).resolves.toMatchObject({
      status: "SUCCEEDED",
      studyId: "study-polled"
    });
    expect(worker.polls - pollsBefore).toBeGreaterThan(1);
  });

  it("submits both jobs and passes the bootstrap study_id to diagnostics", async () => {
    worker.script("governance.bootstrap.v1", {
      states: ["SUCCEEDED"],
      result: { summary: { study_id: "study-diagnostic-input" } }
    });
    worker.script("diagnostic.intraday-v1_1-smoke.v1", {
      states: ["SUCCEEDED"],
      result: { summary: { checks_passed: 8 } }
    });
    const submissionsBefore = worker.submissions.length;
    const input = makeInput("diagnostic.intraday-v1_1-smoke.v1");
    const run = await start(meridianControlWorkflow, [input]);
    const result = await run.returnValue;

    expect(result).toMatchObject({
      status: "SUCCEEDED",
      studyId: "study-diagnostic-input",
      summary: { checks_passed: 8 }
    });
    expect(worker.submissions.slice(submissionsBefore)).toEqual([
      {
        jobType: "governance.bootstrap.v1",
        operationId: `${input.idempotencyKey}:bootstrap`
      },
      {
        jobType: "diagnostic.intraday-v1_1-smoke.v1",
        operationId: `${input.idempotencyKey}:intraday-v1_1-smoke`
      }
    ]);
  });

  it("pauses on the deterministic approval hook and continues when approved", async () => {
    worker.script("governance.bootstrap.v1", {
      states: ["SUCCEEDED"],
      result: { summary: { study_id: "study-approved" } }
    });
    worker.script("diagnostic.intraday-v1_1-smoke.v1", {
      states: ["SUCCEEDED"],
      result: { summary: { approved_run: true } }
    });
    const input = makeInput("diagnostic.intraday-v1_1-smoke.v1", {
      approvalRequired: true
    });
    const token = approvalToken(input.controlRequestId);
    const run = await start(meridianControlWorkflow, [input]);

    const hook = await waitForHook(run, { token });
    expect(hook.token).toBe(token);
    await resumeHook(token, {
      approved: true,
      approver: "risk-owner",
      reason: "checks complete"
    });

    await expect(run.returnValue).resolves.toMatchObject({
      status: "SUCCEEDED",
      studyId: "study-approved"
    });
  });

  it("returns REJECTED without submitting diagnostics", async () => {
    worker.script("governance.bootstrap.v1", {
      states: ["SUCCEEDED"],
      result: { summary: { study_id: "study-rejected" } }
    });
    worker.script("diagnostic.intraday-v1_1-smoke.v1", {
      states: ["SUCCEEDED"],
      result: { summary: { should_not_run: true } }
    });
    const submissionsBefore = worker.submissions.length;
    const input = makeInput("diagnostic.intraday-v1_1-smoke.v1", {
      approvalRequired: true
    });
    const token = approvalToken(input.controlRequestId);
    const run = await start(meridianControlWorkflow, [input]);

    await waitForHook(run, { token });
    await resumeHook(token, {
      approved: false,
      approver: "risk-owner",
      reason: "risk budget exceeded"
    });

    await expect(run.returnValue).resolves.toMatchObject({
      status: "REJECTED",
      approver: "risk-owner",
      reason: "risk budget exceeded"
    });
    expect(worker.submissions.slice(submissionsBefore)).toHaveLength(1);
    expect(worker.submissions.at(-1)?.jobType).toBe("governance.bootstrap.v1");
  });

  it("fails the run for a terminal FAILED worker job", async () => {
    worker.script("governance.bootstrap.v1", {
      states: ["FAILED"],
      failure: { code: "BOOTSTRAP_FAILED", message: "fixture failure" }
    });
    const run = await start(meridianControlWorkflow, [makeInput()]);

    await expect(run.returnValue).rejects.toThrow("BOOTSTRAP_FAILED");
  });

  it("fails unsupported workflow types before fixture submission", async () => {
    const submissionsBefore = worker.submissions.length;
    const input = makeInput(
      "unsupported.workflow.v1" as WorkflowType
    );
    const run = await start(meridianControlWorkflow, [input]);

    await expect(run.returnValue).rejects.toThrow("Unsupported workflowType");
    expect(worker.submissions).toHaveLength(submissionsBefore);
  });
});
