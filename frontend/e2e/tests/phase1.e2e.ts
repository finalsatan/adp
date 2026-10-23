/**
 * 第一期端到端用例（P1-19，技术方案 13.2 的“端到端”一条）。
 *
 * 用例之间有先后关系：先建组织、用户，再分别用这些账号登录验证。
 * 单 worker 顺序执行，整份文件共享同一个 adp_e2e（`make e2e` 会先重置）。
 */
import { expect, test } from "@playwright/test";

import {
  ADMIN_INITIAL_PASSWORD,
  ADMIN_PASSWORD,
  ADMIN_USERNAME,
  BASE_URL,
  BUILDER_NAME,
  BUILDER_NEW_PASSWORD,
  BUILDER_PASSWORD,
  BUILDER_USERNAME,
  DEV_ORG,
  END_USER_NAME,
  END_USER_PASSWORD,
  END_USER_USERNAME,
  INITIAL_PASSWORD,
  ORG_ADMIN_NAME,
  ORG_ADMIN_PASSWORD,
  ORG_ADMIN_USERNAME,
  PRODUCT_ORG,
  TOP_ORG,
} from "../config";
import {
  changeInitialPassword,
  createOrg,
  createUser,
  expectAuditRow,
  login,
  loginWithOptionalChange,
} from "../helpers";

