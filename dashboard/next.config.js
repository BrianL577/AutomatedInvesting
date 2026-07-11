/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Ensure data/trades.json is bundled with the serverless functions on
  // Vercel even though the read path is constructed at runtime.
  experimental: {
    outputFileTracingIncludes: {
      "/api/trades": ["./data/trades.json"],
      "/": ["./data/trades.json"],
      "/api/backtest": ["./data/sample_bars.json"],
    },
  },
};

module.exports = nextConfig;
