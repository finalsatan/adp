# 智能体开发平台 · 部署与运维（第一、二期）

面向客户的实施人员。第一期交付“可部署、可登录的管理后台”（系统管理、登录与改密、用户、组织、资源权限、审计日志、系统设置）；第二期在此基础上交付模型服务（模型管理、密钥加密、连通测试、模型体验）和用量统计。全程用 Docker Compose 单机部署，通过 HTTPS 访问。

部署文件都在 `deploy/` 下；命令统一走仓库根目录的 `make`。

---

## 1. 环境要求

| 项 | 要求 |
| --- | --- |
| 操作系统 | 64 位 Linux（x86_64）。本文示例路径用 `/opt/adp` |
| Docker Engine | 24.0 及以上 |
| Docker Compose | v2.20 及以上（Compose 插件，支持 BuildKit 的 `additional_contexts`）。本机验证版本：Docker 29.8.1 + Compose v5.5.1 |
| 磁盘 | 至少 20 GB 可用（镜像约 1 GB，其余留给数据库和备份） |
| 内存 | 至少 4 GB |
| CPU | 至少 2 核 |

> 第一期按 **2 核 4 GB** 起步即可。**以后各期（文档解析、向量化、重排等）会提高配置要求**，届时按升级说明调整。

需要能访问外网拉取镜像（或提前把镜像导入到内网镜像仓库）。如果服务器前面有负载均衡，见第 7 节。

第二期起，`api` 容器还要能访问所用云模型供应商的 HTTPS 地址；客户内网只能经代理出网时，按第 7 节设置 `HTTPS_PROXY`。

---

## 2. 安装步骤

### 2.1 取得代码

把发布版本放到 `/opt/adp`（目录名可自定，下文按 `/opt/adp`）：

```bash
cd /opt/adp
```

### 2.2 生成密钥

```bash
deploy/gen-secrets.sh
```

它会生成 `deploy/.env`（权限 600），里面是随机的数据库密码和 `JWT_SECRET`。**`deploy/.env` 已存在时脚本会拒绝覆盖**，不会把现有部署的密码冲掉。

`deploy/.env` 里的连接串默认用 compose 的服务名（`postgres`、`redis`），一般不用改。宿主机端口可以按需改：

```ini
HTTP_PORT=80
HTTPS_PORT=443
```

如果前面已经有别的服务占用 80/443，改成别的端口（例如 `58080` / `58443`），访问时带上端口号即可。

### 2.3 放置 HTTPS 证书

生产证书由客户提供，放到 `deploy/certs/`：

- `deploy/certs/server.crt`：证书链
- `deploy/certs/server.key`：私钥（权限 600）

本机验证可以先生成自签名证书（**不能用于生产**）：

```bash
deploy/gen-dev-cert.sh
```

### 2.4 启动

```bash
make prod-up
```

这会构建镜像并启动 `postgres`、`redis`、一次性的 `migrate`（执行表结构迁移后退出）、`api`、`nginx`。`api` 会等 `migrate` 成功退出、`redis` 健康后再启动；`nginx` 等 `api` 健康后再启动。首次启动要构建镜像，可能需要几分钟。

确认状态：

```bash
docker compose -f deploy/docker-compose.yml --env-file deploy/.env ps
```

`postgres`、`redis`、`api` 应为 `healthy`，`migrate` 应为 `Exited (0)`。

### 2.5 创建初始系统管理员

```bash
make prod-create-admin
```

按提示输入密码（两次）。密码要求：不少于 8 位，且包含大写字母、小写字母、数字、符号中的至少 3 类，不能与用户名相同，不能在常见弱密码表中。用户名默认 `admin`，可用 `make prod-create-admin ADMIN_USERNAME=admin2` 指定别的。

### 2.6 首次登录

浏览器打开 `https://<服务器地址>/`（用了非默认端口就带端口，自签名证书会提示不受信任）。用刚创建的管理员登录，**首次登录会强制修改密码**，改完进入控制台。之后在“用户管理”“组织管理”里建组织和用户。

### 2.7 接入云模型（第二期起）

系统管理员登录后，在“模型管理”里添加对话、向量化、重排模型，填 LiteLLM 格式的模型 ID（如 `dashscope/qwen-max`）、接口地址和 API Key。密钥用 `MASTER_KEY` 加密保存，界面和接口都不显示明文。

