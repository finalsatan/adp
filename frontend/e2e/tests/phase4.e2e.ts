/**
 * 第四期端到端用例（P4-18，应用技术方案 17.3 的“端到端”一条）。
 *
 * 依赖第一期用例先建好的组织和用户（文件名 phase1 < phase2 < phase3 < phase4，
 * 单 worker 顺序执行）。后端连 `adp_e2e`、Redis 12 号库，对象存储用独立的桶
 * `adp-e2e`；模型调用走本地假模型服务（9999，含 `/v1/rerank`），解析走本地假解析
 * 服务（59010），入库由 webServer 里起的 Celery worker 执行。
 *
 * 覆盖：配置应用（模型、提示词、文档库、检索、兜底话术、开场白）→ 调试预览提问并
 * 展开处理过程、点召回结果看分块位置 → 发布（含校验失败）→ 终端用户登录进入
 * “我的应用” → 提问（流式）→ 点角标预览 PDF / Word、下载原文件 → 点踩并填写纠错
 * → 搭建者在“用户反馈”和“对话日志”看到；以及权限、版本、生成控制等第四期要点。
 * 纠错加入问答库、问答库直答属第六期（用户 2026-10-26 决定），本卡做到“点踩并填写纠错”。
 */
import { expect, test, type Locator, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  ADMIN_PASSWORD,
  ADMIN_USERNAME,
  BUILDER_NEW_PASSWORD,
  BUILDER_USERNAME,
  END_USER_PASSWORD,
  END_USER_USERNAME,
  FAKE_LLM_BASE_URL,
  INITIAL_PASSWORD,
} from "../config";
import { expectConnectivitySuccess, login, loginWithOptionalChange } from "../helpers";

const CHAT_MODEL = "P4假对话模型";
const CHAT_MODEL_ID = "openai/p4-fake-chat";
const EMBEDDING_MODEL = "P4假向量模型甲";
const EMBEDDING_MODEL_ID = "openai/p4-fake-embedding";
const EMBEDDING_MODEL_B = "P4假向量模型乙";
const RERANK_MODEL = "P4假重排模型";
const RERANK_MODEL_ID = "hosted_vllm/rerank";
const FAKE_API_KEY = "fake-key";

const KB_NAME = "P4端到端文档库";
const EMPTY_KB_NAME = "P4空文档库";
const FOLDER_NAME = "P4差旅制度";
const PDF_NAME = "p4-员工手册.pdf";
const WORD_NAME = "p4-员工手册.docx";
const APP_NAME_PREFIX = "P4端到端问答应用";

const PDF_QUESTION = "差旅报销住宿费标准是多少？";
const WORD_QUESTION = "入职当天需要携带什么材料？";
const FALLBACK_TEXT = "没有找到相关资料，请换个说法。";
const OPENING = "我是知识问答助手，请提问。";

const VIEWER_USERNAME = "p4viewer";
const VIEWER_NAME = "P4仅查看";
const VIEWER_PASSWORD = "P4ViewerNew!2345";

const TMP_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "adp-e2e-p4-"));
const PDF_PATH = path.join(TMP_DIR, PDF_NAME);
const WORD_PATH = path.join(TMP_DIR, WORD_NAME);

// 用例之间共享：应用 id、应用名、文档库 id、空文档库 id。
let appId = 0;
let appName = APP_NAME_PREFIX;
let kbId = 0;
let emptyKbId = 0;
let lastConversationId = 0;

test.beforeAll(() => {
  fs.writeFileSync(PDF_PATH, buildPdf());
  fs.writeFileSync(
    WORD_PATH,
    [
      "员工手册",
      "第一章 入职",
      "新员工入职当天需携带身份证和学历证明到人力资源部报到。",
      "人力资源部为新员工办理工牌、门禁和办公设备。",
      "入职一周内完成公司制度与安全培训。",
    ].join("\n"),
  );
});

test.afterAll(() => {
  fs.rmSync(TMP_DIR, { recursive: true, force: true });
});

