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
      // Dev only: `npm run dev` has no nginx in front, so Next proxies the
      // API itself. In the image nginx routes /api and /ws straight to
      // studio-api and these were never used, but they were live, and on a
      // shared host Next's port is reachable by other local users (#229), so
      // they made Next a forwarder nobody needed. studio-api would still
      // refuse what it forwarded (no X-Studio-Internal), but there's no reason
      // to have the path at all.
      if (!isDev) return [];
      const apiUrl = process.env.API_URL || "http://localhost:8080";
      return [
        { source: "/api/:path*", destination: `${apiUrl}/api/:path*` },
        { source: "/ws/:path*", destination: `${apiUrl}/ws/:path*` },
      ];
    },
  };
};

export default nextConfig;
