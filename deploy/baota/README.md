# 宝塔部署

单机单进程部署：一个 uvicorn 进程同时提供接口与前端产物，Nginx 只做反代。
没有 Redis / MySQL / Celery——这个项目的数据全在 `data/` 下的 SQLite 与几个 JSON。

## 0. 前置

| 项 | 要求 | 说明 |
| --- | --- | --- |
| 系统 | Linux（CentOS 7+ / Ubuntu 20.04+） | 宝塔支持的系统都行 |
| 配置 | 2 核 4G、磁盘 ≥ 20G | 行情库 ~830MB，因子表扫描吃内存 |
| Python | 3.11 或 3.12 | 宝塔「Python 项目管理器」里装 |
| 端口 | 8000（内网） | 不要对公网开放，只给 Nginx 反代 |
| 时区 | **必须 Asia/Shanghai** | 见下面第 1 条，错了自动驾驶会在凌晨跑 |

## 1. 先设时区（最容易漏，错了收盘任务全乱）

宝塔面板 → 面板设置 → 时区 → `Asia/Shanghai`，或者 SSH：

```bash
timedatectl set-timezone Asia/Shanghai
date   # 确认输出是 CST
```

自动驾驶判断「15:05 之后跑收盘任务」用的是进程本地时间。服务器是 UTC 的话，
它会在北京时间 23:05 才跑，当天的委托要等到第二天才撮合。

## 2. 上传代码

宝塔 → 文件 → 进入 `/www/wwwroot` → 新建目录 `alphaforge` → 上传项目压缩包 → 解压。

目录结构应该是：

```
/www/wwwroot/alphaforge/
├── packages/{core,data,engine,api,web}
├── deploy/
├── requirements.txt
├── data/              # 行情库与账户状态
└── .env
```

**不要**把本地的 `node_modules`、`.git`、`__pycache__` 传上去。

## 3. Python 环境与依赖

宝塔 → 软件商店 → 搜索「Python 项目管理器」→ 安装 → 在里面装一个 **3.12**。

装好后 SSH 进项目目录跑一次初始化（建 venv、装依赖、检查产物）：

```bash
cd /www/wwwroot/alphaforge
AF_PY=/www/server/pyporject_evn/versions/3.12.0/bin/python3 bash deploy/baota/setup.sh
```

`AF_PY` 的路径按宝塔实际安装位置改，脚本跑完会打印缺什么。

不想用脚本也行，手工三条：

```bash
cd /www/wwwroot/alphaforge
python3.12 -m venv venv
./venv/bin/pip install -r requirements.txt
```

## 4. 前端产物

后端只托管 `packages/web/dist`，服务器上没有这个目录，打开首页会看到一段提示 JSON。

两种做法，选一个：

**A. 本地构建后上传（推荐，服务器不装 Node）**

```bash
# 本地
npm --prefix packages/web run build
```

把 `packages/web/dist` 整个目录传到 `/www/wwwroot/alphaforge/packages/web/dist`。

**B. 服务器构建**

```bash
cd /www/wwwroot/alphaforge/packages/web
npm ci && npm run build
```

## 5. 数据

`data/` 里有四类东西，作用完全不同：

| 文件 | 作用 | 丢了会怎样 |
| --- | --- | --- |
| `alphaforge.db` | 日线、因子、指数、快照 | 打不开页面，要重新全量同步 |
| `auth.json` | 登录凭证（PBKDF2 哈希） | 回到默认 admin/admin，登录后强制改密码 |
| `store.json` | 自选、持仓、购买权限 | 个人设置清空 |
| `lab.json` | 模拟盘账户、回测、进化结论 | 两个账户重置为 100 万，参数回策略默认 |
| `autopilot.json` | 自动驾驶开关、复盘日记 | 开关键与历史复盘丢失 |

**做法 A：从本地传（推荐，数据完全一致）**

先关掉本地服务，然后做一次 WAL 合并，保证只传一个 `.db` 文件就是完整的：

```bash
# 本地
python -c "import sqlite3;c=sqlite3.connect('data/alphaforge.db');c.execute('PRAGMA wal_checkpoint(TRUNCATE)');c.close()"
```

压缩后上传（830MB → 约 200MB）：

```bash
# 本地
tar -czf alphaforge-data.tar.gz data/
```

宝塔文件管理上传到 `/www/wwwroot/alphaforge/` 后解压覆盖。

**做法 B：服务器首次全量同步（约 19 分钟，需要能访问东财 / 新浪）**

```bash
cd /www/wwwroot/alphaforge
PYTHONPATH=packages/core:packages/data:packages/engine \
  ./venv/bin/python -m alphaforge_data sync --only all --workers 8
```

