import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../api/errors";
import { AppPublishModal } from "./AppPublishModal";
import { usePublishApp, type App } from "./api";

vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return { ...actual, usePublishApp: vi.fn() };
});

const mockedUsePublishApp = vi.mocked(usePublishApp);

const APP: App = {
  id: 1,
  name: "HR 政策问答",
  description: null,
  app_type: "kb_qa",
  chat_path: "/chat/1",
  org: { id: 1, name: "研发中心" },
  owner: { id: 1, username: "zhangsan", name: "张三" },
  visibility: "org",
  status: "published",
  published_version: { id: 9, version_no: 3, published_at: "2025-10-21T10:40:00+08:00" },
  draft_has_changes: true,
  draft_updated_at: null,
  usage_scope: { scope: "orgs", orgs: [{ id: 2, name: "研发中心" }], users: [] },
  created_at: "2025-10-01T00:00:00+08:00",
  updated_at: "2025-10-20T10:12:00+08:00",
  editable: true,
  manageable: true,
};

interface MutationOptions {
  onSuccess?: () => void;
  onError?: (error: unknown) => void;
}

function renderModal() {
  return render(
    <AntApp>
      <AppPublishModal app={APP} open onClose={() => {}} />
    </AntApp>,
  );
}

beforeEach(() => {
  mockedUsePublishApp.mockReturnValue({
    mutate: vi.fn(),
    isPending: false,
  } as unknown as ReturnType<typeof usePublishApp>);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("AppPublishModal", () => {
  it("显示下一个版本号、使用范围与对话页链接", () => {
    renderModal();

    expect(screen.getByText(/将把当前草稿发布为/)).toBeInTheDocument();
    expect(screen.getByText("v4")).toBeInTheDocument();
    expect(screen.getByText(/研发中心/)).toBeInTheDocument();
    expect(screen.getByText(/在“权限与设置”中修改/)).toBeInTheDocument();
    expect(screen.getByText(/\/chat\/1$/)).toBeInTheDocument();
  });

  it("发布时提交版本说明", async () => {
    const mutate = vi.fn();
    mockedUsePublishApp.mockReturnValue({ mutate, isPending: false } as unknown as ReturnType<
      typeof usePublishApp
    >);
    renderModal();

    fireEvent.change(screen.getByPlaceholderText(/问题改写改用通义千问-Turbo/), {
      target: { value: "加入产品手册，开启重排" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发\s*布/ }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0]?.[0]).toEqual({
      id: 1,
      body: { note: "加入产品手册，开启重排" },
    });
  });

  it("发布校验失败时列出问题清单", async () => {
    const mutate = vi.fn((_variables: unknown, options: MutationOptions) => {
      options.onError?.(
        new ApiError(422, {
          code: "PUBLISH_CHECK_FAILED",
          message: "发布校验未通过",
          detail: {
            problems: [
              { code: "MODEL_REQUIRED", message: "请选择回答模型", field: "model.chat_model_id" },
              { code: "KB_REQUIRED", message: "请至少引用一个知识库", field: "knowledge.kbs" },
            ],
          },
        }),
      );
    });
    mockedUsePublishApp.mockReturnValue({ mutate, isPending: false } as unknown as ReturnType<
      typeof usePublishApp
    >);
    renderModal();

    fireEvent.change(screen.getByPlaceholderText(/问题改写改用通义千问-Turbo/), {
      target: { value: "说明" },
    });
    fireEvent.click(screen.getByRole("button", { name: /发\s*布/ }));

    expect(await screen.findByText("发布校验未通过")).toBeInTheDocument();
    expect(screen.getByText("请选择回答模型")).toBeInTheDocument();
    expect(screen.getByText("请至少引用一个知识库")).toBeInTheDocument();
  });
});
