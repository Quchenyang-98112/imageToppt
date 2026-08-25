import type { NextConfig } from 'next';

const nextConfig: NextConfig = {
  serverExternalPackages: ['pptxgenjs'],
  outputFileTracingExcludes: {
    '*': ['**/data/**', '**/Image repository/**', '**/.env.local', '**/node_modules/**'],
  },
};

export default nextConfig;
