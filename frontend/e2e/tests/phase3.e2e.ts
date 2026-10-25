/**
 * 第三期端到端用例（P3-19，知识库技术方案 19.3 的“端到端”一条）。
 *
 * 依赖第一期用例先建好的组织和用户（文件名 phase1 < phase2 < phase3，单 worker 顺序执行）。
 * 后端连 `adp_e2e`、Redis 12 号库，对象存储用独立的桶 `adp-e2e`；
 * 解析走本地假解析服务（59010），模型调用走本地假模型服务（9999），
 * 入库由 webServer 里起的 Celery worker 执行，不需要真实解析引擎和云模型密钥。
 *
 * 覆盖：建库 → 上传 PDF → 等待完成 → 命中测试 → 点结果打开原文并高亮；
 * 以及 Word 的解析结果段落高亮、文档启停与批量删除、停用知识库后命中测试返回空结果。
 */
import { expect, test, type Page } from "@playwright/test";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import {
  ADMIN_PASSWORD,
  ADMIN_USERNAME,
  BUILDER_NEW_PASSWORD,
  BUILDER_USERNAME,
  FAKE_LLM_BASE_URL,
} from "../config";
import { expectConnectivitySuccess, login } from "../helpers";

const EMBEDDING_MODEL = "P3假向量模型";
const EMBEDDING_MODEL_ID = "openai/fake-embedding";
const FAKE_API_KEY = "fake-key";
const KB_NAME = "P3端到端文档库";
const FOLDER_NAME = "差旅制度";
const PDF_NAME = "员工手册.pdf";
const WORD_NAME = "员工手册.docx";
const PDF_QUESTION = "差旅报销住宿费标准";
const WORD_QUESTION = "入职当天需要携带什么";

// 上传的文件在测试开始时写到临时目录：PDF 需要是合法可渲染的 PDF（原文预览要用），
// Word 只要扩展名是 .docx 即可（假解析服务按扩展名返回固定样例，不读内容）。
const TMP_DIR = fs.mkdtempSync(path.join(os.tmpdir(), "adp-e2e-p3-"));
const PDF_PATH = path.join(TMP_DIR, PDF_NAME);
const WORD_PATH = path.join(TMP_DIR, WORD_NAME);

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

