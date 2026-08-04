# Vite bakes VITE_* vars into the built JS at `npm run build` time, not at
# container start - an env var set on the running container is too late.
# So these come in as build ARGs (set as Coolify "build variables", not
# runtime env vars) and get written into .env.production before the build.
# The checked-in .env still points at the old api.futhub.co.uk - that's
# fine as a local-dev default, but it must NOT be what ships in this image,
# which is why the build step overwrites it rather than relying on it.
FROM node:22-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .

ARG VITE_API_URL
ARG VITE_API_BASE_URL
RUN { \
      echo "VITE_API_URL=${VITE_API_URL}"; \
      echo "VITE_API_BASE_URL=${VITE_API_BASE_URL}"; \
    } > .env.production

RUN npm run build

FROM node:22-slim
WORKDIR /app
RUN npm install -g serve@14
COPY --from=build /app/dist ./dist

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "serve -s dist -l ${PORT:-8080} --single"]
