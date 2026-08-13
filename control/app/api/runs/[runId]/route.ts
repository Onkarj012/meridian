import { getRun } from "workflow/api";

export async function GET(
  request: Request,
  context: { params: Promise<{ runId: string }> }
): Promise<Response> {
  void request;
  const { runId } = await context.params;
  const run = getRun(runId);

  if (!(await run.exists)) {
    return Response.json({ code: "run_not_found" }, { status: 404 });
  }

  const [status, workflowName, createdAt, completedAt] = await Promise.all([
    run.status,
    run.workflowName,
    run.createdAt,
    run.completedAt
  ]);

  return Response.json({
    runId,
    status,
    workflowName,
    createdAt,
    completedAt
  });
}