之后每天的增量同步由自动驾驶在收盘后自动完成，不需要配 crontab。

## 6. 环境变量

```bash
cp .env.example .env
```

编辑 `.env`，生产至少要改这三项：

```ini
# AI 点评/复盘用，不填就退回规则版，功能照常
AF_LLM_BASE_URL=https://api.deepseek.com/v1
AF_LLM_API_KEY=sk-xxxx
AF_LLM_MODEL=deepseek-chat

# 只留自己的域名，别用 *
AF_CORS_ORIGINS=https://你的域名.com

# 数据库与状态文件路径（默认就是仓库内 data/，用 bind 目录备份更方便时改成绝对路径）
AF_DB=/www/wwwroot/alphaforge/data/alphaforge.db
```

`AF_AUTH_DISABLED=1` 是本地工装跳过登录用的，**生产不要开**。

## 7. 启动

### 方式一：宝塔「Python 项目管理器」（图形化，推荐）

添加项目，关键字段：

| 字段 | 值 |
| --- | --- |
| 项目名称 | alphaforge |
| 项目路径 | `/www/wwwroot/alphaforge` |
| Python 版本 | 3.12 |
| 框架 | 其他（FastAPI / 自定义） |
| 启动方式 | 自定义命令 |
| 启动命令 | `venv/bin/uvicorn alphaforge_api.main:app --host 127.0.0.1 --port 8000 --app-dir packages/api` |
| 运行用户 | root |
| 项目端口 | 8000 |
| 是否安装依赖 | 是（会自动读 requirements.txt） |
| 开机自启 | 是 |

如果面板里填不了 `PYTHONPATH`，把启动命令改成：

```bash
PYTHONPATH=packages/core:packages/data:packages/engine venv/bin/uvicorn alphaforge_api.main:app --host 127.0.0.1 --port 8000 --app-dir packages/api
```

**必须单进程**。这个项目没有多 worker 的横向扩展能力：自动驾驶是进程内线程，
多开一个 worker 就会有两个线程同时挂单、同时写 JSON；SQLite 也会互相等锁。

### 方式二：Supervisor（更稳，日志好查）

宝塔 → 软件商店 → Supervisor 管理器 → 添加守护进程：

- 名称：`alphaforge`
- 运行目录：`/www/wwwroot/alphaforge`
- 启动命令：`/www/wwwroot/alphaforge/venv/bin/uvicorn alphaforge_api.main:app --host 127.0.0.1 --port 8000 --app-dir packages/api`
- 用户：root

或者直接把 `deploy/baota/supervisor.conf` 贴进 Supervisor 的配置目录再 reload。
其中已经带上了 `PYTHONPATH` 与 `TZ=Asia/Shanghai`。

## 8. Nginx 反代与 HTTPS

uvicorn 默认监听 `127.0.0.1:8000`，这个地址只有服务器自己连得上，公网进不来。
对外访问有三条路，按域名备案状态选：

| 方式 | 地址 | 需要做的事 | 适用 |
| --- | --- | --- | --- |
| 本机自测 | `http://127.0.0.1:8000` | 无 | SSH 里 curl，验证服务活没活 |
| 公网 IP + 端口 | `http://<公网IP>:8000` | 启动参数改 `--host 0.0.0.0`，安全组与宝塔放行 8000 | 不备案、纯自用 |
| 域名 80 | `http://你的域名` | 建站点 + 反向代理，见下 | 域名已备案，且内容性质过得了审核 |

`--host 0.0.0.0` 会把 8000 直接摊到公网，没有 TLS，登录密码是明文传输。两个必做的补救：
安全组的授权对象填自己宽带的出口 IP（`x.x.x.x/32`）而不是 `0.0.0.0/0`；进面板第一件事改掉
`admin/admin`。比 IP 直连更安全的做法是 SSH 隧道，8000 只监听回环：

```bash
ssh -N -L 8000:127.0.0.1:8000 root@<公网IP>
```

宝塔有两个入口能做这件事，**只走一条**——两个都建会生成两个 `server` 块抢同一域名。

### 方式一：反向代理项目（新版宝塔，字段少，推荐）

网站 → 反向代理项目 → 添加反向代理项目：

| 字段 | 值 |
| --- | --- |
| 域名 | `money.vw50.cn`（默认 80 端口，多个就一行一个） |
| 目标 | 选「URL地址」，填 `127.0.0.1:8000`。输入框左边已经带了 `http://`，不要重复写 |
| 发送域名(host) | 保持默认 `$http_host`。目标是本机回环，不必改成真实域名 |
| 备注 | 留空 |

这个入口会自己生成站点配置，不用再去「添加站点」。

### 方式二：站点 + 站点内反向代理（老版界面）