test.describe.serial("第三期端到端", () => {
  test.describe.configure({ timeout: 150_000 });

  test("1. 管理员加白名单、添加向量化模型", async ({ page }) => {
    await login(page, ADMIN_USERNAME, ADMIN_PASSWORD);
    await expect(page).toHaveURL(/\/models/);
    await allowLocalModelHost(page);
    await addEmbeddingModel(page);
  });

  test("2. 搭建者建库、建文件夹、上传 PDF 并完成入库", async ({ page }) => {
    await loginBuilder(page);
    await createKb(page);
    const kbId = await openKb(page, KB_NAME);
    await createFolder(page, FOLDER_NAME);
    await selectFolder(page, FOLDER_NAME);
    await uploadDocument(page, PDF_PATH);
    await waitDocumentDone(page, PDF_NAME);

    // 文档数与分块数更新（重新加载拿最新的知识库计数）
    await page.goto(`/kb/${kbId}`);
    await expect(page.getByText(/1 个文档 · [1-9]\d* 个分块/)).toBeVisible({
      timeout: 30_000,
    });
  });

  test("3. 命中测试四步有结果和耗时，点结果打开 PDF 原文并高亮", async ({ page }) => {
    await loginBuilder(page);
    const kbId = await openKb(page, KB_NAME);
    await runHitTest(page, kbId, PDF_QUESTION);

    // 四步都渲染出来（重排未开启，第三期没有本地假重排服务，见完成记录）
    await expect(page.getByText(/向量召回 [1-9]\d*/)).toBeVisible();
    await expect(page.getByText(/全文召回 [1-9]\d*/)).toBeVisible();
    await expect(page.getByText(/^融合 [1-9]\d*$/)).toBeVisible();
    await expect(page.getByText(/^重排 \d+$/)).toBeVisible();
    // 默认显示融合后的结果，耗时行有四个阶段的耗时
    await expect(page.getByText(/融合后的 [1-9]\d* 条/)).toBeVisible();
    await expect(page.getByText(/向量 \d+ms · 全文 \d+ms · 重排 \d+ms/)).toBeVisible();

    // 切到向量 / 全文步骤也能看到各自的结果
    await page.getByText(/向量召回 [1-9]\d*/).click();
    await expect(page.getByText(/向量召回的 [1-9]\d* 条/)).toBeVisible();
    await page.getByText(/全文召回 [1-9]\d*/).click();
    await expect(page.getByText(/全文召回的 [1-9]\d* 条/)).toBeVisible();
    await page.getByText(/^融合 [1-9]\d*$/).click();
    await expect(page.getByText(/融合后的 [1-9]\d* 条/)).toBeVisible();

    // 点“原文位置”打开 PDF 预览并出现高亮框
    await page.getByRole("link", { name: "原文位置" }).first().click();
    await expect(page).toHaveURL(/\/kb\/\d+\/documents\/\d+/);
    await expect(page.getByText(/第 \d+ \/ \d+ 页/)).toBeVisible({ timeout: 30_000 });
    await expect(page.getByTestId("pdf-highlight").first()).toBeVisible({
      timeout: 30_000,
    });
  });

  test("4. 上传 Word，命中测试点结果在解析结果视图高亮对应段落", async ({ page }) => {
    await loginBuilder(page);
    const kbId = await openKb(page, KB_NAME);
    await selectFolder(page, FOLDER_NAME);
    await uploadDocument(page, WORD_PATH);
    await waitDocumentDone(page, WORD_NAME);

    await runHitTest(page, kbId, WORD_QUESTION);
    await page.getByRole("link", { name: "原文位置" }).first().click();
    await expect(page).toHaveURL(/\/kb\/\d+\/documents\/\d+/);

    const parseResult = page.getByTestId("parse-result");
    await expect(parseResult).toBeVisible({ timeout: 30_000 });
    const paragraph = parseResult.locator("p", {
      hasText: "新员工入职当天需携带身份证和学历证明到人力资源部报到。",
    });
    await expect(paragraph).toHaveCSS("background-color", "rgb(255, 251, 230)", {
      timeout: 30_000,
    });
  });

  test("5. 文档启停与批量删除", async ({ page }) => {
    await loginBuilder(page);
    const kbId = await openKb(page, KB_NAME);
    await selectFolder(page, FOLDER_NAME);

    const pdfRow = page.getByRole("row", { name: new RegExp(escapeRegExp(PDF_NAME)) });
    await expect(pdfRow).toBeVisible();
    await pdfRow.getByRole("switch").click();
    await expect(page.getByText("已停用").first()).toBeVisible();
    await pdfRow.getByRole("switch").click();
    await expect(page.getByText("已启用").first()).toBeVisible();

    const wordRow = page.getByRole("row", { name: new RegExp(escapeRegExp(WORD_NAME)) });
    await expect(wordRow).toBeVisible();
    await wordRow.getByRole("checkbox").check();
    await page.getByRole("button", { name: /批量操作/ }).click();
    await page.locator(".ant-dropdown:visible").getByText("删除", { exact: true }).click();
    const confirm = page.locator(".ant-modal-content").filter({ hasText: "删除" });
    await confirm.getByRole("button", { name: /删\s*除/ }).click();
    await expect(page.getByText("已删除").first()).toBeVisible();
    await expect(wordRow).toHaveCount(0);

    // 删除后只剩 PDF，计数回到 1 个文档
    await page.goto(`/kb/${kbId}`);
    await expect(page.getByText(/1 个文档 · [1-9]\d* 个分块/)).toBeVisible({
      timeout: 30_000,
    });
  });

  test("6. 停用知识库后命中测试仍能跑，返回空结果", async ({ page }) => {
    await loginBuilder(page);
    const kbId = await kbIdFromList(page, KB_NAME);

    const kbRow = page.getByRole("row", { name: new RegExp(KB_NAME) });
    await kbRow.getByRole("button", { name: "更多操作" }).click();
    await page.locator(".ant-dropdown:visible").getByText("停用", { exact: true }).click();
    await confirmKbStop(page);

    await runHitTest(page, kbId, PDF_QUESTION);
    await expect(page.getByText("该步骤没有结果")).toBeVisible();
    await expect(page.getByText(/融合后的 0 条/)).toBeVisible();
  });
});

