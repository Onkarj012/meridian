import { FatalError, RetryableError } from "workflow";
import { afterEach, beforeEach, describe, expect, it } from "vitest";

import {
  startFixtureWorker,
  type FixtureWorker
} from "../../test/fixture-worker-server";
import { submitWorkerJob } from "./worker-client";

function bootstrapRequest(operationId: string) {
  return {
    jobType: "governance.bootstrap.v1" as const,
    operationId,
    requestedBy: "unit-test",
    parameters: { source_alias: "nse-primary", study: { universe: "NIFTY-I" } }
  };
}

describe("worker client steps", () => {
  let worker: FixtureWorker;

  beforeEach(async () => {
    worker = await startFixtureWorker();
    process.env.MERIDIAN_WORKER_URL = worker.url;
    process.env.MERIDIAN_WORKER_TOKEN = worker.token;
  });

  afterEach(async () => {
    await worker.close();
    delete process.env.MERIDIAN_WORKER_URL;
    delete process.env.MERIDIAN_WORKER_TOKEN;
  });

  it("returns a parsed successful submission", async () => {
    const submission = await submitWorkerJob(bootstrapRequest("unit-submit-001"));

    expect(submission).toEqual({
      jobId: expect.stringMatching(/^job-/),
      created: true,
      state: "PENDING"
    });
  });

  it("reports created false for a repeated operation_id", async () => {
    const request = bootstrapRequest("unit-repeat-001");
    const first = await submitWorkerJob(request);
    const second = await submitWorkerJob(request);

    expect(second).toEqual({ ...first, created: false });
  });

  it("maps a 422 response to FatalError", async () => {
    worker.script("governance.bootstrap.v1", {
      states: ["SUCCEEDED"],
      submitStatus: 422
    });

    await expect(
      submitWorkerJob(bootstrapRequest("unit-fatal-001"))
    ).rejects.toSatisfy((error: unknown) => FatalError.is(error));
  });

  it("maps a 500 response to RetryableError", async () => {
    worker.script("governance.bootstrap.v1", {
      states: ["SUCCEEDED"],
      submitStatus: 500
    });

    await expect(
      submitWorkerJob(bootstrapRequest("unit-retry-001"))
    ).rejects.toSatisfy((error: unknown) => RetryableError.is(error));
  });

  it("maps a 429 response to RetryableError with retryAfter", async () => {
    worker.script("governance.bootstrap.v1", {
      states: ["SUCCEEDED"],
      submitStatus: 429
    });

    const startedAt = Date.now();
    try {
      await submitWorkerJob(bootstrapRequest("unit-rate-limit-001"));
      throw new Error("Expected submitWorkerJob to reject");
    } catch (error) {
      expect(RetryableError.is(error)).toBe(true);
      if (RetryableError.is(error)) {
        expect(error.retryAfter.getTime()).toBeGreaterThanOrEqual(startedAt + 29_000);
      }
    }
  });

  it("maps a malformed success body to FatalError", async () => {
    worker.script("governance.bootstrap.v1", {
      states: ["SUCCEEDED"],
      submitBody: { schema_version: "unexpected" }
    });

    await expect(
      submitWorkerJob(bootstrapRequest("unit-malformed-001"))
    ).rejects.toSatisfy((error: unknown) => FatalError.is(error));
  });
});
