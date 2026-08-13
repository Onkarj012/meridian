import { timingSafeEqual } from "node:crypto";

import { HookNotFoundError } from "workflow/errors";
import { resumeHook } from "workflow/api";

import { approvalToken, parseApprovalPayload } from "@/lib/contracts";
import { readControlEnv } from "@/lib/env";

function authorized(header: string, expectedToken: string): boolean {
  if (!header.startsWith("Bearer ")) {
    return false;
  }

  const supplied = Buffer.from(header.slice("Bearer ".length), "utf8");
  const expected = Buffer.from(expectedToken, "utf8");
  if (supplied.length !== expected.length) {
    return false;
  }

  return timingSafeEqual(supplied, expected);
}

export async function POST(
  request: Request,
  context: { params: Promise<{ controlRequestId: string }> }
): Promise<Response> {
  const authorization = request.headers.get("Authorization");
  if (authorization === null) {
    return Response.json({ code: "unauthorized" }, { status: 401 });
  }

  const { approvalToken: expectedToken } = readControlEnv();
  if (!authorized(authorization, expectedToken)) {
    return Response.json({ code: "forbidden" }, { status: 403 });
  }

  let payload;
  try {
    const body: unknown = await request.json();
    payload = parseApprovalPayload(body);
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

  const { controlRequestId } = await context.params;
  try {
    await resumeHook(approvalToken(controlRequestId), payload);
  } catch (error) {
    if (HookNotFoundError.is(error)) {
      return Response.json({ code: "hook_not_found" }, { status: 404 });
    }
    throw error;
  }

  return Response.json({ ok: true });
}
