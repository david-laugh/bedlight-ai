# BedLight AI — simulator-web
# Static dashboard (vanilla HTML/JS, no build step) that renders controller state over WebSocket.
# React + Vite migration is still planned; the old placeholder page is kept under placeholder/.
FROM nginx:1.27-alpine

COPY site/ /usr/share/nginx/html/

LABEL org.opencontainers.image.title="bedlight-simulator-web" bedlight.status="static-dashboard"

EXPOSE 80
