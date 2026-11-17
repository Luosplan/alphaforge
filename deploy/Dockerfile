# 多阶段构建：先编前端，再打后端镜像，最终镜像不含 Node 与源码依赖
FROM node:20-alpine AS web

WORKDIR /build
COPY packages/web/package*.json ./
RUN npm ci
COPY packages/web/ ./
RUN npm run build


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/packages/core:/app/packages/engine:/app/packages/api

WORKDIR /app

RUN pip install --no-cache-dir "fastapi>=0.115,<1" "uvicorn[standard]>=0.32,<1" "pydantic>=2,<3"

COPY packages/core ./packages/core
COPY packages/engine ./packages/engine
COPY packages/api ./packages/api
COPY --from=web /build/dist ./packages/web/dist

# 持久化目录单独挂载，容器重建不丢自选与持仓
VOLUME ["/app/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "alphaforge_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "packages/api"]
