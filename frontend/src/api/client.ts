import createClient from "openapi-fetch";

import { ApiError, readErrorBody } from "./errors";
import type { paths } from "./schema";

/** 跳转函数，默认整页跳转；应用内用 react-router 替换，测试里可注入。 */
export type NavigateFn = (to: string) => void;

let navigate: NavigateFn = (to) => {
  window.location.assign(to);
};

export function setNavigate(fn: NavigateFn): void {
  navigate = fn;
}

/** 这三个接口本身返回 401 时不触发刷新，避免循环。 */
const NO_REFRESH_PATHS = new Set([
  "/api/v1/auth/login",
  "/api/v1/auth/refresh",
  "/api/v1/auth/logout",
]);

let refreshPromise: Promise<boolean> | null = null;

/** 同一时刻只发一个刷新请求，其他调用等同一个 Promise。 */
export function refreshOnce(): Promise<boolean> {
  refreshPromise ??= doRefresh().finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

async function doRefresh(): Promise<boolean> {
  try {
    const response = await fetch("/api/v1/auth/refresh", { method: "POST" });
    return response.ok;
  } catch {
    return false;
  }
}

function currentPath(): string {
  return window.location.pathname + window.location.search;
}

/**
 * 访问令牌过期（401）时刷新一次并重试原请求；刷新失败跳登录页。
 * 403 PASSWORD_CHANGE_REQUIRED 时跳改密页。第二期的 SSE 读取工具复用本函数。
 */
export async function fetchWithRefresh(request: Request): Promise<Response> {
  const path = new URL(request.url, window.location.origin).pathname;
  // 第一次发送前克隆，重试时才能再发一次带 body 的请求
  const retryRequest = request.clone();
  const response = await fetch(request);

  if (response.status === 401 && !NO_REFRESH_PATHS.has(path)) {
    if (await refreshOnce()) {
      return fetch(retryRequest);
    }
    navigate(`/login?redirect=${encodeURIComponent(currentPath())}`);
    return response;
  }

  if (response.status === 403) {
    const body = await readErrorBody(response);
    if (body.code === "PASSWORD_CHANGE_REQUIRED") {
      navigate("/change-password");
    }
  }

  return response;
}

export const client = createClient<paths>({ baseUrl: "", fetch: fetchWithRefresh });

interface OpenApiResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** 成功返回数据，失败抛 ApiError（供 TanStack Query 的 onError 使用）。 */
export async function unwrap<T>(result: OpenApiResult<T>): Promise<T> {
  if (result.response.ok) {
    return result.data as T;
  }
  const body = result.error ?? (await readErrorBody(result.response));
  throw ApiError.from(result.response.status, body);
}
