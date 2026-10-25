/**
 * 第二期端到端用例（P2-12，技术方案 13.2 的“端到端”一条）。
 *
 * 依赖第一期用例先建好的组织和用户（文件名 phase1 < phase2，单 worker 顺序执行）。
 * 模型调用全部打到本地假模型服务（backend/tests/fake_llm/server.py，9999 端口），
 * 不需要真实密钥。
 */
import { expect, test, type Page } from "@playwright/test";

import {
  ADMIN_PASSWORD,
  ADMIN_USERNAME,
  BUILDER_NEW_PASSWORD,
  BUILDER_USERNAME,
  FAKE_LLM_BASE_URL,
  FAKE_LLM_REPLY,
} from "../config";
import { expectConnectivitySuccess, login } from "../helpers";

const MODEL_A = "假模型A";
const MODEL_B = "假模型B";
const MODEL_A_ID = "openai/fake-a";
const MODEL_B_ID = "openai/fake-b";
const FAKE_API_KEY = "fake-key";
/** 回复文本里稳定的一段，用来断言两列都流式出现了回答。 */
const REPLY_SNIPPET = "这是本地假模型的回答";

/** 系统设置：把本机地址加入模型接口地址白名单，否则保存和测试都会被拒。 */
async function allowLocalModelHost(page: Page): Promise<void> {
  await page.goto("/system/settings");
  const allowlist = page.locator("#model_call_model_host_allowlist");
  await expect(allowlist).toBeVisible();
  await allowlist.fill("127.0.0.1");
  await page.getByRole("button", { name: /保\s*存/ }).click();
  await expect(page.getByText("已保存").first()).toBeVisible();
}

/** 添加一个对话模型：填表 → 保存前测试连通 → 保存。 */
async function addChatModel(page: Page, name: string, litellmModel: string): Promise<void> {
  await page.getByRole("button", { name: "添加模型" }).click();
  const drawer = page.locator(".ant-drawer-content");
  await drawer.locator("#name").fill(name);
  await drawer.locator("#litellm_model").fill(litellmModel);
  await drawer.locator("#api_base").fill(FAKE_LLM_BASE_URL);
  await drawer.locator("#api_key").fill(FAKE_API_KEY);
  await drawer.locator("#context_length").fill("128000");
  await drawer.locator("#max_output").fill("1024");

  await expectConnectivitySuccess(page, drawer, FAKE_LLM_BASE_URL);

  await drawer.getByRole("button", { name: /保\s*存/ }).click();
  await expect(page.getByText("模型已添加").first()).toBeVisible();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toBeVisible();
}

/** 列表里对已保存模型点“测试”，弹窗里应显示连通成功。 */
async function testModelInList(page: Page, name: string): Promise<void> {
  const row = page.getByRole("row", { name: new RegExp(name) });
  await row.getByText("测试", { exact: true }).click();
  const modal = page.locator(".ant-modal-content");
  await expect(modal.getByText(/✓ 连通/)).toBeVisible();
  await modal.getByRole("button", { name: /关\s*闭/ }).click();
}

/** 体验页第 index 列选一个模型。 */
async function selectColumnModel(page: Page, index: number, name: string): Promise<void> {
  await page.locator(".ant-select-selector").nth(index).click();
  // 已经开过的下拉还留在 DOM 里，用 :not(.ant-select-dropdown-hidden) + last() 选当前这个
  const dropdown = page
    .locator(".ant-select-dropdown:not(.ant-select-dropdown-hidden)")
    .last();
  await dropdown.locator(".ant-select-item-option", { hasText: name }).first().click();
}

