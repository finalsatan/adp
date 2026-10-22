import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { fetchWithRefresh, setNavigate, type NavigateFn } from "./client";

function jsonResponse(status: number, body: unknown = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function requestUrl(input: RequestInfo | URL): string {
  if (input instanceof Request) {
    return input.url;
  }
  return String(input);
}

describe("fetchWithRefresh", () => {
  let navigate: ReturnType<typeof vi.fn>;
  let refreshCalls: number;

  beforeEach(() => {
    navigate = vi.fn();
    setNavigate(navigate as unknown as NavigateFn);
    refreshCalls = 0;
    window.history.replaceState({}, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("三个请求同时 401 时只刷新一次，三个都重试成功", async () => {
    let refreshed = false;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url.includes("/api/v1/auth/refresh")) {
        refreshCalls += 1;
        refreshed = true;
        return jsonResponse(200, { must_change_password: false });
      }
      if (!refreshed) {
        return jsonResponse(401, { code: "AUTH_REQUIRED" });
      }
      return jsonResponse(200, { ok: true });
    });
    vi.stubGlobal("fetch", fetchMock);

    const responses = await Promise.all(
      ["/api/v1/me", "/api/v1/users", "/api/v1/orgs"].map((path) =>
        fetchWithRefresh(new Request(`http://localhost${path}`)),
      ),
    );

    expect(responses.map((response) => response.status)).toEqual([200, 200, 200]);
    expect(refreshCalls).toBe(1);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("刷新失败时调用跳转函数，并带上当前路径的 redirect", async () => {
    window.history.replaceState({}, "", "/system/users?page=2");
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = requestUrl(input);
      if (url.includes("/api/v1/auth/refresh")) {
        refreshCalls += 1;
        return jsonResponse(401, { code: "REFRESH_INVALID" });
      }
      return jsonResponse(401, { code: "AUTH_REQUIRED" });
    });
    vi.stubGlobal("fetch", fetchMock);

    const response = await fetchWithRefresh(
      new Request("http://localhost/api/v1/me"),
    );

    expect(response.status).toBe(401);
    expect(refreshCalls).toBe(1);
    expect(navigate).toHaveBeenCalledWith(
      "/login?redirect=%2Fsystem%2Fusers%3Fpage%3D2",
    );
  });

  it.each([
    "/api/v1/auth/login",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
  ])("%s 返回 401 时不触发刷新", async (path) => {
    const fetchMock = vi.fn(async () => jsonResponse(401, { code: "AUTH_REQUIRED" }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchWithRefresh(new Request(`http://localhost${path}`));

    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("返回 403 PASSWORD_CHANGE_REQUIRED 时跳转改密页", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(403, {
        code: "PASSWORD_CHANGE_REQUIRED",
        message: "请先修改密码",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await fetchWithRefresh(
      new Request("http://localhost/api/v1/users"),
    );

    expect(response.status).toBe(403);
    expect(navigate).toHaveBeenCalledWith("/change-password");
  });

  it("返回其他 403 时不跳转", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse(403, { code: "PERMISSION_DENIED" }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchWithRefresh(new Request("http://localhost/api/v1/users"));

    expect(navigate).not.toHaveBeenCalled();
  });
});
