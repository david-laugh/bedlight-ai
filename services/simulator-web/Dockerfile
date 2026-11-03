# BedLight AI — simulator-web
# 현재: PLACEHOLDER. 정적 안내 페이지(placeholder/index.html)만 제공한다.
#
# 향후 React + Vite 도입 시 multi-stage 로 교체:
#   FROM node:20-alpine AS build
#   WORKDIR /app
#   COPY package*.json ./
#   RUN npm ci
#   COPY . .
#   RUN npm run build
#   FROM nginx:1.27-alpine
#   COPY --from=build /app/dist /usr/share/nginx/html
FROM nginx:1.27-alpine

COPY placeholder/ /usr/share/nginx/html/

LABEL org.opencontainers.image.title="bedlight-simulator-web" \
      org.opencontainers.image.description="PLACEHOLDER static page - simulator not implemented" \
      bedlight.status="placeholder"

EXPOSE 80