网站 → 添加站点：域名 `money.vw50.cn`，根目录改成 `/www/wwwroot/money.vw50.cn`，
PHP 版本选「纯静态」，FTP 与数据库都选「不创建」。

站点只是个壳，页面和接口都是 uvicorn 从 `packages/web/dist` 读出来返回的，
所以不需要 PHP、不需要数据库，也不用往根目录传任何文件。

站点 → 设置 → 反向代理 → 添加反向代理，这个界面才有「代理名称」：

- 代理名称：`alphaforge`（只是个标识，随便填）
- 目标 URL：`http://127.0.0.1:8000`
- 发送域名：`$host`

然后到「配置文件」，把超时补上（宝塔默认 60 秒，同步接口要几分钟）：

```nginx
proxy_read_timeout 600s;
proxy_send_timeout 600s;
client_max_body_size 50m;
```

完整片段见 `deploy/baota/nginx.conf`。

最后在「SSL」里申请 Let's Encrypt 证书，打开「强制 HTTPS」。

## 9. 验证

```bash
curl -s http://127.0.0.1:8000/api/health
# {"code":0,"msg":"ok","data":{"status":"UP","version":"..."}}

curl -sI https://你的域名.com | head -3
```

浏览器打开域名，用 `admin / admin` 登录（如果没传 `auth.json`），立刻改密码，然后逐项确认：

- [ ] 总览页有指数、有涨跌家数
- [ ] 今日选股 → 打板，能列出当日涨停股
- [ ] 策略实验室 → 两个账户（打板 / 波段）都在
- [ ] 自动驾驶开关是开的（`data/autopilot.json` 里 `enabled: true`）
- [ ] 浏览器 F12 → Network，`/api/v1/lab/overview` 返回 200 而不是 502

## 10. 日常运维

**备份**（宝塔 → 计划任务 → 备份目录）

只备份 `data/` 就够了，每天 15:40（收盘任务跑完之后）。数据库是 WAL 模式，
直接拷 `.db` 可能少最后几笔，最稳的是停服务再拷，或者在计划任务里先 checkpoint：

```bash
sqlite3 /www/wwwroot/alphaforge/data/alphaforge.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

**升级**

```bash
cd /www/wwwroot/alphaforge
# 覆盖代码（保留 data/ 与 .env），然后
./venv/bin/pip install -r requirements.txt
```

重启项目。注意 `MODEL_VERSION` 一升级，旧回测会全部作废（页面显示「还没有跑过回测」），
账户与参数不受影响，重新在实验室点一次回测即可。

**日志**

- Supervisor：`/www/wwwlogs/alphaforge.out.log` 与 `.err.log`
- Python 项目管理器：面板里直接看「日志」

**收盘任务没跑**

页面 → 策略实验室 → 「补跑收盘」。它会同步日线 → 推进模拟盘 → 复盘 → 进化。
不要在收盘前补跑（会把盘中的半截行情写成收盘价）。

## 11. 常见坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 打开首页是一段 JSON 提示 | 缺 `packages/web/dist` | 见第 4 节 |
| 502 Bad Gateway | uvicorn 没起来 | 看 Supervisor / 项目日志，八成是 PYTHONPATH 少了一项 |
| 页面能开但接口 500，日志里 `no such table` | 数据库没传或路径不对 | 检查 `.env` 的 `AF_DB` 与文件是否存在 |
| 收盘任务在北京时间晚上 11 点才跑 | 服务器时区是 UTC | 第 1 节 |
| 模拟盘一天挂了两遍单 | 开了多个 worker / 多个进程 | 只留一个 uvicorn 进程 |
| 同步接口 504，但后台还在跑 | Nginx 超时 | `proxy_read_timeout 600s` |
| 登录后一直被踢 | 域名/协议变了，CORS 或 cookie 不匹配 | `AF_CORS_ORIGINS` 填实际访问地址，强制 HTTPS 后要带 `https://` |
| 上传数据库后少最后一天 | 只传了 `.db`，没带 `-wal` | 传之前先 `wal_checkpoint(TRUNCATE)` |

## 备选：Docker

`deploy/Dockerfile` 与 `deploy/docker-compose.yml` 已存在，宝塔装 Docker 管理器后：

```bash
cd /www/wwwroot/alphaforge
docker compose -f deploy/docker-compose.yml up -d --build
```

Docker 方案要注意三点：容器时区默认 UTC（compose 里要给 `TZ=Asia/Shanghai`）；
`data/` 要用 bind mount 到宿主机才好备份；数据同步要 `docker exec` 进容器跑。
对单人工作台来说，原生部署的运维成本更低，所以上面的步骤以原生为主。
