import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useMe } from "../../api/me";
import type { Me } from "../../api/me";
import {
  useCreateModel,
  useDeleteModel,
  useDisableModel,
  useEnableModel,
  useModelRefs,
  useModels,
  useTestModel,
  useTestModelForm,
  useUpdateModel,
} from "./api";
import type { Model } from "./format";
import ModelsPage from "./ModelsPage";

vi.mock("../../api/me", () => ({ useMe: vi.fn() }));
vi.mock("./api", () => ({
  useModels: vi.fn(),
  useModelRefs: vi.fn(),
  useCreateModel: vi.fn(),
  useUpdateModel: vi.fn(),
  useDisableModel: vi.fn(),
  useEnableModel: vi.fn(),
  useDeleteModel: vi.fn(),
  useTestModel: vi.fn(),
  useTestModelForm: vi.fn(),
}));

const mockedUseMe = vi.mocked(useMe);
const mockedUseModels = vi.mocked(useModels);
const mockedUseModelRefs = vi.mocked(useModelRefs);
const mockedUseCreateModel = vi.mocked(useCreateModel);
const mockedUseDisableModel = vi.mocked(useDisableModel);
const mockedUseEnableModel = vi.mocked(useEnableModel);
const mockedUseDeleteModel = vi.mocked(useDeleteModel);
const mockedUseTestModel = vi.mocked(useTestModel);
const mockedUseTestModelForm = vi.mocked(useTestModelForm);
const mockedUseUpdateModel = vi.mocked(useUpdateModel);

const ME_SYS_ADMIN: Me = {
  id: 1,
  username: "admin",
  name: "管理员",
  email: null,
  phone: null,
  role: "sys_admin",
  org: null,
  permissions: ["console.use", "model.manage"],
  must_change_password: false,
};

const ME_BUILDER: Me = {
  ...ME_SYS_ADMIN,
  id: 2,
  username: "lisi",
  role: "builder",
  permissions: ["console.use"],
};

function model(partial: Partial<Model> & Pick<Model, "id" | "name">): Model {
  return {
    model_type: "chat",
    litellm_model: "dashscope/qwen-max",
    api_base: null,
    api_key_last4: null,
    description: null,
    context_length: 128000,
    max_output: 4096,
    cap_vision: false,
    cap_tools: true,
    cap_thinking: false,
    embedding_dim: null,
    embedding_max_input: null,
    embedding_batch_size: null,
    default_params: {},
    first_token_timeout_s: 30,
    total_timeout_s: 300,
    max_retries: 2,
    rate_qps: null,
    rate_concurrency: null,
    status: "enabled",
    ref_counts: { app: 0, kb: 0, scheme: 0 },
    fields_locked: false,
    last_test_at: "2025-10-20T09:30:00+08:00",
    last_test_ok: true,
    last_test_message: null,
    created_at: "2025-10-01T00:00:00+08:00",
    updated_at: "2025-10-01T00:00:00+08:00",
    ...partial,
  };
}

let store: Model[];

function mutationStub<T>(): T {
  return { mutate: vi.fn(), reset: vi.fn(), isPending: false } as unknown as T;
}

beforeEach(() => {
  store = [
    model({ id: 1, name: "通义千问-Max", ref_counts: { app: 12, kb: 0, scheme: 0 } }),
    model({
      id: 2,
      name: "text-embedding-v3",
      model_type: "embedding",
      litellm_model: "dashscope/text-embedding-v3",
      embedding_dim: 1024,
      status: "disabled",
    }),
  ];
  mockedUseMe.mockReturnValue({ data: ME_SYS_ADMIN } as unknown as ReturnType<typeof useMe>);
  mockedUseModels.mockImplementation(
    () =>
      ({
        data: { items: [...store], total: store.length },
        isLoading: false,
      }) as unknown as ReturnType<typeof useModels>,
  );
  mockedUseModelRefs.mockReturnValue({
    data: [],
    isLoading: false,
  } as unknown as ReturnType<typeof useModelRefs>);
  mockedUseCreateModel.mockImplementation(() => mutationStub());
  mockedUseUpdateModel.mockImplementation(() => mutationStub());
  mockedUseDisableModel.mockImplementation(() => mutationStub());
  mockedUseEnableModel.mockImplementation(() => mutationStub());
  mockedUseDeleteModel.mockImplementation(() => mutationStub());
  mockedUseTestModel.mockImplementation(() => mutationStub());
  mockedUseTestModelForm.mockImplementation(() => mutationStub());
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <MemoryRouter>
          <ModelsPage />
        </MemoryRouter>
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("ModelsPage", () => {
  it("搭建者只看到测试操作", async () => {
    mockedUseMe.mockReturnValue({ data: ME_BUILDER } as unknown as ReturnType<typeof useMe>);
    renderPage();

    const row = (await screen.findByText("通义千问-Max")).closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("测试")).toBeInTheDocument();
    expect(within(row as HTMLElement).queryByText("编辑")).toBeNull();
    expect(within(row as HTMLElement).queryByText("停用")).toBeNull();
    expect(within(row as HTMLElement).queryByText("删除")).toBeNull();
    expect(screen.queryByText("添加模型")).toBeNull();
  });

  it("系统管理员看到添加和全部操作，列表显示能力与被引用", async () => {
    renderPage();

    expect(await screen.findByText("添加模型")).toBeInTheDocument();
    expect(screen.getByText("共 2 个模型")).toBeInTheDocument();
    expect(screen.getByText("上下文 128K")).toBeInTheDocument();
    expect(screen.getByText("应用 12")).toBeInTheDocument();
    expect(screen.getByText("1024 维")).toBeInTheDocument();

    const row = screen.getByText("通义千问-Max").closest("tr");
    expect(within(row as HTMLElement).getByText("编辑")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("停用")).toBeInTheDocument();
    expect(within(row as HTMLElement).getByText("删除")).toBeInTheDocument();
  });

  it("添加模型：测试连通成功并保存后列表显示", async () => {
    mockedUseTestModelForm.mockReturnValue({
      isPending: false,
      mutate: vi.fn((_body, options?: { onSuccess?: (r: unknown) => void }) => {
        options?.onSuccess?.({
          ok: true,
          latency_ms: 710,
          first_token_ms: 710,
          returned_model: "gpt-4o",
          tested_at: "2025-10-20T10:00:00+08:00",
        });
      }),
    } as unknown as ReturnType<typeof useTestModelForm>);
    mockedUseCreateModel.mockReturnValue({
      isPending: false,
      mutate: vi.fn((body: Partial<Model>, options?: { onSuccess?: (m: Model) => void }) => {
        const created = model({ id: 99, name: body.name ?? "新模型", ...body });
        store = [...store, created];
        options?.onSuccess?.(created);
      }),
    } as unknown as ReturnType<typeof useCreateModel>);

    renderPage();
    fireEvent.click(await screen.findByText("添加模型"));

    fireEvent.change(await screen.findByLabelText("显示名称"), {
      target: { value: "GPT-4o" },
    });
    fireEvent.change(screen.getByLabelText("模型 ID（LiteLLM 格式）"), {
      target: { value: "openai/gpt-4o" },
    });
    fireEvent.change(screen.getByLabelText("上下文长度（tokens）"), {
      target: { value: "128000" },
    });
    fireEvent.change(screen.getByLabelText("最大输出（tokens）"), {
      target: { value: "4096" },
    });

    fireEvent.click(screen.getByText("测试连通"));
    expect(await screen.findByText(/连通成功/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));
    expect(await screen.findByText("GPT-4o")).toBeInTheDocument();
  });
});