test.describe.serial("第二期端到端", () => {
  test("1. 管理员加白名单、添加两个对话模型并保存前测试连通", async ({ page }) => {
    await login(page, ADMIN_USERNAME, ADMIN_PASSWORD);
    await expect(page).toHaveURL(/\/models/);

    await allowLocalModelHost(page);

    await page.goto("/models");
    await addChatModel(page, MODEL_A, MODEL_A_ID);
    await addChatModel(page, MODEL_B, MODEL_B_ID);
  });

  test("2. 模型列表里“测试”显示连通成功", async ({ page }) => {
    await login(page, ADMIN_USERNAME, ADMIN_PASSWORD);
    await expect(page).toHaveURL(/\/models/);
    await testModelInList(page, MODEL_A);
    await testModelInList(page, MODEL_B);
  });

  test("3. 体验页两列对比：都流式出现回答和统计行", async ({ page }) => {
    await login(page, ADMIN_USERNAME, ADMIN_PASSWORD);
    await expect(page).toHaveURL(/\/models/);
    await page.goto("/models/playground");

    await selectColumnModel(page, 0, MODEL_A);
    await selectColumnModel(page, 1, MODEL_B);

    await page.locator("textarea").first().fill("你好，请介绍一下自己");
    await page.getByRole("button", { name: /发\s*送/ }).click();

    // 两列都拿到完整回答，并出现统计行（RUN_FINISHED）
    await expect(page.locator(".safe-markdown", { hasText: FAKE_LLM_REPLY })).toHaveCount(2, {
      timeout: 30_000,
    });
    await expect(page.getByTestId("msg-stats")).toHaveCount(2);
  });

  test("4. 输出中途点“停止生成”，流式停止且不出现统计行", async ({ page }) => {
    await login(page, ADMIN_USERNAME, ADMIN_PASSWORD);
    await expect(page).toHaveURL(/\/models/);
    await page.goto("/models/playground");

    await selectColumnModel(page, 0, MODEL_A);
    await selectColumnModel(page, 1, MODEL_B);
    await page.getByRole("button", { name: "清空对话" }).click();
    await expect(page.locator(".safe-markdown", { hasText: REPLY_SNIPPET })).toHaveCount(0);

    await page.locator("textarea").first().fill("再回答一个问题");
    await page.getByRole("button", { name: /发\s*送/ }).click();

    // 等第一段文字出现后立刻停止
    await expect(page.locator(".safe-markdown", { hasText: "这是本地" })).toHaveCount(2);
    await page.getByRole("button", { name: "停止生成" }).click();

    // 回到可发送状态，且没有 RUN_FINISHED 的统计行
    await expect(page.getByRole("button", { name: /发\s*送/ })).toBeVisible();
    await expect(page.getByTestId("msg-stats")).toHaveCount(0);
  });

  test("5. 用量统计按来源汇总能看到模型体验和连通测试的调用次数", async ({ page }) => {
    await login(page, ADMIN_USERNAME, ADMIN_PASSWORD);
    await expect(page).toHaveURL(/\/models/);
    await page.goto("/observe/usage");

    await page.locator(".ant-segmented-item", { hasText: "来源" }).click();

    const playgroundRow = page.getByRole("row", { name: /模型体验/ });
    await expect(playgroundRow).toBeVisible();
    await expect(playgroundRow.locator("td").nth(1)).not.toHaveText("0");

    const testRow = page.getByRole("row", { name: /连通测试/ });
    await expect(testRow).toBeVisible();
    await expect(testRow.locator("td").nth(1)).not.toHaveText("0");
  });

  test("6. 搭建者只能测试模型、能进体验页、看不到用量统计", async ({ page }) => {
    await login(page, BUILDER_USERNAME, BUILDER_NEW_PASSWORD);
    await expect(page).toHaveURL(/\/models/);

    await expect(page.getByRole("button", { name: "添加模型" })).toHaveCount(0);
    const row = page.getByRole("row", { name: new RegExp(MODEL_A) });
    await expect(row.getByText("测试", { exact: true })).toBeVisible();
    await expect(row.getByText("编辑", { exact: true })).toHaveCount(0);
    await expect(row.getByText("停用", { exact: true })).toHaveCount(0);
    await expect(row.getByText("删除", { exact: true })).toHaveCount(0);

    await page.goto("/models/playground");
    await expect(page.getByRole("button", { name: /添加对比模型/ })).toBeVisible();

    await expect(page.locator(".ant-menu").getByText("用量统计")).toHaveCount(0);
  });
});
