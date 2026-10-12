import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  /* config options here */
  reactCompiler: true,
  async redirects() {
    return [
      {
        source: "/my-puzzles/:path*",
        destination: "/puzzle-extractor/:path*",
        permanent: true,
      },
    ];
  },
};

export default nextConfig;
