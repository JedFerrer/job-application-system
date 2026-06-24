import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Proxy is handled by src/app/api/backend/[...path]/route.ts which sets
  // maxDuration=120 to support long-running ingest requests (LinkedIn ~40s + embeddings).
};

export default nextConfig;
