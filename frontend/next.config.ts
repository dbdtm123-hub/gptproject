import type { NextConfig } from "next";

const config: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  experimental: {
    cpus: 2,
    // Opt in on hosts that cannot capture child-process pipes/IPC. Keep type
    // checking enabled through the TypeScript API and use thread-based workers.
    ...(process.env.AUTOLOG_BUILD_WORKER_THREADS === "1" ? {
      useTypeScriptCli: false,
      workerThreads: true,
      webpackBuildWorker: false,
    } : {}),
  },
};
export default config;
