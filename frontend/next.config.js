/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  
  // Environment variables exposed to the browser
  env: {
    NEXT_PUBLIC_APP_NAME: 'Mela AI',
    NEXT_PUBLIC_ORG_NAME: 'Armely',
  },
  
  // Image optimization
  images: {
    domains: ['graph.microsoft.com'],
    remotePatterns: [
      {
        protocol: 'https',
        hostname: '*.sharepoint.com',
      },
    ],
  },
  
  // Headers for security
  async headers() {
    // Phase 6: tightened Content-Security-Policy.  We allow MSAL's iframe-based
    // silent-token flow (login.microsoftonline.com) and Graph fetches; everything
    // else falls back to 'self'.  'unsafe-inline' on script-src is unfortunately
    // required for Next.js inline runtime hydration; we avoid 'unsafe-eval' in
    // production but must allow it in development for react-refresh/HMR.
    const isDev = process.env.NODE_ENV === 'development';
    const csp = [
      "default-src 'self'",
      `script-src 'self' 'unsafe-inline'${isDev ? " 'unsafe-eval'" : ''} https://login.microsoftonline.com`,
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data: blob: https:",
      "font-src 'self' data:",
      `connect-src 'self' https://*.azurewebsites.net https://login.microsoftonline.com https://graph.microsoft.com https://*.applicationinsights.azure.com https://*.in.applicationinsights.azure.com wss://*.azurewebsites.net${isDev ? " http://localhost:8000 ws://localhost:8000" : ""}`,
      "frame-src 'self' https://login.microsoftonline.com",
      "frame-ancestors 'self'",
      "form-action 'self'",
      "base-uri 'self'",
      "object-src 'none'",
    ].join('; ');

    return [
      {
        source: '/:path*',
        headers: [
          {
            key: 'X-DNS-Prefetch-Control',
            value: 'on',
          },
          {
            key: 'X-Frame-Options',
            value: 'SAMEORIGIN',
          },
          {
            key: 'X-Content-Type-Options',
            value: 'nosniff',
          },
          {
            key: 'Referrer-Policy',
            value: 'strict-origin-when-cross-origin',
          },
          {
            key: 'Strict-Transport-Security',
            value: 'max-age=31536000; includeSubDomains',
          },
          {
            key: 'Permissions-Policy',
            value: 'camera=(), microphone=(self), geolocation=()',
          },
          {
            key: 'Content-Security-Policy',
            value: csp,
          },
        ],
      },
    ];
  },
  
  // Redirects
  async redirects() {
    return [
      {
        source: '/home',
        destination: '/',
        permanent: true,
      },
    ];
  },
  
  // Webpack customization
  webpack: (config, { isServer }) => {
    // Handle Azure MSAL dependencies
    if (!isServer) {
      config.resolve.fallback = {
        ...config.resolve.fallback,
        fs: false,
        net: false,
        tls: false,
      };
    }
    return config;
  },
};

module.exports = nextConfig;
