export interface ControlEnv {
  workerUrl: string;
  workerToken: string;
  approvalToken: string;
}

function requireVariable(source: NodeJS.ProcessEnv, name: string): string {
  const value = source[name];
  if (!value) {
    throw new Error(`Missing environment variable: ${name}`);
  }
  return value;
}

function parseWorkerUrl(value: string): string {
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw new Error("Invalid MERIDIAN_WORKER_URL");
  }

  const isLocalHttp =
    url.protocol === "http:" &&
    (url.hostname === "127.0.0.1" || url.hostname === "localhost");
  if (url.protocol !== "https:" && !isLocalHttp) {
    throw new Error("Invalid MERIDIAN_WORKER_URL");
  }

  return value.replace(/\/+$/, "");
}

export function readControlEnv(
  source: NodeJS.ProcessEnv = process.env
): ControlEnv {
  const workerUrl = parseWorkerUrl(
    requireVariable(source, "MERIDIAN_WORKER_URL")
  );
  const workerToken = requireVariable(source, "MERIDIAN_WORKER_TOKEN");
  const approvalToken = requireVariable(source, "MERIDIAN_APPROVAL_TOKEN");

  return { workerUrl, workerToken, approvalToken };
}

export function readWorkerEnv(
  source: NodeJS.ProcessEnv = process.env
): Pick<ControlEnv, "workerUrl" | "workerToken"> {
  const workerUrl = parseWorkerUrl(
    requireVariable(source, "MERIDIAN_WORKER_URL")
  );
  const workerToken = requireVariable(source, "MERIDIAN_WORKER_TOKEN");

  return { workerUrl, workerToken };
}
