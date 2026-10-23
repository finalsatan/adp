import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../api/errors";
import { VisibilityModal } from "./VisibilityModal";
import { useUpdateResourceVisibility } from "./api";

vi.mock("./api", async () => {
  const actual = await vi.importActual<typeof import("./api")>("./api");
  return { ...actual, useUpdateResourceVisibility: vi.fn() };
});

const mockedUpdate = vi.mocked(useUpdateResourceVisibility);

function renderModal() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <VisibilityModal
          open
          resource={{ id: 7, name: "员工手册", visibility: "org" }}
          onClose={vi.fn()}
        />
      </AntApp>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("VisibilityModal", () => {
  it("保存时提交选中的可见范围", () => {
    const mutate = vi.fn();
    mockedUpdate.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateResourceVisibility>);

    renderModal();
    fireEvent.click(screen.getByLabelText("全平台"));
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    expect(mutate).toHaveBeenCalledWith(
      { id: 7, visibility: "platform" },
      expect.anything(),
    );
  });

  it("收到 RESOURCE_IN_USE 时列出引用方", async () => {
    const mutate = vi.fn((_variables, options) => {
      options?.onError?.(
        new ApiError(409, {
          code: "RESOURCE_IN_USE",
          message: "被其他资源引用，请先解除引用",
          detail: {
            refs: [
              { id: 1, type: "app", name: "报销助手" },
              { id: 2, type: "app", name: "IT 服务台" },
            ],
          },
        }),
      );
    });
    mockedUpdate.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateResourceVisibility>);

    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    expect(
      await screen.findByText("这些应用会因此不能再引用它，请先在应用中解除引用"),
    ).toBeInTheDocument();
    expect(screen.getByText("报销助手（app）")).toBeInTheDocument();
    expect(screen.getByText("IT 服务台（app）")).toBeInTheDocument();
  });

  it("其他错误显示错误说明", async () => {
    const mutate = vi.fn((_variables, options) => {
      options?.onError?.(
        new ApiError(422, {
          code: "VISIBILITY_FIXED",
          message: "该类资源的可见范围不能修改",
        }),
      );
    });
    mockedUpdate.mockReturnValue({
      mutate,
      isPending: false,
    } as unknown as ReturnType<typeof useUpdateResourceVisibility>);

    renderModal();
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    expect(
      await screen.findByText("该类资源的可见范围不能修改"),
    ).toBeInTheDocument();
  });
});
