# Build with the UI folder as the context:  docker build -t ui:<git-sha> services/ui
# Multi-stage, non-root (nginx-unprivileged runs as uid 101 on port 8080), pinned bases, no `latest`.
FROM node:22.21.1-alpine3.22 AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY . .
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.29.3-alpine
# Trivy CRITICAL (CVE-2026-31789) in the base's OpenSSL 3.5.4-r0, fixed in 3.5.6-r0: upgrade just those
# two packages as root, then drop back to the image's non-root user. Remove when the base tag is bumped.
USER root
RUN apk upgrade --no-cache libcrypto3 libssl3
COPY --chown=101:101 nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --chown=101:101 --chmod=0755 nginx/40-runtime-config.sh /docker-entrypoint.d/40-runtime-config.sh
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html
ENV API_BASE_URL=/api
USER 101
EXPOSE 8080
