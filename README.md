# ADP 智能体开发平台

基于 [PRD v0.2](docs/prd/智能体开发平台-需求文档-v0.2.html) 的前后端 MVP，以“文档入库 → 带引用问答 → 用户反馈 → 效果评测”为核心。

技术栈为 React / TypeScript / Vite、Fastify / Node.js 24、SQLite 和独立 Python 解析服务。数据库、原文件和对话都保存在本地数据卷；真实模型由管理员配置 OpenAI 兼容地址及密钥。技术取舍、任务分工和验收门槛见 [实施计划](docs/MVP实施计划.md)，接口见 [接口约定](docs/MVP接口约定.md)，实际测试与部署状态见 [验收记录](docs/MVP验收记录.md)。

## 本地启动

需要 Node.js **24**、npm 和 Python 3.12+。推荐安装 [uv](https://docs.astral.sh/uv/)，未安装时 setup 使用 Python venv / pip。

```sh
npm install
npm run setup
npm run dev
```

打开 <http://localhost:5173>，首次使用时创建管理员、密码和默认组织。没有预置公共管理员密码。API 默认 <http://127.0.0.1:3001>，解析服务 <http://127.0.0.1:8001>。

`setup` 创建仅当前用户可读的 `.env` 和解析服务虚拟环境。加密密钥与解析 token 自动随机生成，请随数据库一起安全备份，不要提交到 Git。

## 首次体验

1. 在“知识库”中新建知识库，选择标有“本地演示”的 Embedding，上传 `examples/员工手册.md` 和 `examples/费用报销.md`，等文档进入 READY。
2. 查看切片、调整分块或用命中测试检查召回。
3. 创建知识问答应用，关联知识库、选择 chat 模型，调试并发布。
4. 打开发布后的 `/chat/<应用ID>`，提问“一线城市每人每晚住宿上限是多少？”，点击引用查看原文。点赞、点踩或补充纠错。
5. 创建评测集，导入 `examples/evaluation.json` 或 CSV；也可将反馈加入评测集。选择两种检索配置运行并比较，在逐条结果中分别评审答案和引用，汇总业务人工指标。
6. 在链路追踪中查看入库、检索与问答的阶段详情；创建绑定发布应用的 API Key 供程序调用。

示例文档与问题是虚构的开发数据。**本地演示模型使用文本特征检索和原文摘录，不是真实 LLM，也不代表语义问答质量。** 调用真实模型失败会显示错误，不会静默换成本地演示。按文档生成评测问题需要真实 chat 模型。

## 真实模型

在模型管理中登记 chat / embedding / rerank / vision 模型，填写 Base URL、模型 ID 和 API Key 并测试。系统管理员可配置全平台模型；组织管理员可配置本组织模型。密钥用 AES-256-GCM 保存，读取接口只返回掩码。

网络请求由配置的模型地址决定。使用云端模型时，相关上下文会发送到管理员选定的服务；若要求内网运行，请配置内网 OpenAI 兼容服务。

## 构建与验证

```sh
npm run typecheck
npm test
npm run test:parser
npm run build
npm run test:e2e
npm run test:browser
```

构建后 `npm start` 启动 API 并托管前端，访问 <http://127.0.0.1:3001>。解析服务需独立启动，或使用下面的 Compose。

浏览器测试使用独立临时数据库和浏览器。在 macOS 上自动使用已安装的 Chrome；其他环境可执行 `npx playwright install chromium`，或通过 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` 指定浏览器。截图保存在 `test-results/browser/`。

```sh
apps/parser/.venv/bin/python -m uvicorn app:app --app-dir apps/parser --host 127.0.0.1 --port 8001
```

## Docker Compose

先生成 `.env`（`npm run setup`，或按 `.env.example` 自行填写随机密钥），在根目录执行：

```sh
docker compose --env-file .env -f deploy/compose.yml up --build -d
```

访问 <http://127.0.0.1:3001>。应用数据在 `adp-data` 卷中，解析服务不对外暴露端口。不要删除数据卷，或更换已有数据使用的 `APP_MASTER_KEY`。

## Kubernetes

提供单副本、Recreate 更新、持久卷的清单。该 MVP 不支持多个写入实例共享 SQLite。

```sh
docker build -t adp:0.1.0 .
docker build -t adp-parser:0.1.0 apps/parser
kubectl create namespace adp
kubectl -n adp create secret generic adp-secrets --from-env-file=.env
kubectl apply -f deploy/kubernetes.yml
kubectl -n adp port-forward service/adp 3001:3001
```

将镜像导入本地集群，或推送到自己的镜像仓库并修改清单中的 image。集群需要默认 StorageClass；网关的域名与 HTTPS 按部署环境配置。示例为本地 HTTP 体验，接入 HTTPS 网关时将 `COOKIE_SECURE=true`。

## 数据与恢复

`DATA_DIR` 默认 `./data`；生产容器为 `/app/data`。数据库和原文件必须一起备份。先停止应用再复制整个数据目录及 `.env`，恢复时挂载相同目录和原加密密钥。不要直接复制正在写入的 SQLite 主文件而遗漏 WAL。

异步任务保存在数据库，应用重启后恢复未完成任务。文件重新处理成功前保留旧检索切片；失败原因和重试入口在文档列表中。

可选 OIDC 的环境变量见 `.env.example`。不配置时使用本地账号。应用 Key 仅能调用绑定应用，创建后完整密钥只显示一次；吊销立即生效。

## 试点验收

PRD 的真实试点场景、业务确认的至少 200 条问题，以及答案/引用准确率和首字响应 X/Y/Z 尚未提供。示例集只验证软件闭环，不能替代业务签字和质量达标。业务方确认数据和阈值后，使用评测功能记录配置与结果，按 [试点验收清单](docs/试点验收清单.md) 验收。

管理后台面向桌面浏览器，独立聊天页适配手机。可视化工作流、通用 Agent、MCP、工具、Skills、计费不属于首版。扫描件/图片 OCR 尚未实现；生产规模以及未实测部署项目见 [验收记录](docs/MVP验收记录.md)。
