import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../../api/errors";
import {
  useSettings,
  useUpdateSettings,
  type Settings,
} from "../../../api/settings";
import SettingsPage from "./SettingsPage";
import {
  allowlistToText,
  diffSettings,
  settingsToForm,
  textToAllowlist,
  type SettingsFormValues,
} from "./settingsForm";

vi.mock("../../../api/settings", () => ({
  useSettings: vi.fn(),
  useUpdateSettings: vi.fn(),
}));

const mockedUseSettings = vi.mocked(useSettings);
const mockedUseUpdateSettings = vi.mocked(useUpdateSettings);

const SETTINGS: Settings = {
  password: { min_length: 8, min_classes: 3 },
  login: { lock_threshold: 5, lock_minutes: 15 },
  session: { access_minutes: 15, refresh_days: 7 },
  model_call: { ingest_rate_ratio: 0.5, model_host_allowlist: ["10.20.0.0/16"] },
};

const FORM_VALUES: SettingsFormValues = {
  password: { min_length: 8, min_classes: 3 },
  login: { lock_threshold: 5, lock_minutes: 15 },
  session: { access_minutes: 15, refresh_days: 7 },
  model_call: {
    ingest_rate_ratio: 0.5,
    model_host_allowlist: "10.20.0.0/16",
  },
};

function mockData() {
  mockedUseSettings.mockReturnValue({
    data: SETTINGS,
    isLoading: false,
    error: null,
  } as unknown as ReturnType<typeof useSettings>);
  const update = { mutate: vi.fn(), isPending: false };
  mockedUseUpdateSettings.mockReturnValue(
    update as unknown as ReturnType<typeof useUpdateSettings>,
  );
  return update;
}

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <SettingsPage />
      </AntApp>
    </QueryClientProvider>,
  );
}

afterEach(() => {
  vi.clearAllMocks();
});

describe("白名单文本与数组互转", () => {
  it("按行拆分，去首尾空格，丢弃空行", () => {
    expect(
      textToAllowlist("10.20.0.0/16\n\n  llm-gateway.corp.local  \n"),
    ).toEqual(["10.20.0.0/16", "llm-gateway.corp.local"]);
  });

  it("数组转多行文本", () => {
    expect(allowlistToText(["10.20.0.0/16", "llm-gateway.corp.local"])).toBe(
      "10.20.0.0/16\nllm-gateway.corp.local",
    );
  });
});

describe("diffSettings", () => {
  it("没有改动时返回空对象", () => {
    expect(diffSettings(SETTINGS, FORM_VALUES)).toEqual({});
  });

  it("只提交改动过的字段", () => {
    const values: SettingsFormValues = {
      ...FORM_VALUES,
      login: { ...FORM_VALUES.login, lock_threshold: 6 },
    };
    expect(diffSettings(SETTINGS, values)).toEqual({
      login: { lock_threshold: 6 },
    });
  });

  it("白名单只有空白差异时不提交，内容变化时转成数组", () => {
    const unchanged: SettingsFormValues = {
      ...FORM_VALUES,
      model_call: {
        ...FORM_VALUES.model_call,
        model_host_allowlist: "  10.20.0.0/16  \n",
      },
    };
    expect(diffSettings(SETTINGS, unchanged)).toEqual({});

    const changed: SettingsFormValues = {
      ...FORM_VALUES,
      model_call: {
        ...FORM_VALUES.model_call,
        model_host_allowlist: "10.20.0.0/16\n\n llm-gateway.corp.local \n",
      },
    };
    expect(diffSettings(SETTINGS, changed)).toEqual({
      model_call: {
        model_host_allowlist: ["10.20.0.0/16", "llm-gateway.corp.local"],
      },
    });
  });
});

describe("settingsToForm", () => {
  it("白名单数组转成多行文本", () => {
    expect(settingsToForm(SETTINGS).model_call.model_host_allowlist).toBe(
      "10.20.0.0/16",
    );
  });
});

// 本机测试环境较重，Ant Design 表单在 jsdom 下渲染慢，放宽单测超时。
describe("SettingsPage", () => {
  it("按原型显示三组字段和提示", async () => {
    mockData();
    renderPage();

    expect(await screen.findByText("密码与登录")).toBeInTheDocument();
    expect(screen.getByText("会话")).toBeInTheDocument();
    expect(screen.getByText("模型调用")).toBeInTheDocument();
    expect(screen.getByText("大写、小写、数字、符号四类中")).toBeInTheDocument();
    expect(screen.getByText("按“用户名 + 来源 IP”计数")).toBeInTheDocument();
    expect(
      screen.getByText("模型设了限流时才起作用；入库和在线合计不超过模型限额"),
    ).toBeInTheDocument();
    // 第一期不显示后续分期的两组
    expect(screen.queryByText("上传与解析")).not.toBeInTheDocument();
    expect(screen.queryByText("解析引擎")).not.toBeInTheDocument();
  }, 30000);

  it("保存时只提交改动过的字段", async () => {
    const update = mockData();
    renderPage();

    fireEvent.change(await screen.findByLabelText("连续失败锁定次数"), {
      target: { value: "6" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => {
      expect(update.mutate).toHaveBeenCalledWith(
        { login: { lock_threshold: 6 } },
        expect.anything(),
      );
    });
  }, 30000);

  it("白名单保存前去空行和首尾空格", async () => {
    const update = mockData();
    renderPage();

    fireEvent.change(await screen.findByLabelText("模型接口地址白名单"), {
      target: { value: "10.20.0.0/16\n\n  llm-gateway.corp.local  \n" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    await waitFor(() => {
      expect(update.mutate).toHaveBeenCalledWith(
        {
          model_call: {
            model_host_allowlist: [
              "10.20.0.0/16",
              "llm-gateway.corp.local",
            ],
          },
        },
        expect.anything(),
      );
    });
  }, 30000);

  it("422 时把错误显示在对应字段下", async () => {
    const update = mockData();
    update.mutate.mockImplementation((_patch, options) => {
      // 真实请求是异步返回的，同步调用会被字段变更后的校验覆盖
      setTimeout(() => {
        options?.onError?.(
          new ApiError(422, {
            code: "VALIDATION_ERROR",
            message: "请求参数不正确",
            detail: {
              errors: [
                {
                  type: "greater_than_equal",
                  loc: ["body", "login", "lock_threshold"],
                  msg: "该值超出允许范围",
                },
              ],
            },
          }),
        );
      }, 0);
    });
    renderPage();

    fireEvent.change(await screen.findByLabelText("连续失败锁定次数"), {
      target: { value: "6" },
    });
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    expect(await screen.findByText("该值超出允许范围")).toBeInTheDocument();
  }, 30000);
});
