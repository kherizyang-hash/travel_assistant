# syntax=docker/dockerfile:1

FROM node:20-alpine AS frontend
WORKDIR /build
COPY front/mcp_agent/package*.json ./
RUN npm ci
COPY front/mcp_agent/ ./
RUN npm run build

FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends nginx \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=frontend /build/dist /app/static
COPY nginx.conf /etc/nginx/nginx.conf
RUN chmod +x /app/start.sh && mkdir -p /app/data

ENV DATA_DIR=/app/data
ENV PYTHONUNBUFFERED=1

EXPOSE 80

CMD ["/app/start.sh"]
