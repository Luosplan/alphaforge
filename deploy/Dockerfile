# 多阶段构建：先编前端，再打后端镜像，最终镜像不含 Node 与源码依赖
FROM node:20-alpine AS web

WORKDIR /build
COPY packages/web/package*.json ./
RUN npm ci
COPY packages/web/ ./
RUN npm run build


FROM python:3.12-slim AS runtime

# 时区必须显式设：自动驾驶按进程本地时间判断「15:05 之后跑收盘」，
# 容器默认 UTC 会让它在北京时间 23:05 才跑，当天的委托要等第二天才撮合。
ENV TZ=Asia/Shanghai \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/packages/core:/app/packages/data:/app/packages/engine:/app/packages/api

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime \
    && echo $TZ > /etc/timezone \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY packages/core ./packages/core
COPY packages/data ./packages/data
COPY packages/engine ./packages/engine
COPY packages/api ./packages/api
COPY --from=web /build/dist ./packages/web/dist

# 持久化目录单独挂载，容器重建不丢自选与持仓
VOLUME ["/app/data"]

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "alphaforge_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "packages/api"]
