# docs 目录约定

## 目录
- `prd/`：需求文档和原型；`tech/`：技术方案；`research/`：调研结论，可作参考；`adr/`：架构决策记录；`history/`：旧稿，忽略

## 需求文档
- 读者是研发团队，不写优先级、交付批次和业务场景
- 详细需求的章节顺序与第 1 节的模块表一致；有二级模块的（如资源库、应用开发）按“一级模块 → 二级模块 → 小节”三级组织，二级模块顺序与模块表的“二级模块”列一致；还没细化的模块或二级模块保留标题，写“待细化”
- 标题带 `id`（`s3`、`s3-2`，三级为 `s4-1-2`），目录是 `<nav class="toc">`；增删章节时要同步更新编号、id、目录，以及正文里“见 X.Y”的引用和技术方案中“需求 X.Y”的引用

## 原型（`prd/prototype/style-a1-antd.html`）
- 只用 HTML 和 CSS，不写脚本（预览服务的 CSP 禁止脚本）；页面靠 `:target` 切换
- 新增页面 `#v-xxx` 或抽屉、弹窗 `#d-xxx`/`#m-xxx` 时，要同步改四处：侧栏高亮选择器、面包屑 `.c-xxx`、抽屉对应的背景页、`index.html` 入口
- 修改后用 Playwright 截图检查，宽度取 1440（平台只在 PC 端使用，不检查手机宽度）。Playwright 在仓库根目录的 `node_modules` 里

## 预览服务
- 容器 `adp-docs`（nginx，带 Basic Auth）把 `docs/prd` 挂载到 `/prd`、`docs/tech` 挂载到 `/tech`，文件改完刷新就能看到
- 配置在仓库外的 `/home/ubuntu/adp-docs-nginx/`；docker 命令要用 `sudo -n docker`
