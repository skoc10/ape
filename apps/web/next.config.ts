import type { NextConfig } from "next";

// Every page in the panel is per-user and per-tenant, so nothing is prerendered
// or cached: Cache Components and partial prefetching stay off.
const nextConfig: NextConfig = {
  // Self-contained server bundle for the container image.
  output: "standalone",
  poweredByHeader: false,
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
