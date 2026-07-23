FROM node:24.18.0-bookworm-slim AS build

WORKDIR /workspace
RUN npm install --global pnpm@10.14.0

COPY web/package.json web/pnpm-lock.yaml web/pnpm-workspace.yaml ./web/
RUN cd web && pnpm install --frozen-lockfile

COPY web ./web
RUN cd web && pnpm build

FROM nginx:1.30.4-alpine

COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/web/dist /usr/share/nginx/html

EXPOSE 80 443