test.describe.serial("第一期端到端", () => {
  test("1. 管理员首次登录后改密，侧栏有系统管理五项", async ({ page }) => {
    await login(page, ADMIN_USERNAME, ADMIN_INITIAL_PASSWORD);
    await expect(page).toHaveURL(/\/change-password/);
    await changeInitialPassword(page, ADMIN_PASSWORD);

    // 改密后回根路径，HomeRedirect 跳到侧栏第一个可见菜单（第二期起是模型管理）
    await expect(page).toHaveURL(/\/models/);
    const menu = page.locator(".ant-menu");
    for (const label of ["用户管理", "组织管理", "资源总览", "审计日志", "系统设置"]) {
      await expect(menu.getByText(label)).toBeVisible();
    }
  });

  test("2. 新建顶级组织和下级组织，并在研发中心建组织管理员、搭建者", async ({
    page,
  }) => {
    await login(page, ADMIN_USERNAME, ADMIN_PASSWORD);
    await expect(page).toHaveURL(/\/models/);

    await createOrg(page, TOP_ORG);
    await createOrg(page, DEV_ORG, TOP_ORG);
    await createOrg(page, PRODUCT_ORG, TOP_ORG);

    await createUser(page, {
      username: ORG_ADMIN_USERNAME,
      name: ORG_ADMIN_NAME,
      org: DEV_ORG,
      role: "组织管理员",
      password: INITIAL_PASSWORD,
    });
    await createUser(page, {
      username: BUILDER_USERNAME,
      name: BUILDER_NAME,
      org: DEV_ORG,
      role: "搭建者",
      password: INITIAL_PASSWORD,
    });
  });

  test("3. 组织管理员：用户管理只见研发中心，无系统设置，直访 403", async ({
    page,
  }) => {
    await loginWithOptionalChange(
      page,
      ORG_ADMIN_USERNAME,
      INITIAL_PASSWORD,
      ORG_ADMIN_PASSWORD,
    );
    await expect(page).toHaveURL(/\/models/);
    await page.goto("/system/users");

    const tree = page.getByRole("tree");
    await expect(tree.getByText(DEV_ORG)).toBeVisible();
    await expect(tree.getByText(TOP_ORG)).toHaveCount(0);
    await expect(tree.getByText(PRODUCT_ORG)).toHaveCount(0);

    const menu = page.locator(".ant-menu");
    await expect(menu.getByText("系统设置")).toHaveCount(0);
    await expect(menu.getByText("用户管理")).toBeVisible();

    await page.goto("/system/settings");
    await expect(page.getByText("没有权限访问该页面")).toBeVisible();
  });

  test("4. 搭建者：无系统管理分组，个人中心改姓名，改密后仍可操作", async ({
    page,
  }) => {
    await loginWithOptionalChange(
      page,
      BUILDER_USERNAME,
      INITIAL_PASSWORD,
      BUILDER_PASSWORD,
    );
    // 搭建者有“模型服务”菜单，落到模型管理；个人资料要自己进个人中心
    await expect(page).toHaveURL(/\/models/);
    await expect(page.locator(".ant-menu").getByText("系统管理")).toHaveCount(0);

    await page.goto("/profile");
    await page.locator("#name").fill(`${BUILDER_NAME}甲`);
    await page.getByRole("button", { name: /保\s*存/ }).click();
    await expect(page.getByText("已保存").first()).toBeVisible();

    await page.locator("#old_password").fill(BUILDER_PASSWORD);
    await page.locator("#new_password").fill(BUILDER_NEW_PASSWORD);
    await page.locator("#confirm_password").fill(BUILDER_NEW_PASSWORD);
    await page.getByRole("button", { name: "修改密码" }).click();
    await expect(page.getByText("密码已修改")).toBeVisible();

    // 改密后当前页面仍可操作：再保存一次资料
    await page.locator("#name").fill(`${BUILDER_NAME}乙`);
    await page.getByRole("button", { name: /保\s*存/ }).click();
    await expect(page.getByText("已保存").first()).toBeVisible();
  });

  test("5. 终端用户：进入我的应用空页面，访问用户管理被带回", async ({
    page,
    browser,
  }) => {
    await login(page, ADMIN_USERNAME, ADMIN_PASSWORD);
    await expect(page).toHaveURL(/\/models/);
    await createUser(page, {
      username: END_USER_USERNAME,
      name: END_USER_NAME,
      org: DEV_ORG,
      role: "终端用户",
      password: INITIAL_PASSWORD,
    });

    // 另开一个浏览器上下文，避免和上面管理员的会话冲突
    const context = await browser.newContext({ baseURL: BASE_URL });
    const endUserPage = await context.newPage();
    try {
      await loginWithOptionalChange(
        endUserPage,
        END_USER_USERNAME,
        INITIAL_PASSWORD,
        END_USER_PASSWORD,
      );
      await expect(endUserPage).toHaveURL(/\/my-apps/);
      await expect(endUserPage.getByText("暂无可用的应用")).toBeVisible();

      await endUserPage.goto("/system/users");
      await expect(endUserPage).toHaveURL(/\/my-apps/);
    } finally {
      await context.close();
    }
  });

  test("6. 密码错 5 次后锁定，管理员解锁后能登录", async ({ page, browser }) => {
    const wrongPassword = "WrongPassword!9";

    for (let i = 0; i < 5; i += 1) {
      await login(page, END_USER_USERNAME, wrongPassword);
      await expect(page.getByText("用户名或密码错误")).toBeVisible();
    }
    await login(page, END_USER_USERNAME, wrongPassword);
    await expect(page.getByText(/尝试次数过多/)).toBeVisible();

    // 管理员在用户列表看到“已锁定”并解锁
    await login(page, ADMIN_USERNAME, ADMIN_PASSWORD);
    await expect(page).toHaveURL(/\/models/);
    await page.goto("/system/users");
    const row = page.getByRole("row", { name: new RegExp(END_USER_USERNAME) });
    await expect(row.getByText("已锁定")).toBeVisible();
    await row.getByText("解锁").click();
    await expect(page.getByText("已解锁")).toBeVisible();

    // 解锁后能登录
    const context = await browser.newContext({ baseURL: BASE_URL });
    const endUserPage = await context.newPage();
    try {
      await login(endUserPage, END_USER_USERNAME, END_USER_PASSWORD);
      await expect(endUserPage).toHaveURL(/\/my-apps/);
    } finally {
      await context.close();
    }
  });

  test("7. 审计日志能查到登录、创建用户、解锁记录", async ({ page }) => {
    await login(page, ADMIN_USERNAME, ADMIN_PASSWORD);
    await expect(page).toHaveURL(/\/models/);

    await expectAuditRow(page, "登录", ADMIN_USERNAME);
    await expectAuditRow(page, "创建用户", ORG_ADMIN_USERNAME);
    await expectAuditRow(page, "解锁用户", END_USER_USERNAME);
  });
});
