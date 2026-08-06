import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false,
  reactCompiler: true,
  // ponytail: proxy /api/* to the FastAPI backend so all requests appear
  // same-origin to the browser. Refresh cookie flows without CORS drama.
  // In prod, a real reverse proxy (Caddy/nginx) does the same job.
  async rewrites() {
    const backend = process.env.BACKEND_URL || "http://localhost:8000";
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
};

export default nextConfig;