/** 系统设置：把本机地址加入模型接口地址白名单，否则保存和测试都会被拒。 */
async function allowLocalModelHost(page: Page): Promise<void> {
  await page.goto("/system/settings");
  const allowlist = page.locator("#model_call_model_host_allowlist");
  await expect(allowlist).toBeVisible();
  // 第二期用例已加过时不再保存（值没变时设置页不发请求、不弹“已保存”）。
  if ((await allowlist.inputValue()).includes("127.0.0.1")) {
    return;
  }
  await allowlist.fill("127.0.0.1");
  await page.getByRole("button", { name: /保\s*存/ }).click();
  await expect(page.getByText("已保存").first()).toBeVisible();
}

/** 添加一个向量化模型：填表 → 保存前测试连通 → 保存。维度要和假模型服务一致（1024）。 */
async function addEmbeddingModel(page: Page): Promise<void> {
  await page.goto("/models");
  // 等模型列表加载完（第二期已建好“假模型A”），再判断本次是否已经建过。
  await expect(page.getByRole("row", { name: /假模型A/ })).toBeVisible();
  if ((await page.getByRole("row", { name: new RegExp(EMBEDDING_MODEL) }).count()) > 0) {
    return;
  }
  await page.getByRole("button", { name: "添加模型" }).click();
  const drawer = page.locator(".ant-drawer-content");
  await expect(drawer).toBeVisible();
  await drawer.locator("#name").fill(EMBEDDING_MODEL);
  await drawer.getByRole("radio", { name: "向量化" }).check();
  await drawer.locator("#litellm_model").fill(EMBEDDING_MODEL_ID);
  await drawer.locator("#api_base").fill(FAKE_LLM_BASE_URL);
  await drawer.locator("#api_key").fill(FAKE_API_KEY);
  await drawer.locator("#embedding_dim").fill("1024");
  await drawer.locator("#embedding_max_input").fill("8192");
  await drawer.locator("#embedding_batch_size").fill("16");

  await expectConnectivitySuccess(page, drawer, FAKE_LLM_BASE_URL);

  await drawer.getByRole("button", { name: /保\s*存/ }).click();
  await expect(page.getByText("模型已添加").first()).toBeVisible();
  await expect(page.getByRole("row", { name: new RegExp(EMBEDDING_MODEL) })).toBeVisible();
}

/** 搭建者新建一个文档库（向量化模型选刚添加的假模型，可见范围用默认“本组织”）。 */
async function createKb(page: Page): Promise<void> {
  await page.goto("/kb");
  await page.getByRole("button", { name: "新建知识库" }).click();
  const modal = page.locator(".ant-modal-content").filter({ hasText: "新建知识库" });
  await expect(modal).toBeVisible();
  await modal.locator("#name").fill(KB_NAME);
  await modal.locator("#embedding_model_id").click();
  await page
    .locator(".ant-select-dropdown:visible .ant-select-item-option")
    .filter({ hasText: EMBEDDING_MODEL })
    .first()
    .click();
  await modal.getByRole("button", { name: /创\s*建/ }).click();
  await expect(page.getByText("已创建").first()).toBeVisible();
  await expect(page.getByRole("row", { name: new RegExp(KB_NAME) })).toBeVisible();
}

