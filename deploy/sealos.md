# Sealos 部署指南

本文说明如何将差旅出行助手部署到 [Sealos 应用管理](https://sealos.run/docs/guides/app-launchpad/create-app)。

## 1. 构建并推送镜像

本地验证：

```bash
cp .env.example .env
# 编辑 .env 填入 DASHSCOPE_API_KEY、JWT_SECRET

docker compose up --build
# 浏览器访问 http://localhost:8080
```

推送镜像到仓库（示例）：

```bash
docker build -t your-registry/travel-assistant:latest .
docker push your-registry/travel-assistant:latest
```

## 2. Sealos 应用管理配置

在 Sealos 控制台 → **应用管理** → **新建应用**：

| 配置项 | 值 |
|--------|-----|
| 应用名称 | travel-assistant |
| 镜像名称 | `your-registry/travel-assistant:latest` |
| 容器暴露端口 | `80` |
| 外网访问 | 开启（自动分配 HTTPS 域名） |
| CPU / 内存 | 建议 ≥ 1 核 / 2GB |
| 实例数 | **1**（SQLite checkpoint 不支持多副本并发写） |

### 持久化存储

| 配置 | 值 |
|------|-----|
| 挂载路径 | `/app/data` |
| 容量 | 5–10 GB |

挂载后以下数据在 Pod 重建后保留：

- `preferences.db` — 用户偏好与会话元数据
- `checkpoints.db` — LangGraph 对话 checkpoint
- `output/` — 各用户行程 Markdown 文件

### 环境变量

| 变量 | 必填 | 说明 |
|------|------|------|
| `DATA_DIR` | 是 | `/app/data` |
| `DASHSCOPE_API_KEY` | 是 | 通义 API Key |
| `JWT_SECRET` | 是 | JWT 签名密钥 |
| `MODEL` | 否 | 默认 `qwen-plus` |
| `ENV` | 是 | `production` |
| `OPENWEATHER_API_KEY` | 否 | 天气 MCP |

## 3. 访问方式

- 前端静态页：`https://<your-domain>/`
- API（nginx 反代）：`https://<your-domain>/api/travel/...`
- 注册：`POST /api/auth/register`
- 登录：`POST /api/auth/login`

## 4. 与 docker-compose 对照

| docker-compose | Sealos 应用管理 |
|----------------|-----------------|
| `services.travel-assistant` | 单个应用 |
| `ports: 8080:80` | 容器端口 80 + 外网访问 |
| `volumes: ./data:/app/data` | 持久化存储 → `/app/data` |
| `env_file: .env` | 环境变量配置 |
| `environment.DATA_DIR` | 同上 |

## 5. 运维注意

- 更新应用：构建新镜像 → 在 Sealos 中替换镜像地址 → 重新部署
- 备份：定期备份 `/app/data` 卷内全部文件
- 日志：Sealos 应用详情 → 日志，或进入容器终端查看 nginx / uvicorn 输出
- MCP 远程服务（高德、12306、飞常准）需容器出网可达 ModelScope 端点

## 6. 本地开发（非 Docker）

```bash
# 终端 1
python travel_agent.py --api

# 终端 2
cd front/mcp_agent && npm run dev
```

前端 `.env.development` 已配置 `VITE_API_BASE=http://localhost:8001`，直连后端，无需 nginx。
