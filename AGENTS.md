# 智能体开发平台（ADP）

当前阶段：逐模块讨论需求，并写技术方案。本分支还没有代码。

## 以哪些文件为准
- 需求：`docs/prd/智能体开发平台-需求文档-v0.3.html`；原型：`docs/prd/prototype/style-a1-antd.html`（选定 A1 Ant Design 风）
- 技术方案：`docs/tech/智能体开发平台-技术方案-v0.1.html`（平台骨架、M8 系统管理、M1 模型服务）；`docs/tech/智能体开发平台-知识库技术方案-v0.1.html`（M2 知识库与入库方案）
- `docs/history/`、`feat/mvp` 分支只是旧稿和测试代码，不作为依据
- 术语以 `GLOSSARY.md` 为准；难以回退的架构决策记在 `docs/adr/`
- 编辑文档的约定见 `docs/AGENTS.md`

## 已定技术栈
后端 Python（FastAPI）+ PostgreSQL 16 + Redis；LiteLLM 以 SDK 方式嵌入后端；前端 React + Ant Design 5；部署用 Docker Compose。

## 协作规则
- 用中文回复
- 先给方案和待拍板的问题，用户确认后再写进文档；没定的写“待定”，不要替用户编答案；自己补充的内容要说明
- 只在用户要求时提交，不推送
- 不要提交 `apps/`、`node_modules/`、`test-results/`（`feat/mvp` 的遗留产物）和 `repos/`（参考实现）