/** 打开知识库文档页，返回知识库 id。 */
async function openKb(page: Page, name: string): Promise<number> {
  await page.goto("/kb");
  const row = page.getByRole("row", { name: new RegExp(name) });
  await expect(row).toBeVisible();
  await row.getByText("进入").click();
  await expect(page).toHaveURL(/\/kb\/\d+$/);
  const match = /\/kb\/(\d+)/.exec(page.url());
  return Number(match?.[1]);
}

/** 在知识库列表里读出 id，不离开列表页（只用到 id 的用例用这个）。 */
async function kbIdFromList(page: Page, name: string): Promise<number> {
  await page.goto("/kb");
  const row = page.getByRole("row", { name: new RegExp(name) });
  await expect(row).toBeVisible();
  const href = await row.getByText("进入").getAttribute("href");
  const match = /\/kb\/(\d+)/.exec(href ?? "");
  return Number(match?.[1]);
}

/** 点“确认停用”并等“已停用”。弹窗 zoom 动画期间按钮位置还在变，点击可能落空，
 * 先等动画结束，必要时再重试一次。 */
async function confirmKbStop(page: Page): Promise<void> {
  const stopModal = page.locator(".ant-modal-content").filter({ hasText: "停用知识库" });
  await expect(stopModal).toBeVisible();
  await expect(page.locator(".ant-modal").filter({ hasText: "停用知识库" }))
    .not.toHaveClass(/ant-zoom-(enter|appear)/, { timeout: 5_000 })
    .catch(() => undefined);
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await stopModal.getByRole("button", { name: "确认停用" }).click();
    try {
      await expect(page.getByText("已停用").first()).toBeVisible({ timeout: 15_000 });
      return;
    } catch (error) {
      if (attempt === 1) {
        throw error;
      }
    }
  }
}

/** 新建一个文件夹。 */
async function createFolder(page: Page, name: string): Promise<void> {
  await page.getByText("+ 新建").click();
  const modal = page.locator(".ant-modal-content").filter({ hasText: "新建文件夹" });
  await expect(modal).toBeVisible();
  await modal.locator("#name").fill(name);
  await modal.getByRole("button", { name: /创\s*建/ }).click();
  await expect(page.getByText("已创建").first()).toBeVisible();
}

/** 在左侧文件夹树里选中某个文件夹（上传默认落到它）。 */
async function selectFolder(page: Page, name: string): Promise<void> {
  await page.getByText(name, { exact: true }).click();
}

/** 打开上传弹窗，选一个文件，预检后上传。 */
async function uploadDocument(page: Page, filePath: string): Promise<void> {
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

/** 等文档从排队中走到已完成（列表每 5 秒自动轮询）。 */
async function waitDocumentDone(page: Page, name: string): Promise<void> {
  const row = page.getByRole("row", { name: new RegExp(escapeRegExp(name)) });
  await expect(row).toBeVisible({ timeout: 30_000 });
  await expect(row.getByText("已完成")).toBeVisible({ timeout: 90_000 });
}

/** 搭建者登录；登录 POST 是异步的，等落到首页再继续，避免随后跳转抢在设置 Cookie 之前。 */
async function loginBuilder(page: Page): Promise<void> {
  await login(page, BUILDER_USERNAME, BUILDER_NEW_PASSWORD);
  await expect(page).toHaveURL(/\/models/);
}

/** 在命中测试页输入问题并点“测试”，等结果出来。 */
async function runHitTest(page: Page, kbId: number, question: string): Promise<void> {
  await page.goto(`/kb/${kbId}/hit-test`);
  await page.getByPlaceholder(/输入问题/).fill(question);
  await page.getByRole("button", { name: /测\s*试/ }).click();
  await expect(page.getByText(/当前显示：/)).toBeVisible({ timeout: 30_000 });
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * 生成一个最小的两页 PDF（A4 点尺寸 595×842）。
 * 假解析服务返回的固定位置数据在 PDF 的第 1 页，原文预览要能渲染出页面才能叠加高亮框。
 */
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
