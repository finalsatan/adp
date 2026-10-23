# 前端静态文件 + nginx 镜像。
# 第一阶段用 Node + pnpm 构建 frontend/，第二阶段只把 dist 和 nginx 配置拷进 nginx。
#
# 构建上下文是 frontend/（用 frontend/.dockerignore 排除 node_modules、dist 等）；
# nginx 配置通过 compose 的 additional_contexts 以名字 nginx_conf 传入，
# 对应 deploy/nginx/ 目录。见 deploy/docker-compose.yml。

FROM node:24.22.1-slim AS frontend-build

WORKDIR /app

# pnpm 版本与本地开发保持一致
RUN npm install --global pnpm@9.15.9

# 先装依赖（锁文件不变时可复用缓存），再拷源码构建
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

FROM nginx:1.31.2-alpine

COPY --from=frontend-build /app/dist /usr/share/nginx/html
COPY --from=nginx_conf nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80 443
CMD ["nginx", "-g", "daemon off;"]
