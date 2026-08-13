import { start } from "workflow/api";

import { parseControlRequest } from "@/lib/contracts";
import { meridianControlWorkflow } from "@/workflows/meridian-control";

export const dynamic = "force-dynamic";

export async function POST(request: Request): Promise<Response> {
  let input;
  try {
    const body: unknown = await request.json();
    input = parseControlRequest(
      body,
      request.headers.get("Idempotency-Key")
    );
  } catch (error) {
    const message =
      error instanceof SyntaxError
        ? "Request body must be valid JSON"
        : error instanceof Error
          ? error.message
          : "Invalid request";
    return Response.json(
      { code: "invalid_request", message },
      { status: 400 }
    );
  }

  const controlRequestId = crypto.randomUUID();
  input.controlRequestId = controlRequestId;
  const run = await start(meridianControlWorkflow, [input]);

  return Response.json(
    {
      schemaVersion: "meridian.control-run.v1",
      runId: run.runId,
      controlRequestId,
      workflowType: input.workflowType,
      statusUrl: `/api/runs/${run.runId}`
    },
    { status: 202 }
  );
}
