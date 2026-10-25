/** 端到端用例共用的操作封装。 */
import { expect, type Locator, type Page } from "@playwright/test";

/** 打开登录页并提交用户名 / 密码。 */
export async function login(
  page: Page,
  username: string,
  password: string,
): Promise<void> {
  await page.goto("/login");
  await page.locator("#username").fill(username);
  await page.locator("#password").fill(password);
  // AntD 会给两个汉字的按钮插一个空格（“登 录”），所以用正则匹配
  await page.getByRole("button", { name: /登\s*录/ }).click();
}

/** 首次登录（或管理员重置密码后）在改密页设置新密码并进入平台。 */
export async function changeInitialPassword(
  page: Page,
  newPassword: string,
): Promise<void> {
  await expect(page).toHaveURL(/\/change-password/);
  await page.locator("#new_password").fill(newPassword);
  await page.locator("#confirm_password").fill(newPassword);
  await page.getByRole("button", { name: "确认并进入平台" }).click();
}

/** 登录；给了 newPassword 就按首次登录处理，先完成强制改密。 */
export async function loginWithOptionalChange(
  page: Page,
  username: string,
  password: string,
  newPassword?: string,
): Promise<void> {
  await login(page, username, password);
  if (newPassword) {
    await changeInitialPassword(page, newPassword);
  }
}

/** 在组织管理页新建组织；parentName 为空时新建顶级组织。 */
export async function createOrg(
  page: Page,
  name: string,
  parentName?: string,
): Promise<void> {
  await page.goto("/system/orgs");
  if (parentName) {
    const parentRow = page.getByRole("row", { name: new RegExp(parentName) });
    await parentRow.getByText("添加下级").click();
  } else {
    await page.getByRole("button", { name: "新建顶级组织" }).click();
  }
  await page.locator("#name").fill(name);
  await page.getByRole("button", { name: /创\s*建/ }).click();
  await expect(page.getByRole("row", { name: new RegExp(name) })).toBeVisible();
}

export interface NewUser {
  username: string;
  name: string;
  org: string;
  role: "组织管理员" | "搭建者" | "终端用户";
  password: string;
}

/** 在用户管理页新建一个用户。 */
export async function createUser(page: Page, user: NewUser): Promise<void> {
  await page.goto("/system/users");
  await page.getByRole("button", { name: "新建用户" }).click();
  await page.locator("#username").fill(user.username);
  await page.locator("#name").fill(user.name);
  await page.locator("#password").fill(user.password);
  await page.locator("#org_id").click();
  await page.locator(".ant-select-tree-title", { hasText: user.org }).first().click();
  await page.getByRole("radio", { name: user.role }).check();
  await page.getByRole("button", { name: /创\s*建/ }).click();
  await expect(page.getByText("用户已创建")).toBeVisible();
}

/** 审计日志页：按动作筛选，并断言存在包含目标文本的记录。 */export async function expectAuditRow(
  page: Page,
  action: string,
  target?: string,
): Promise<void> {
  await page.goto("/system/audit");
  await page.getByText("全部动作").click();

  // 选可见的下拉项；Select 里还渲染了一份给读屏用的隐藏 listbox，不能点那个。
  // 动作有二十多个，AntD 虚拟列表只渲染窗口内的项。逐屏滚动，每滚一次都等目标项
  // 出现（机器慢时 React 重渲染会超过一帧）；一次测量不准就绕回顶部再滚，避免死循环。
  const dropdown = page.locator(".ant-select-dropdown:visible");
  // 下拉是点击后异步渲染的，先等它和第一项出现，否则量到的虚拟列表高度是 0
  await expect(dropdown).toBeVisible();
  await expect(dropdown.locator(".ant-select-item-option").first()).toBeVisible();
  const option = dropdown.locator(".ant-select-item-option", { hasText: action }).first();
  const holder = dropdown.locator(".rc-virtual-list-holder");
  if ((await holder.count()) > 0) {
    for (let attempt = 0; attempt < 30; attempt += 1) {
      if ((await option.count()) > 0) {
        break;
      }
      await holder.evaluate((el) => {
        const max = Math.max(0, el.scrollHeight - el.clientHeight);
        const step = Math.max(48, Math.floor(el.clientHeight / 2));
        el.scrollTop = el.scrollTop + step >= max ? 0 : el.scrollTop + step;
      });
      // 等这一屏渲染出来；命中就结束，否则继续滚
      await option.waitFor({ state: "attached", timeout: 500 }).catch(() => undefined);
    }
  }
  await option.click();

  let row = page.getByRole("row").filter({ hasText: action });
  if (target) {
    row = row.filter({ hasText: target });
  }
  await expect(row.first()).toBeVisible();
}

/**
 * 预热本地假模型服务：机器内存紧张时它可能被换出，首次调用会超过连通测试的
 * 10 秒产品超时（需求 3.4）。测试前先打一次真实请求，把代码页和 HTTP 连接拉热。
 */
async function warmUpFakeModel(page: Page, baseUrl: string): Promise<void> {
  await page.request.post(`${baseUrl}/chat/completions`, {
    data: {
      model: "openai/warmup",
      messages: [{ role: "user", content: "warmup" }],
    },
    timeout: 60_000,
  });
}

/**
 * 在模型抽屉里点“测试连通”并等成功。产品侧的 10 秒超时（需求 3.4）不动：
 * 先在测试侧预热假模型服务，机器负载高时首次调用仍超时就重试一次，
 * 避免把负载抖动当成产品失败。
 */
export async function expectConnectivitySuccess(
  page: Page,
  drawer: Locator,
  baseUrl: string,
): Promise<void> {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    await warmUpFakeModel(page, baseUrl);
    await drawer.getByRole("button", { name: "测试连通" }).click();
    try {
      await expect(drawer.getByText(/✓ 连通成功/)).toBeVisible({ timeout: 20_000 });
      return;
    } catch (error) {
      if (attempt === 1) {
        throw error;
      }
    }
  }
}
