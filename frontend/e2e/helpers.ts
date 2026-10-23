/** 端到端用例共用的操作封装。 */
import { expect, type Page } from "@playwright/test";

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

/** 审计日志页：按动作筛选，并断言存在包含目标文本的记录。 */
export async function expectAuditRow(
  page: Page,
  action: string,
  target?: string,
): Promise<void> {
  await page.goto("/system/audit");
  await page.getByText("全部动作").click();

  // 选可见的下拉项；Select 里还渲染了一份给读屏用的隐藏 listbox，不能点那个。
  // 动作有二十多个，AntD 虚拟列表只渲染窗口内的项；按一个选项的高度逐格滚动，
  // 等 React 重渲染后目标项出现（否则会一次滑到底，跳过中间的项）。
  const dropdown = page.locator(".ant-select-dropdown:visible");
  // 下拉是点击后异步渲染的，先等它和第一项出现，否则量到的虚拟列表高度是 0
  await expect(dropdown).toBeVisible();
  await expect(dropdown.locator(".ant-select-item-option").first()).toBeVisible();
  const option = dropdown.locator(".ant-select-item-option", { hasText: action }).first();
  const holder = dropdown.locator(".rc-virtual-list-holder");
  if ((await holder.count()) > 0) {
    const maxScroll = await holder.evaluate((el) =>
      Math.max(0, el.scrollHeight - el.clientHeight),
    );
    for (let top = 0; top <= maxScroll; top += 32) {
      await holder.evaluate((el, y) => {
        el.scrollTop = y;
      }, top);
      await page.waitForTimeout(50);
      if ((await option.count()) > 0) {
        break;
      }
    }
  }
  await option.click();

  let row = page.getByRole("row").filter({ hasText: action });
  if (target) {
    row = row.filter({ hasText: target });
  }
  await expect(row.first()).toBeVisible();
}
