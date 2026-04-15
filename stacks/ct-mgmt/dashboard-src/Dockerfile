FROM oven/bun:slim AS build
WORKDIR /app
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile
COPY . .
RUN bun run build

FROM oven/bun:slim
WORKDIR /app
# Production deps only (preact + preact-render-to-string for SSR)
COPY package.json bun.lock ./
RUN bun install --frozen-lockfile --production
COPY --from=build /app/dist ./dist
COPY src ./src
COPY server.js tsconfig.json ./
EXPOSE 3000
CMD ["bun", "server.js"]