test.describe.serial("第四期端到端", () => {
  test.describe.configure({ timeout: 240_000 });

  test("1. 管理员加白名单，添加向量化与重排模型", async ({ page }) => {
    await login(page, ADMIN_USERNAME, ADMIN_PASSWORD);
    await expect(page).toHaveURL(/\/models/);
    await allowLocalModelHost(page);
    await addModel(page, { name: CHAT_MODEL, type: "对话", litellmModel: CHAT_MODEL_ID });
    await addModel(page, {
      name: EMBEDDING_MODEL,
      type: "向量化",
      litellmModel: EMBEDDING_MODEL_ID,
      embeddingDim: "1024",
    });
    await addModel(page, {
      name: EMBEDDING_MODEL_B,
      type: "向量化",
      litellmModel: "openai/p4-fake-embedding-b",
      embeddingDim: "1024",
    });
    await addModel(page, { name: RERANK_MODEL, type: "重排", litellmModel: RERANK_MODEL_ID });
    // 造一个“仅有查看权限”的搭建者：同组织、可见范围内，但不是应用协作者。
    await createViewer(page);
  });

  test("2. 搭建者建文档库、上传 PDF 与 Word 并完成入库", async ({ page }) => {
    await loginBuilder(page);
    await createKb(page, KB_NAME, EMBEDDING_MODEL);
    kbId = await openKb(page, KB_NAME);
    await createFolder(page, FOLDER_NAME);
    await selectFolder(page, FOLDER_NAME);
    await uploadDocument(page, PDF_PATH);
    await waitDocumentDone(page, PDF_NAME);
    await uploadDocument(page, WORD_PATH);
    await waitDocumentDone(page, WORD_NAME);

    await page.goto(`/kb/${kbId}`);
    await expect(page.getByText(/2 个文档 · [1-9]\d* 个分块/)).toBeVisible({ timeout: 30_000 });

    // 造一个使用另一个向量化模型的空文档库，供发布校验用（不用入库，够快）。
    emptyKbId = await createEmptyKb(page, EMPTY_KB_NAME, EMBEDDING_MODEL_B);
  });

  test("3. 搭建者建应用、配置并保存草稿，设置使用范围", async ({ page }) => {
    await loginBuilder(page);
    await page.goto("/apps");
    await page.getByRole("button", { name: "新建应用" }).click();
    const modal = page.locator(".ant-modal-content").filter({ hasText: "新建应用" });
    await expect(modal).toBeVisible();
    // 名字带后缀，重复跑时不会 NAME_CONFLICT。
    appName = `${APP_NAME_PREFIX}-${Date.now().toString().slice(-6)}`;
    await modal.locator("#name").fill(appName);
    await modal.locator("#description").fill("P4 端到端联调应用");
    await modal.getByRole("button", { name: /创建并配置/ }).click();
    await expect(page).toHaveURL(/\/apps\/\d+$/);
    appId = Number(/\/apps\/(\d+)/.exec(page.url())?.[1]);

    await configureApp(page, {
      chatModel: CHAT_MODEL,
      systemPrompt: "你是 HR 助手，依据参考资料简洁回答。",
      kbName: KB_NAME,
      rerankModel: RERANK_MODEL,
      fallback: FALLBACK_TEXT,
      opening: OPENING,
      allowDownload: true,
    });
    await saveDraft(page);
    await expect(page.getByText("草稿有未发布的修改")).toBeVisible();

    // 使用范围：指定组织（复核 P4-14 的只读组织树接口），再切回全平台供终端用户使用。
    await page.goto(`/apps/${appId}/permissions`);
    await setUsageScopeOrgs(page, "研发中心");
    await setUsageScopePlatform(page);
  });

  test("4. 发布校验失败后发布 v1", async ({ page }) => {
    await loginBuilder(page);
    await page.goto(`/apps/${appId}`);
    await expect(page.getByText("草稿有未发布的修改")).toBeVisible();

    // 校验失败：移除全部知识库后发布。
    const knowledge = card(page, "知识库");
    await knowledge.getByRole("button", { name: /移\s*除/ }).first().click();
    await saveDraft(page);
    await publishApp(page, "P4 校验失败用例");
    await expect(page.getByText("请至少引用一个知识库")).toBeVisible();

    // 校验失败：两个不同向量化模型的文档库未开启重排。保存草稿即拒绝（发布也做同样校验）。
    const draft = await getDraft(page);
    const withEmptyKb = structuredClone(draft.config);
    withEmptyKb.knowledge.kbs = [
      { kb_id: kbId, retrieval: null, filter: null, qa_category_ids: [] },
      { kb_id: emptyKbId, retrieval: null, filter: null, qa_category_ids: [] },
    ];
    withEmptyKb.knowledge.doc.rerank = { enabled: false, model_id: null, keep: 5 };
    const saveRejected = await page.request.put(`/api/v1/apps/${appId}/draft`, {
      data: { draft_revision: draft.draft_revision, config: withEmptyKb },
    });
    expect(saveRejected.status()).toBe(422);
    expect(JSON.stringify((await saveRejected.json()).detail?.problems ?? [])).toContain("重排");

    // 恢复草稿并发布 v1。
    await page.reload();
    await configureApp(page, {
      chatModel: CHAT_MODEL,
      systemPrompt: "你是 HR 助手，依据参考资料简洁回答。",
      kbName: KB_NAME,
      rerankModel: RERANK_MODEL,
      fallback: FALLBACK_TEXT,
      opening: OPENING,
      allowDownload: true,
    });
    await saveDraft(page);
    await publishApp(page, "P4 端到端发布 v1");
    await expect(page.getByText("已发布").first()).toBeVisible();
    await expect(page.getByText("草稿有未发布的修改")).toHaveCount(0);
  });

  test("5. 调试预览：处理过程、分块位置、完整提示词", async ({ page }) => {
    await loginBuilder(page);
    await page.goto(`/apps/${appId}`);
    const panel = page.locator(".ant-card").filter({ hasText: "预览调试" });

    // 第一问：没有历史，问题改写跳过；检索 + 重排 + 生成。
    await askInPanel(page, panel, PDF_QUESTION);
    const answer = panel.locator('[data-testid="chat-answer"]').last();
    await expect(answer.locator('[data-testid="citation-1"]').first()).toBeVisible({
      timeout: 30_000,
    });

    const trace = answer.locator('[data-testid="chat-trace"]');
    await trace.locator("summary").click();
    for (const step of ["intent", "rewrite", "retrieve", "rerank", "generate"]) {
      await expect(answer.locator(`[data-testid="trace-step-${step}"]`)).toBeVisible();
    }

    // 点召回结果看分块位置（PDF 内嵌页或 Word 解析结果）。
    await answer.getByRole("button", { name: /查看位置 #\d+/ }).first().click();
    const previewModal = page.locator(".ant-modal-content").filter({ hasText: "分块位置" });
    await expect(previewModal).toBeVisible();
    await expect(
      previewModal.locator("iframe, [data-testid='parse-result']").first(),
    ).toBeVisible({ timeout: 30_000 });
    await previewModal.locator(".ant-modal-close").click();
    await expect(previewModal).toBeHidden();

    // 查看完整提示词。
    await answer.getByRole("button", { name: "查看完整提示词" }).click();
    const promptModal = page.locator(".ant-modal-content").filter({ hasText: "完整提示词" });
    await expect(promptModal.locator("pre")).toContainText("references");
    await promptModal.locator(".ant-modal-close").click();
    await expect(promptModal).toBeHidden();

    // 第二问：带历史，问题改写真正执行。
    await askInPanel(page, panel, "那住宿费呢？");
    const answer2 = panel.locator('[data-testid="chat-answer"]').last();
    await expect(answer2.locator('[data-testid="chat-trace"]')).toBeVisible({ timeout: 30_000 });
    await answer2.locator('[data-testid="chat-trace"] summary').click();
    await expect(answer2.locator('[data-testid="trace-step-rewrite"]')).toContainText("那住宿费呢");
  });

  test("6. 终端用户提问、引用预览与下载", async ({ page }) => {
    await loginEndUser(page);
    const myAppCard = page.locator(".ant-card").filter({ hasText: appName });
    await expect(myAppCard).toBeVisible();
    await myAppCard.getByRole("link", { name: /开始提问/ }).click();
    await expect(page).toHaveURL(/\/chat\/\d+/);

    // 开场白与推荐问题来自发布版本配置。
    await expect(page.getByText(OPENING)).toBeVisible();

    const panel = page.locator(".ant-card").filter({ hasText: appName });
    await askInPanel(page, panel, PDF_QUESTION);
    await expect(page.locator('[data-testid="answer-citation-1"]').first()).toBeVisible({
      timeout: 30_000,
    });

    // 点 PDF 角标 → 只含被引用页的 PDF + 高亮。
    const pdfCitation = page
      .locator('[data-testid^="answer-citation-"]')
      .filter({ hasText: PDF_NAME })
      .first();
    await pdfCitation.click();
    await expect(page.locator('[data-testid="citation-pdf"]')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText(/第 \d+ \/ \d+ 页/)).toBeVisible({ timeout: 30_000 });
    // 下载原文件（开关开）。
    const downloadResp = await page.request.get(
      citationFileUrl(await lastAnswerId(page), await citationNumberOf(pdfCitation)),
    );
    expect(downloadResp.headers()["content-disposition"] ?? "").toContain("attachment");
    await page.keyboard.press("Escape");

    // 再问一个命中 Word 的问题，点 Word 角标 → 解析结果视图高亮被引用段落。
    await askInPanel(page, panel, WORD_QUESTION);
    await expect(page.locator('[data-testid="answer-citation-1"]').first()).toBeVisible({
      timeout: 30_000,
    });
    const wordCitation = page
      .locator('[data-testid^="answer-citation-"]')
      .filter({ hasText: WORD_NAME })
      .first();
    await expect(wordCitation).toBeVisible();
    await wordCitation.click();
    const body = page.locator('[data-testid="citation-preview-body"]');
    await expect(body).toBeVisible({ timeout: 30_000 });
    await expect(body.locator('[data-testid="parse-result"]')).toBeVisible({ timeout: 30_000 });
    await page.keyboard.press("Escape");
  });

  test("7. 关闭下载后立即不能下载", async ({ page }) => {
    // 搭建者发布 v2，关闭“允许下载原文件”。
    await loginBuilder(page);
    await page.goto(`/apps/${appId}`);
    await configureApp(page, { allowDownload: false });
    await saveDraft(page);
    await publishApp(page, "P4 关闭下载 v2");
    await expect(page.getByText("已发布").first()).toBeVisible();

    // 终端用户重新打开引用抽屉：没有下载按钮，提示未开启下载。
    await loginEndUser(page);
    await page.goto(`/chat/${appId}`);
    const panel = page.locator(".ant-card").filter({ hasText: appName });
    await askInPanel(page, panel, PDF_QUESTION);
    const pdfCitation = page
      .locator('[data-testid^="answer-citation-"]')
      .filter({ hasText: PDF_NAME })
      .first();
    await expect(pdfCitation).toBeVisible({ timeout: 30_000 });
    await pdfCitation.click();
    await expect(page.getByText("本应用未开启下载")).toBeVisible({ timeout: 30_000 });
    await expect(page.getByRole("link", { name: "下载原文件" })).toHaveCount(0);
    // 直接请求下载接口返回 403。
    const resp = await page.request.get(
      citationFileUrl(await lastAnswerId(page), await citationNumberOf(pdfCitation)),
    );
    expect(resp.status()).toBe(403);
    await page.keyboard.press("Escape");
  });

  test("8. 点踩并填写纠错，搭建者看反馈与日志", async ({ page }) => {
    await loginEndUser(page);
    await page.goto(`/chat/${appId}`);
    const panel = page.locator(".ant-card").filter({ hasText: appName });
    await askInPanel(page, panel, WORD_QUESTION);
    await expect(page.locator('[data-testid="answer-citation-1"]').first()).toBeVisible({
      timeout: 30_000,
    });

    await page.getByRole("button", { name: "点踩" }).last().click();
    const feedback = page.locator('[data-testid="feedback-panel"]');
    await expect(feedback).toBeVisible();
    await feedback.getByText("答案错误", { exact: true }).click();
    await feedback.getByText("引用不对", { exact: true }).click();
    await feedback
      .getByPlaceholder(/你认为正确的答案是什么/)
      .fill("入职当天应携带身份证和学历证明。");
    await feedback.locator('[data-testid="feedback-submit"]').click();
    await expect(feedback).toBeHidden({ timeout: 15_000 });

    // 搭建者：反馈列表看到该反馈，打开对应对话日志看到回答、处理过程与点踩。
    await loginBuilder(page);
    await page.goto(`/apps/${appId}/feedback`);
    await expect(page.getByTestId("feedback-summary")).toContainText("点踩 1");
    const row = page.getByRole("row").filter({ hasText: WORD_QUESTION });
    await expect(row).toBeVisible();
    await expect(row).toContainText("答案错误");
    await expect(row).toContainText("入职当天应携带身份证和学历证明。");
    await row.getByRole("button", { name: "对话日志" }).click();
    await expect(page).toHaveURL(/\/apps\/\d+\/logs/);

    const answer = page.locator('[data-testid^="log-answer-"]').last();
    await expect(answer).toBeVisible({ timeout: 30_000 });
    await expect(answer.getByText("点踩")).toBeVisible();
    await answer.locator("summary").first().click();
    await expect(answer.locator('[data-testid="log-trace-step-generate"]')).toBeVisible();
  });

  test("9. 权限：终端用户控制台 403、仅有查看权限的人读日志/反馈 403", async ({
    page,
  }) => {
    // 终端用户调用控制台接口返回 403。
    await loginEndUser(page);
    const appsResp = await page.request.get("/api/v1/apps");
    expect(appsResp.status()).toBe(403);
    const logsResp = await page.request.get(`/api/v1/apps/${appId}/logs/conversations`);
    expect(logsResp.status()).toBe(403);

    // 同组织、可见范围内但非协作者的搭建者：能看应用，读日志/反馈 403。
    await loginWithOptionalChange(page, VIEWER_USERNAME, INITIAL_PASSWORD, VIEWER_PASSWORD);
    await expect(page).toHaveURL(/\/models/);
    const detail = await page.request.get(`/api/v1/apps/${appId}`);
    expect(detail.status()).toBe(200);
    const viewerLogs = await page.request.get(`/api/v1/apps/${appId}/logs/conversations`);
    expect(viewerLogs.status()).toBe(403);
    const viewerFeedback = await page.request.get(`/api/v1/apps/${appId}/feedback`);
    expect(viewerFeedback.status()).toBe(403);
  });

  test("10. 版本：同一 revision 重复保存返回 DRAFT_CONFLICT", async ({ page }) => {
    await loginBuilder(page);
    const draft = await getDraft(page);
    const body = {
      draft_revision: draft.draft_revision,
      config: draft.config,
    };
    const first = await page.request.put(`/api/v1/apps/${appId}/draft`, { data: body });
    expect(first.status()).toBe(200);
    const second = await page.request.put(`/api/v1/apps/${appId}/draft`, { data: body });
    expect(second.status()).toBe(409);
    expect((await second.json()).code).toBe("DRAFT_CONFLICT");
  });

  test("11. 生成控制：中途停止后状态 stopped，重新生成后旧回答在日志可见", async ({
    page,
  }) => {
    await loginEndUser(page);
    await page.goto(`/chat/${appId}`);
    const panel = page.locator(".ant-card").filter({ hasText: appName });
    const input = panel.locator("textarea").last();
    await input.fill("介绍一下公司的报销制度好吗？");
    await panel.getByRole("button", { name: /发\s*送/ }).click();

    // 等出现一段文字后停止生成。
    await expect(panel.getByRole("button", { name: "停止生成" })).toBeVisible({
      timeout: 30_000,
    });
    await panel.getByRole("button", { name: "停止生成" }).click();
    await expect(panel.getByRole("button", { name: /发\s*送/ })).toBeVisible();

    lastConversationId = Number(/\/chat\/\d+\/(\d+)/.exec(page.url())?.[1]);
    expect(lastConversationId).toBeGreaterThan(0);
    const stopped = await waitForAnswerStatus(page, lastConversationId, "stopped");
    expect(stopped).toBeGreaterThan(0);

    // 重新生成：旧回答仍保留，当前回答变成新的一条。
    await panel.getByRole("button", { name: "重新生成" }).click();
    await expect(panel.getByRole("button", { name: "停止生成" })).toBeVisible({ timeout: 30_000 });
    await expect(panel.getByRole("button", { name: /发\s*送/ })).toBeVisible({ timeout: 90_000 });
    const turns = await getTurns(page, lastConversationId);
    const lastTurn = turns.at(-1);
    expect(lastTurn.answers.length).toBeGreaterThanOrEqual(2);
  });
});

// ---------------------------------------------------------------------------
// 通用操作
// ---------------------------------------------------------------------------

/** 按卡片标题取卡片。 */
function card(page: Page, title: string): Locator {
  return page.locator(".ant-card").filter({
    has: page.locator(".ant-card-head-title", { hasText: title }),
  });
}

/** 系统设置：把本机地址加入模型接口地址白名单，否则保存和测试都会被拒。 */
async function allowLocalModelHost(page: Page): Promise<void> {
  await page.goto("/system/settings");
  const allowlist = page.locator("#model_call_model_host_allowlist");
  await expect(allowlist).toBeVisible();
  if ((await allowlist.inputValue()).includes("127.0.0.1")) {
    return;
  }
  await allowlist.fill("127.0.0.1");
  await page.getByRole("button", { name: /保\s*存/ }).click();
  await expect(page.getByText("已保存").first()).toBeVisible();
}

interface ModelSpec {
  name: string;
  type: "对话" | "向量化" | "重排";
  litellmModel: string;
  embeddingDim?: string;
}

/** 添加一个模型（已存在则跳过）；保存前测试连通。 */
async function addModel(page: Page, spec: ModelSpec): Promise<void> {
  await page.goto("/models");
  await expect(page.getByRole("button", { name: "添加模型" })).toBeVisible();
  // 等列表加载完（有行或空状态），否则会把已有的模型当成没有而重复添加。
  await expect(
    page.locator(".ant-table-tbody tr.ant-table-row, .ant-empty").first(),
  ).toBeVisible();
  await waitTableLoaded(page);
  if ((await page.getByRole("row", { name: new RegExp(spec.name) }).count()) > 0) {
    return;
  }
  await page.getByRole("button", { name: "添加模型" }).click();
  const drawer = page.locator(".ant-drawer-content");
  await expect(drawer).toBeVisible();
  await drawer.locator("#name").fill(spec.name);
  await drawer.getByRole("radio", { name: spec.type }).check();
  await drawer.locator("#litellm_model").fill(spec.litellmModel);
  await drawer.locator("#api_base").fill(FAKE_LLM_BASE_URL);
  await drawer.locator("#api_key").fill(FAKE_API_KEY);
  if (spec.type === "对话") {
    await drawer.locator("#context_length").fill("128000");
    await drawer.locator("#max_output").fill("1024");
  }
  if (spec.type === "向量化") {
    await drawer.locator("#embedding_dim").fill(spec.embeddingDim ?? "1024");
    await drawer.locator("#embedding_max_input").fill("8192");
    await drawer.locator("#embedding_batch_size").fill("16");
  }
  await expectConnectivitySuccess(page, drawer, FAKE_LLM_BASE_URL);
  await drawer.getByRole("button", { name: /保\s*存/ }).click();
  await expect(page.getByText("模型已添加").first()).toBeVisible();
  await expect(page.getByRole("row", { name: new RegExp(spec.name) })).toBeVisible();
}

/** 管理员通过接口创建一个同组织的搭建者（用于“仅有查看权限”的用例）。 */
async function createViewer(page: Page): Promise<void> {
  const orgsResp = await page.request.get("/api/v1/orgs:tree");
  const orgs = (await orgsResp.json()) as { id: number; name: string }[];
  const org = orgs.find((item) => item.name === "研发中心");
  expect(org).toBeTruthy();
  const created = await page.request.post("/api/v1/users", {
    data: {
      username: VIEWER_USERNAME,
      name: VIEWER_NAME,
      password: INITIAL_PASSWORD,
      role: "builder",
      org_id: org?.id,
    },
  });
  // 已存在（重复跑）时 409，同样视为准备好。
  expect([201, 409]).toContain(created.status());
}

/** 搭建者新建一个文档库。 */
async function createKb(page: Page, name: string, embeddingModel: string): Promise<void> {
  await page.goto("/kb");
  await waitTableLoaded(page);
  if ((await page.getByRole("row", { name: new RegExp(name) }).count()) > 0) {
    return;
  }
  await page.getByRole("button", { name: "新建知识库" }).click();
  const modal = page.locator(".ant-modal-content").filter({ hasText: "新建知识库" });
  await expect(modal).toBeVisible();
  await modal.locator("#name").fill(name);
  await modal.locator("#embedding_model_id").click();
  await page
    .locator(".ant-select-dropdown:visible .ant-select-item-option")
    .filter({ hasText: embeddingModel })
    .first()
    .click();
  await modal.getByRole("button", { name: /创\s*建/ }).click();
  await expect(page.getByText("已创建").first()).toBeVisible();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toBeVisible();
}

/** 通过接口建一个空文档库，返回 id（用于发布校验，不入库）。 */
async function createEmptyKb(page: Page, name: string, embeddingModel: string): Promise<number> {
  const modelsResp = await page.request.get("/api/v1/models?type=embedding&page_size=100");
  const models = (await modelsResp.json()) as { items: { id: number; name: string }[] };
  const model = models.items.find((item) => item.name === embeddingModel);
  expect(model).toBeTruthy();
  const created = await page.request.post("/api/v1/kbs", {
    data: {
      kind: "doc",
      name,
      embedding_model_id: model?.id,
      visibility: "org",
      org_id: null,
    },
  });
  if (created.status() === 201) {
    return ((await created.json()) as { id: number }).id;
  }
  // 已存在（重复跑）：从列表里找出来复用。
  const list = await page.request.get("/api/v1/kbs?page_size=100");
  const data = (await list.json()) as { items: { id: number; name: string }[] };
  const existing = data.items.find((item) => item.name === name);
  expect(existing).toBeTruthy();
  return existing?.id ?? 0;
}

/** 打开知识库文档页，返回知识库 id。 */
async function openKb(page: Page, name: string): Promise<number> {
  await page.goto("/kb");
  const row = page.getByRole("row", { name: new RegExp(name) });
  await expect(row).toBeVisible();
  await row.getByText("进入").click();
  await expect(page).toHaveURL(/\/kb\/\d+$/);
  return Number(/\/kb\/(\d+)/.exec(page.url())?.[1]);
}

async function createFolder(page: Page, name: string): Promise<void> {
  await expect(page.getByText(name, { exact: true }))
    .toBeVisible({ timeout: 5_000 })
    .catch(() => undefined);
  if ((await page.getByText(name, { exact: true }).count()) > 0) {
    return;
  }
  await page.getByText("+ 新建").click();
  const modal = page.locator(".ant-modal-content").filter({ hasText: "新建文件夹" });
  await expect(modal).toBeVisible();
  await modal.locator("#name").fill(name);
  await modal.getByRole("button", { name: /创\s*建/ }).click();
  await expect(page.getByText("已创建").first()).toBeVisible();
}

async function selectFolder(page: Page, name: string): Promise<void> {
  await page.getByText(name, { exact: true }).click();
}

async function uploadDocument(page: Page, filePath: string): Promise<void> {
  const rowName = new RegExp(escapeRegExp(path.basename(filePath)));
  await waitTableLoaded(page);
  if ((await page.getByRole("row", { name: rowName }).count()) > 0) {
    return;
  }
  await page.getByRole("button", { name: "上传文档" }).click();
  const modal = page.locator(".ant-modal-content").filter({ hasText: "上传文档" });
  await expect(modal).toBeVisible();
  await modal.locator('input[type="file"]').first().setInputFiles(filePath);
  await expect(modal.getByText(path.basename(filePath))).toBeVisible();
  await modal.getByRole("button", { name: "预检并继续" }).click();
  await expect(modal.getByText(/超长预检通过/)).toBeVisible({ timeout: 30_000 });
  await modal.getByRole("button", { name: "开始上传" }).click();
  await expect(modal).toBeHidden({ timeout: 60_000 });
}

async function waitDocumentDone(page: Page, name: string): Promise<void> {
  const row = page.getByRole("row", { name: new RegExp(escapeRegExp(name)) });
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(row.getByText("已完成")).toBeVisible({ timeout: 120_000 });
}

/** 搭建者登录。 */
async function loginBuilder(page: Page): Promise<void> {
  await login(page, BUILDER_USERNAME, BUILDER_NEW_PASSWORD);
  await expect(page).toHaveURL(/\/models/);
}

/** 终端用户登录，落到“我的应用”。 */
async function loginEndUser(page: Page): Promise<void> {
  await login(page, END_USER_USERNAME, END_USER_PASSWORD);
  await expect(page).toHaveURL(/\/my-apps/);
}

interface AppConfigInput {
  chatModel?: string;
  systemPrompt?: string;
  kbName?: string;
  rerankModel?: string;
  fallback?: string;
  opening?: string;
  allowDownload?: boolean;
}

/** 在配置页填写配置（只填传入的字段）。 */
async function configureApp(page: Page, input: AppConfigInput): Promise<void> {
  if (input.chatModel) {
    const modelCard = card(page, "模型与提示词");
    await modelCard.locator(".ant-select").first().click();
    await page
      .locator(".ant-select-dropdown:visible .ant-select-item-option")
      .filter({ hasText: input.chatModel })
      .first()
      .click();
  }
  if (input.systemPrompt !== undefined) {
    await card(page, "模型与提示词").locator("textarea").first().fill(input.systemPrompt);
  }
  if (input.kbName) {
    const knowledge = card(page, "知识库");
    const already = await knowledge.getByText(input.kbName).count();
    if (already === 0) {
      await knowledge.getByRole("button", { name: "添加知识库" }).click();
      const modal = page.locator(".ant-modal-content").filter({ hasText: "添加知识库" });
      await modal.getByRole("checkbox", { name: input.kbName }).check();
      await modal.getByRole("button", { name: /添加 \d+ 个/ }).click();
      await expect(knowledge.getByText(input.kbName)).toBeVisible();
    }
  }
  if (input.rerankModel) {
    const docCard = card(page, "文档库检索");
    const enabled = await docCard.getByRole("switch").getAttribute("aria-checked");
    if (enabled !== "true") {
      await docCard.getByRole("switch").click();
    }
    await docCard.locator(".ant-select").last().click();
    await page
      .locator(".ant-select-dropdown:visible .ant-select-item-option")
      .filter({ hasText: input.rerankModel })
      .first()
      .click();
  }
  if (input.fallback !== undefined) {
    await card(page, "回答设置").locator("input").first().fill(input.fallback);
  }
  if (input.opening !== undefined) {
    await card(page, "回答设置").locator("textarea").first().fill(input.opening);
  }
  if (input.allowDownload !== undefined) {
    const reply = card(page, "回答设置");
    const checked = await reply.getByRole("switch").getAttribute("aria-checked");
    const want = input.allowDownload ? "true" : "false";
    if (checked !== want) {
      await reply.getByRole("switch").click();
    }
  }
}

async function saveDraft(page: Page): Promise<void> {
  await page.getByRole("button", { name: "保存草稿" }).click();
  await expect(page.getByText("草稿已保存").first()).toBeVisible();
}

async function publishApp(page: Page, note: string): Promise<void> {
  await page.getByRole("button", { name: /发\s*布/ }).first().click();
  const modal = page.locator(".ant-modal-content").filter({ hasText: "发布「" });
  await expect(modal).toBeVisible();
  await modal.locator("textarea").fill(note);
  await modal.getByRole("button", { name: /发\s*布/ }).click();
}

/** 使用范围切到“指定组织”并选一个组织。 */
async function setUsageScopeOrgs(page: Page, orgName: string): Promise<void> {
  const scopeCard = card(page, "使用范围");
  await scopeCard.getByRole("radio", { name: /指定组织/ }).click();
  const tree = scopeCard.locator(".ant-select").first();
  await tree.click();
  const dropdown = page.locator(".ant-select-dropdown:visible");
  await expect(dropdown).toBeVisible();
  await dropdown
    .locator(".ant-select-tree-node-content-wrapper")
    .filter({ hasText: orgName })
    .first()
    .click();
  if ((await page.locator(".ant-select-dropdown:visible").count()) > 0) {
    await page.keyboard.press("Escape");
  }
  await scopeCard.getByRole("button", { name: /保\s*存/ }).click();
  await expect
    .poll(async () => (await readUsageScope(page)).scope, { timeout: 15_000 })
    .toBe("orgs");
  const scope = await readUsageScope(page);
  expect(scope.orgs.map((org: { name: string }) => org.name)).toContain(orgName);
}

interface UsageScopeJson {
  scope: string;
  orgs: { id: number; name: string }[];
}

async function readUsageScope(page: Page): Promise<UsageScopeJson> {
  const resp = await page.request.get(`/api/v1/apps/${appId}/usage-scope`);
  return resp.json();
}

/** 使用范围切到“全平台”。 */
async function setUsageScopePlatform(page: Page): Promise<void> {
  const scopeCard = card(page, "使用范围");
  await scopeCard.getByRole("radio", { name: /全平台/ }).click();
  await scopeCard.getByRole("button", { name: /保\s*存/ }).click();
  await expect
    .poll(async () => (await readUsageScope(page)).scope, { timeout: 15_000 })
    .toBe("platform");
}

/** 在对话面板里提问并等回答完成。 */
async function askInPanel(page: Page, panel: Locator, question: string): Promise<void> {
  const input = panel.locator("textarea").last();
  await input.fill(question);
  await panel.getByRole("button", { name: /发\s*送/ }).click();
  // 先等流式开始，再等流式结束（发送按钮回来），最后确认回答有内容。
  await expect(panel.getByRole("button", { name: "停止生成" })).toBeVisible({ timeout: 30_000 });
  await expect(panel.getByRole("button", { name: /发\s*送/ })).toBeVisible({ timeout: 90_000 });
  await expect(panel.locator('[data-testid="chat-answer"]').last()).toContainText("这是本地", {
    timeout: 30_000,
  });
}

// ---------------------------------------------------------------------------
// 接口辅助
// ---------------------------------------------------------------------------

async function getDraft(page: Page): Promise<{
  draft_revision: number;
  config: AppConfigJson;
}> {
  const resp = await page.request.get(`/api/v1/apps/${appId}/draft`);
  expect(resp.status()).toBe(200);
  return resp.json();
}

interface AppConfigJson {
  knowledge: {
    kbs: { kb_id: number; retrieval: unknown; filter: unknown; qa_category_ids: number[] }[];
    doc: { rerank: { enabled: boolean; model_id: number | null; keep: number } };
  };
  [key: string]: unknown;
}

async function getTurns(page: Page, conversationId: number): Promise<LogTurn[]> {
  const resp = await page.request.get(`/api/v1/conversations/${conversationId}/turns`);
  expect(resp.status()).toBe(200);
  return resp.json();
}

interface LogTurn {
  id: number;
  answers: { id: number; status: string }[];
}

/** 轮询会话的最后一问，等回答达到指定状态，返回回答 id。 */
async function waitForAnswerStatus(
  page: Page,
  conversationId: number,
  status: string,
): Promise<number> {
  for (let attempt = 0; attempt < 30; attempt += 1) {
    const turns = await getTurns(page, conversationId);
    const answer = turns.at(-1)?.answers.at(-1);
    if (answer?.status === status) {
      return answer.id;
    }
    await page.waitForTimeout(500);
  }
  return 0;
}

/** 取终端用户最近一次提问的回答 id。 */
async function lastAnswerId(page: Page): Promise<number> {
  const conversationId = Number(/\/chat\/\d+\/(\d+)/.exec(page.url())?.[1]);
  const turns = await getTurns(page, conversationId);
  const answer = turns.at(-1)?.answers.at(-1);
  expect(answer).toBeTruthy();
  return answer?.id ?? 0;
}

function citationFileUrl(answerId: number, n: number): string {
  return `/api/v1/answers/${answerId}/citations/${n}/file?download=true`;
}

async function citationNumberOf(locator: Locator): Promise<number> {
  const testId = await locator.getAttribute("data-testid");
  return Number(/answer-citation-(\d+)/.exec(testId ?? "")?.[1] ?? 1);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** 等 AntD 表格的加载动画结束（负载下数据可能比首屏晚到）。 */
async function waitTableLoaded(page: Page): Promise<void> {
  await page
    .locator(".ant-spin-spinning")
    .waitFor({ state: "hidden", timeout: 15_000 })
    .catch(() => undefined);
}

/** 生成一个最小的两页 PDF（A4 595×842），引用预览要能渲染页面才能叠加高亮框。 */
function buildPdf(): Buffer {
  const stream1 = "BT /F1 24 Tf 72 700 Td (Chapter 1 Travel Reimbursement) Tj ET\n";
  const stream2 = "BT /F1 24 Tf 72 700 Td (Chapter 2 Reimbursement Standard) Tj ET\n";
  const objects = new Map<number, string>([
    [1, "<< /Type /Catalog /Pages 2 0 R >>"],
    [2, "<< /Type /Pages /Kids [3 0 R 5 0 R] /Count 2 >>"],
    [
      3,
      "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 7 0 R >> >> /Contents 4 0 R >>",
    ],
    [4, `<< /Length ${Buffer.byteLength(stream1)} >>\nstream\n${stream1}endstream`],
    [
      5,
      "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 7 0 R >> >> /Contents 6 0 R >>",
    ],
    [6, `<< /Length ${Buffer.byteLength(stream2)} >>\nstream\n${stream2}endstream`],
    [7, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"],
  ]);

  const chunks: string[] = ["%PDF-1.4\n%\xE2\xE3\xCF\xD3\n"];
  const offsets = new Map<number, number>();
  let size = Buffer.byteLength(chunks[0], "latin1");
  for (let num = 1; num <= 7; num += 1) {
    offsets.set(num, size);
    const part = `${num} 0 obj\n${objects.get(num)}\nendobj\n`;
    chunks.push(part);
    size += Buffer.byteLength(part, "latin1");
  }

  let xref = "xref\n0 8\n0000000000 65535 f \n";
  for (let num = 1; num <= 7; num += 1) {
    xref += `${String(offsets.get(num)).padStart(10, "0")} 00000 n \n`;
  }
  xref += `trailer\n<< /Size 8 /Root 1 0 R >>\nstartxref\n${size}\n%%EOF\n`;
  chunks.push(xref);
  return Buffer.from(chunks.join(""), "latin1");
}
