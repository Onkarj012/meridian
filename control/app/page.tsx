export default function Page() {
  return (
    <main>
      <h1>MERIDIAN Control</h1>
      <p>
        This service is the durable control plane; all computation runs in the MERIDIAN Python worker.
      </p>
      <h2>Supported workflow types</h2>
      <ul>
        <li><code>governance.bootstrap.v1</code></li>
        <li><code>diagnostic.intraday-v1_1-smoke.v1</code></li>
      </ul>
      <h2>Endpoints</h2>
      <ul>
        <li><code>POST /api/runs</code></li>
        <li><code>GET /api/runs/{"{"}runId{"}"}</code></li>
        <li><code>POST /api/approvals/{"{"}controlRequestId{"}"}</code></li>
      </ul>
      <h2>Notes</h2>
      <ul>
        <li>POST /api/runs requires an Idempotency-Key header.</li>
        <li>Any other workflow type is rejected.</li>
        <li>Research phases 1-6 are not implemented.</li>
      </ul>
    </main>
  );
}
