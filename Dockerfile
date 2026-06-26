

# 阶段1：构建前端
FROM node:20-alpine AS frontend
WORKDIR /build
COPY front/mcp_agent/package*.json ./
RUN npm ci
COPY front/mcp_agent/ ./
RUN npm run build

# 阶段2：构建后端依赖（Python 包）
FROM python:3.11-slim AS backend
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 阶段3：最终运行镜像（使用 nginx 官方镜像）
FROM nginx:alpine

# 安装 Python 和 pip
RUN apk add --no-cache python3 py3-pip

# 设置工作目录
WORKDIR /app

# 复制 requirements.txt
COPY requirements.txt .

# 创建虚拟环境并安装依赖
RUN python3 -m venv /app/venv \
    && . /app/venv/bin/activate \
    && pip install --no-cache-dir -r requirements.txt

# 复制所有代码
COPY . .

# 复制前端构建产物
COPY --from=frontend /build/dist /usr/share/nginx/html

# 复制 nginx 配置
COPY nginx.conf /etc/nginx/nginx.conf

# 创建数据目录
RUN mkdir -p /app/data

ENV DATA_DIR=/app/data
ENV PYTHONUNBUFFERED=1

# 设置 PATH 优先使用虚拟环境
ENV PATH="/app/venv/bin:$PATH"

EXPOSE 80

RUN chmod +x /app/start.sh

CMD ["/app/start.sh"]