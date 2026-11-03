FROM node:20.18-alpine AS build
WORKDIR /web
COPY package.json package-lock.json ./
COPY scripts ./scripts
RUN npm ci --no-audit --no-fund
COPY . .
# Public origin for canonical URLs, sitemap and share images, e.g. --build-arg VITE_SITE_URL=https://flaria.health
ARG VITE_SITE_URL
ARG FLARIA_SAME_AS=""
ARG FLARIA_GOOGLE_SITE_VERIFICATION=""
ARG FLARIA_BING_SITE_VERIFICATION=""
ENV VITE_SITE_URL=$VITE_SITE_URL FLARIA_SAME_AS=$FLARIA_SAME_AS \
    FLARIA_GOOGLE_SITE_VERIFICATION=$FLARIA_GOOGLE_SITE_VERIFICATION FLARIA_BING_SITE_VERIFICATION=$FLARIA_BING_SITE_VERIFICATION
# The face model is required; images fall back to their remote sources if a fetch fails.
RUN npm run assets || true
RUN test -s public/mediapipe/face_landmarker.task || (echo "face_landmarker.task missing: run 'npm run assets' with network access" && exit 1)
RUN npm run build

FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY security-headers.conf /etc/nginx/snippets/security-headers.conf
COPY --from=build /web/dist /usr/share/nginx/html
EXPOSE 8080
