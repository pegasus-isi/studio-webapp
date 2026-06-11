import type { NextConfig } from "next";
import { PHASE_DEVELOPMENT_SERVER } from "next/constants";

const nextConfig = (phase: string): NextConfig => {
  const isDev = phase === PHASE_DEVELOPMENT_SERVER;

  return {
    output: "standalone",
    // The app is served below a per-user prefix in KubeSpawner
    // (/user/<name>). Next's basePath is build-time only, so emit relative
    // static asset URLs and let the runtime <base> tag define the app root.
    assetPrefix: isDev ? undefined : ".",
    async rewrites() {
      // In dev mode, proxy API calls to the FastAPI backend
      const apiUrl = process.env.API_URL || "http://localhost:8080";
      return [
        { source: "/api/:path*", destination: `${apiUrl}/api/:path*` },
        { source: "/ws/:path*", destination: `${apiUrl}/ws/:path*` },
      ];
    },
  };
};

export default nextConfig;