- **客户内网的模型网关**：接口地址指向内网时，先在“系统设置 → 模型调用 → 模型接口地址白名单”里加入该地址（主机名、IP 或 CIDR 网段），否则保存和调用会被拒绝。
- **访问云模型供应商**：`api` 容器需要能访问供应商的 HTTPS 地址；只能经代理出网时，在 `deploy/.env` 里设置 `HTTPS_PROXY`，再 `make prod-up` 重建 `api` 容器。
- **`MASTER_KEY` 必须备份**：它用来解密模型密钥，丢失后所有已保存的模型密钥都无法解密，只能重新录入（见第 3 节）。
- **离线可用**：模型价格表和 tiktoken 编码文件都在构建镜像时准备好，运行时不联网拉取。

---

## 3. 必须备份的内容

| 内容 | 位置 | 丢失后果 |
| --- | --- | --- |
| 数据库 | PostgreSQL 数据卷 `adp-prod_adp_pgdata` | 所有用户、组织、审计日志、系统设置丢失 |
| `JWT_SECRET` | `deploy/.env` | 丢失后所有人需要重新登录（影响较小） |
| `MASTER_KEY`（第二期起） | `deploy/.env` | **丢失后所有模型密钥无法解密**，必须重新录入；务必和数据库一起备份 |
| `deploy/.env` 整体 | `deploy/.env` | 数据库密码、密钥一起丢，恢复困难 |
| HTTPS 证书 | `deploy/certs/` | 需要重新申请 |

建议：把 `deploy/.env` 和证书备份到部署机之外的安全位置。

---

## 4. 备份与恢复

### 4.1 每日备份（cron 示例）

用超级用户 `postgres` 在容器内执行 `pg_dump`，输出 custom 格式（`-Fc`，支持并行恢复），保留最近 7 份。新建 `/opt/adp/scripts/backup.sh`：

```bash
#!/usr/bin/env bash
# ADP 每日备份。建议以部署用户身份运行。
set -euo pipefail

cd /opt/adp
BACKUP_DIR=/var/backups/adp
KEEP=7

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/adp-$STAMP.dump"

# 容器内本地连接用 trust，不需要密码
docker compose -f deploy/docker-compose.yml --env-file deploy/.env \
    exec -T postgres pg_dump -U postgres -Fc adp > "$OUT"

# 同时备份 .env（含 JWT_SECRET / 第二期的 MASTER_KEY）
cp deploy/.env "$BACKUP_DIR/env-$STAMP"

# 只保留最近 $KEEP 份
ls -1t "$BACKUP_DIR"/adp-*.dump | tail -n +$((KEEP + 1)) | xargs -r rm -f
ls -1t "$BACKUP_DIR"/env-*      | tail -n +$((KEEP + 1)) | xargs -r rm -f
```

```bash
chmod +x /opt/adp/scripts/backup.sh
```

加入 cron（每天 03:00，日志写到 `/var/log/adp-backup.log`）：

```cron
0 3 * * * /opt/adp/scripts/backup.sh >> /var/log/adp-backup.log 2>&1
```

> 备份目录放在别的磁盘时记得同步到异地。恢复演练建议每季度做一次。

### 4.2 恢复

恢复期间先停 `api`，避免写入；数据库保持运行。

```bash
cd /opt/adp

# 1. 停 api（nginx 还在，此时页面请求会 502）
docker compose -f deploy/docker-compose.yml --env-file deploy/.env stop api

# 2. 恢复数据库。--clean --if-exists 会先删掉同名对象再重建；
#    不加 --no-owner，保留表的所有者（迁移账号 adp_migrator），否则以后迁移会失败
docker compose -f deploy/docker-compose.yml --env-file deploy/.env \
    exec -T postgres pg_restore -U postgres -d adp --clean --if-exists \
    < /var/backups/adp/adp-YYYYmmdd-HHMMSS.dump

# 3. 启动 api
docker compose -f deploy/docker-compose.yml --env-file deploy/.env start api
```

恢复后做一次冒烟检查（第 6 节），确认能登录、数据还在。`JWT_SECRET` 丢失时把备份的 `deploy/.env` 拷回来再 `make prod-up` 重启。

---

## 5. 升级

**只支持向前升级**，不支持降级。每个版本的升级说明会写明是否有不可逆的迁移（如删列、改类型）。

