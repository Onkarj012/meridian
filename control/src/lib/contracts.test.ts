import { describe, expect, it } from "vitest";

import {
  approvalToken,
  CONTROL_REQUEST_SCHEMA,
  DEFAULT_MAX_POLLS,
  operationId,
  parseControlRequest,
  parseJobStatus
} from "./contracts";

function validControlRequest(): Record<string, unknown> {
  return {
    schemaVersion: CONTROL_REQUEST_SCHEMA,
    workflowType: "governance.bootstrap.v1",
    sourceAlias: "nse-primary",
    requestedBy: "integration-test",
    study: { universe: "NIFTY-I" },
    approvalRequired: false
  };
}

function validJobStatus(): Record<string, unknown> {
  return {
    schema_version: "meridian.job-status.v1",
    job_id: "job-1",
    job_type: "governance.bootstrap.v1",
    operation_id: "operation-1",
    state: "SUCCEEDED",
    attempt: 1,
    not_before: null,
    created_at: "2026-01-01T00:00:00.000Z",
    updated_at: "2026-01-01T00:00:01.000Z",
    result: { summary: { study_id: "study-1" } },
    failure: null
  };
}

describe("control contracts", () => {
  it("rejects missing, short, and invalid Idempotency-Key values", () => {
    expect(() => parseControlRequest(validControlRequest(), null)).toThrow(
      "Idempotency-Key"
    );
    expect(() => parseControlRequest(validControlRequest(), "short")).toThrow(
      "Idempotency-Key"
    );
    expect(() =>
      parseControlRequest(validControlRequest(), "invalid key")
    ).toThrow("Idempotency-Key");
  });

  it("rejects the wrong control request schemaVersion", () => {
    expect(() =>
      parseControlRequest(
        { ...validControlRequest(), schemaVersion: "meridian.control-request.v0" },
        "request-key-001"
      )
    ).toThrow("schemaVersion");
  });

  it("rejects an unsupported workflowType", () => {
    expect(() =>
      parseControlRequest(
        { ...validControlRequest(), workflowType: "unsupported.workflow.v1" },
        "request-key-002"
      )
    ).toThrow("workflowType");
  });

  it("enforces maxPolls bounds and applies the default", () => {
    expect(
      parseControlRequest(validControlRequest(), "request-key-003").maxPolls
    ).toBe(DEFAULT_MAX_POLLS);
    expect(() =>
      parseControlRequest(
        { ...validControlRequest(), maxPolls: 0 },
        "request-key-004"
      )
    ).toThrow("maxPolls");
    expect(() =>
      parseControlRequest(
        { ...validControlRequest(), maxPolls: 5001 },
        "request-key-005"
      )
    ).toThrow("maxPolls");
  });

  it("builds operation and approval token shapes", () => {
    expect(operationId("request-key-006", "bootstrap")).toBe(
      "request-key-006:bootstrap"
    );
    expect(approvalToken("control-1")).toBe(
      "meridian:approval:v1:control-1"
    );
  });

  it("rejects an unknown job state and a missing job_id", () => {
    expect(() =>
      parseJobStatus({ ...validJobStatus(), state: "CANCELLED" })
    ).toThrow("state");
    const { job_id: omitted, ...withoutJobId } = validJobStatus();
    void omitted;
    expect(() => parseJobStatus(withoutJobId)).toThrow("job_id");
  });
});
