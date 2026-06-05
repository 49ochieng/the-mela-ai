# =============================================================================
# Mela AI - Frontend Dockerfile
# Multi-stage build for Next.js 14 application with standalone output
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: Dependencies - install node modules
# ---------------------------------------------------------------------------
FROM node:20-alpine AS deps

WORKDIR /app

# Install libc6-compat for Alpine compatibility with some npm packages
RUN apk add --no-cache libc6-compat

# Copy package files for dependency installation
COPY package.json package-lock.json* yarn.lock* pnpm-lock.yaml* ./

# Install dependencies based on the available lock file
RUN \
    if [ -f yarn.lock ]; then yarn install --frozen-lockfile; \
    elif [ -f package-lock.json ]; then npm ci; \
    elif [ -f pnpm-lock.yaml ]; then corepack enable pnpm && pnpm install --frozen-lockfile; \
    else npm install; \
    fi

# ---------------------------------------------------------------------------
# Stage 2: Builder - build the Next.js application
# ---------------------------------------------------------------------------
FROM node:20-alpine AS builder

WORKDIR /app

# Copy dependencies from the deps stage
COPY --from=deps /app/node_modules ./node_modules

# Copy all source files
COPY . .

# Disable Next.js telemetry during build
ENV NEXT_TELEMETRY_DISABLED=1

# Build the application (output: 'standalone' is set in next.config.js)
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 3: Runner - minimal production image
# ---------------------------------------------------------------------------
FROM node:20-alpine AS runner

WORKDIR /app

# Set production environment
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

# Create a non-root user for security
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# Copy the public assets
COPY --from=builder /app/public ./public

# Set correct permissions for the Next.js cache directory
RUN mkdir .next && chown nextjs:nodejs .next

# Copy the standalone build output and static files
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

# Switch to non-root user
USER nextjs

# Expose the Next.js port
EXPOSE 3000

# Set the hostname to listen on all interfaces
ENV HOSTNAME="0.0.0.0"
ENV PORT=3000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD wget --no-verbose --tries=1 --spider http://localhost:3000/ || exit 1

# Start the Next.js standalone server
CMD ["node", "server.js"]