```bash
cd /opt/adp

# 1. 先备份（见第 4 节），确认备份文件正常
/opt/adp/scripts/backup.sh

# 2. 拉取新版本代码（git pull / 解压发布包，视交付方式而定）

# 3. 重建并启动。migrate 容器会先执行 alembic upgrade head，成功退出后 api 才启动
make prod-up
```

升级过程中数据库短暂不可写，页面会报错或 502，属正常现象。`make prod-up` 完成后按第 6 节做冒烟检查。

---

## 6. 日志与健康检查

### 6.1 日志

所有服务把 JSON 日志写到标准输出，由 Docker 收集，不写本地日志文件：

```bash
cd /opt/adp
docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs -f api
docker compose -f deploy/docker-compose.yml --env-file deploy/.env logs --tail=200 nginx
```

每条日志带 `request_id`。**用户反馈问题时，请他提供页面上显示的请求 ID**（错误提示里带），我们用它可以在日志里定位到那一次请求。日志里不会出现密码、令牌、模型密钥。

### 6.2 健康检查

`postgres`、`redis`、`api` 都配了 Docker healthcheck，`docker compose ps` 里能看到 `healthy`：

- postgres：`pg_isready -U postgres`
- redis：`redis-cli ping`
- api：容器内请求 `http://127.0.0.1:8000/readyz`（检查能连上 PostgreSQL 和 Redis）

在容器内手动调用（`/healthz` 只看进程存活，`/readyz` 还检查依赖）：

```bash
cd /opt/adp
docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec api \
    python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/healthz').read().decode())"
docker compose -f deploy/docker-compose.yml --env-file deploy/.env exec api \
    python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/readyz').read().decode())"
```

`/healthz`、`/readyz` 只在容器网络内可用，nginx 不对外暴露。

### 6.3 冒烟检查

```bash
cd /opt/adp
deploy/smoke.sh
```

它会检查：首页返回 HTML、OpenAPI 可访问、错误密码登录返回统一 JSON 且带请求 ID、按 IP 锁定生效（伪造 `X-Real-IP` 无效）、HTTP 跳转到 HTTPS。默认读 `deploy/.env` 的端口，也可以传两个参数指定 HTTPS / HTTP 地址：

```bash
deploy/smoke.sh https://localhost:58443 http://localhost:58080
```

---

## 7. 网络与代理

- **只对外开放 443**（HTTPS）和 **80**（仅用于 301 跳转到 443）。其余服务只在 compose 内部网络，不发布端口。
- **前面有负载均衡时**：默认 nginx 一律用连接来源地址 `$remote_addr` 计来源 IP（防止伪造 `X-Forwarded-For` 绕过登录锁定）。若前面确实有负载均衡，编辑 `deploy/nginx/nginx.conf`，打开并修改这几行，只信任负载均衡的地址/网段：

  ```nginx
  set_real_ip_from 10.0.0.0/8;      # 换成负载均衡的地址或网段
  real_ip_header X-Forwarded-For;
  real_ip_recursive on;
  ```

  改完 `docker compose -f deploy/docker-compose.yml --env-file deploy/.env restart nginx`。
- **访问外网需要代理时**：在 `deploy/.env` 里设置 `HTTPS_PROXY`（第二期调用云模型供应商时用）：

  ```ini
  HTTPS_PROXY=http://proxy.example.com:8080
  ```

  改完 `make prod-up` 重建 api 容器。

---

## 8. 常见问题

**忘记管理员密码**
- 用 `make prod-create-admin ADMIN_USERNAME=admin2` 再建一个系统管理员，用它登录后在“用户管理”里给原管理员重置密码；或
- 如果还有另一个系统管理员，直接让他在“用户管理”里重置。

**账号被锁定**
- 登录连续失败 5 次（同一用户名 + 同一来源 IP）会锁定 15 分钟。管理员在“用户管理”里对该用户点“解锁”即可立即恢复；或等锁定时长到期。
- 阈值和时长可以在“系统设置”里调整。

**页面报“登录已过期”**
- 刷新令牌过期（默认 7 天）或已被管理员停用/重置密码。重新登录即可。

**`make prod-up` 后 api 起不来**
- 先看 `docker compose ... logs migrate`：迁移失败时 api 不会启动。再看 `logs api`。确认 `deploy/.env` 里的连接串密码和 `POSTGRES_PASSWORD` / `ADP_*_PASSWORD` 一致。

**磁盘占用越来越大**
- 主要是数据库和 Docker 镜像/日志。`docker system df` 查看；确认备份正常后可以 `docker image prune` 清理旧镜像。
