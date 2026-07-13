import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Proxy is handled by src/app/api/backend/[...path]/route.ts which sets
  // maxDuration=120 to support long-running ingest requests (LinkedIn ~40s + embeddings).
  async rewrites() {
    return [
      { source: "/jobs", destination: "/dashboard/jobs" },
      { source: "/jobs/:path*", destination: "/dashboard/jobs/:path*" },
      { source: "/matches", destination: "/dashboard/matches" },
      { source: "/matches/:path*", destination: "/dashboard/matches/:path*" },
      { source: "/applications", destination: "/dashboard/applications" },
      {
        source: "/applications/:path*",
        destination: "/dashboard/applications/:path*",
      },
      { source: "/settings", destination: "/dashboard/settings" },
      { source: "/settings/:path*", destination: "/dashboard/settings/:path*" },
      { source: "/resume", destination: "/dashboard/resume" },
      { source: "/resume/:path*", destination: "/dashboard/resume/:path*" },
    ];
  },
};

export default nextConfig;
