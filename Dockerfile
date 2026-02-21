# ============================================
# Ultra Suite — Dockerfile
# Multi-stage build for production deployment
# ============================================

# ─── Stage 1: Build ──────────────────────────
FROM node:20-alpine AS builder

WORKDIR /app

# Install build dependencies for native modules (canvas, sodium)
RUN apk add --no-cache python3 make g++ pkgconfig pixman-dev cairo-dev pango-dev libjpeg-turbo-dev giflib-dev

COPY package*.json ./
RUN npm ci --production=false

# ─── Stage 2: Production ─────────────────────
FROM node:20-alpine

LABEL maintainer="Ultra Suite Team"
LABEL description="Ultra Suite Discord Bot"

WORKDIR /app

# Runtime dependencies for canvas and audio
RUN apk add --no-cache \
    pixman cairo pango libjpeg-turbo giflib \
    ffmpeg opus-dev \
    dumb-init

# Copy node_modules from builder
COPY --from=builder /app/node_modules ./node_modules

# Copy application code
COPY . .

# Create data directories
RUN mkdir -p data logs

# Non-root user for security
RUN addgroup -g 1001 -S botuser && \
    adduser -S botuser -u 1001 -G botuser && \
    chown -R botuser:botuser /app

USER botuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD node -e "const http = require('http'); http.get('http://localhost:${API_PORT:-3000}/health', (r) => process.exit(r.statusCode === 200 ? 0 : 1)).on('error', () => process.exit(1))"

# Entrypoint with dumb-init for proper signal handling
ENTRYPOINT ["dumb-init", "--"]
CMD ["node", "index.js"]
