import type { NextConfig } from "next";
import { withWorkflow } from "workflow/next";

const nextConfig: NextConfig = {
  // Workflow configuration is enabled by the wrapper.
};

export default withWorkflow(nextConfig);
