import type { NextConfig } from "next";

// Every page in the panel is per-user and per-tenant, so nothing is prerendered
// or cached: Cache Components and partial prefetching stay off.
const nextConfig: NextConfig = {
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
