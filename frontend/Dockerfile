# =============================================================================
# The Watchtower — Next.js Frontend Dockerfile
# Multi-stage build: base → development → production
# =============================================================================

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1: base — Node.js LTS with dependencies installed
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS base

LABEL maintainer="The Watchtower Team"
LABEL description="The Watchtower Next.js Frontend"

# Install libc compatibility for Alpine (required by some native modules)
RUN apk add --no-cache libc6-compat curl

WORKDIR /app

# Copy package files first for optimal layer caching
COPY package.json ./

# Install dependencies — using npm install because package-lock.json does not
# exist yet (first build). After the first run, commit the generated lockfile
# and switch back to "npm ci --legacy-peer-deps" for reproducible installs.
RUN npm install --legacy-peer-deps

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2: development — hot-reload Next.js dev server
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS development

ENV NODE_ENV=development \
    NEXT_TELEMETRY_DISABLED=1

# Copy full source — volumes override in docker-compose for live reload
COPY . .

EXPOSE 3000

HEALTHCHECK --interval=20s --timeout=10s --start-period=60s --retries=5 \
    CMD curl -f http://localhost:3000 || exit 1

CMD ["npm", "run", "dev"]

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3: builder — compile Next.js production bundle
# ─────────────────────────────────────────────────────────────────────────────
FROM base AS builder

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

COPY . .

RUN npm run build

# ─────────────────────────────────────────────────────────────────────────────
# Stage 4: production — minimal runtime image
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-alpine AS production

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1

WORKDIR /app

RUN apk add --no-cache curl

# Create non-root user
RUN addgroup --system --gid 1001 nodejs && \
    adduser --system --uid 1001 nextjs

# Copy only the Next.js standalone output for a minimal production image
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static
COPY --from=builder --chown=nextjs:nodejs /app/public ./public

USER nextjs

EXPOSE 3000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:3000 || exit 1

CMD ["node", "server.js"]
