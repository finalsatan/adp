import test from 'node:test';
import assert from 'node:assert/strict';
import { chromium } from 'playwright';
import { existsSync } from 'node:fs';
import { mkdir } from 'node:fs/promises';
import path from 'node:path';
import { apiClient, bootServer, root } from './helpers/runtime.mjs';

test('浏览器：初始化、知识入库、发布聊天、引用反馈与手机布局', { timeout: 180000 }, async t => {
  const runtime = await bootServer();
  t.after(() => runtime.close());
  const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || (existsSync(chrome) ? chrome : undefined);
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  t.after(() => browser.close());
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = [];
  const serverErrors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => { if (response.status() >= 500) serverErrors.push(`${response.status()} ${response.url()}`); });
  const outputDir = path.join(root, 'test-results/browser');
  await mkdir(outputDir, { recursive: true });
  async function screenshot(name) {
    await page.screenshot({ path: path.join(outputDir, `${name}.png`), fullPage: true, animations: 'disabled' });
  }
  try {
    await page.goto(runtime.baseUrl);
    await page.locator('#setup-name').fill('浏览器验收员');
    await page.locator('#setup-org').fill('界面验收组织');
    await page.locator('#setup-email').fill('browser@example.test');
    await page.locator('#setup-password').fill('BrowserAcceptance2026!');
    await page.locator('#setup-confirm').fill('BrowserAcceptance2026!');
    await screenshot('01-setup');
    await page.getByRole('button', { name: '创建管理员并进入', exact: true }).click();
    await page.waitForURL('**/knowledge');
    await page.getByRole('button', { name: '新建知识库', exact: true }).first().click();
    await page.locator('#kb-name').fill('浏览器验收库');
    await page.getByRole('button', { name: '创建知识库', exact: true }).click();
    await page.waitForURL(/\/knowledge\/[^/]+$/);
    await page.getByRole('button', { name: '上传文档', exact: true }).first().click();
    // The upload dialog's file input accepts the actual fixture from disk.
    await page.getByRole('dialog').locator('input[type=file]').setInputFiles(path.join(root, 'examples/费用报销.md'));
    await page.getByRole('button', { name: '加入处理队列', exact: true }).click();
    await page.getByText('费用报销.md', { exact: true }).first().waitFor();
    await page.getByRole('row').filter({ hasText: '费用报销.md' }).getByText('已完成', { exact: true }).waitFor({ timeout: 15000 });
    await screenshot('02-knowledge');

    await page.getByRole('link', { name: '知识问答', exact: true }).click();
    await page.getByRole('button', { name: '新建知识问答', exact: true }).first().click();
    await page.locator('#app-name').fill('差旅问答验收');
    await page.getByRole('checkbox', { name: /浏览器验收库/ }).check();
    await page.getByRole('button', { name: '创建并进入编辑', exact: true }).click();
    await page.waitForURL(/\/apps\/[^/]+$/);
    const appId = new URL(page.url()).pathname.split('/').at(-1);
    await page.getByRole('tab', { name: '发布与嵌入', exact: true }).click();
    await page.getByRole('button', { name: '发布新版本', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: '发布新版本', exact: true }).click();
    await page.getByText(/当前线上版本 v1/).waitFor();
    await screenshot('03-published');

    await page.goto(`${runtime.baseUrl}/chat/${appId}`);
    await page.getByRole('textbox', { name: '输入问题', exact: true }).fill('一线城市每人每晚住宿上限是多少？');
    await page.getByRole('button', { name: '发送', exact: true }).click();
    await page.locator('.message-assistant').filter({ hasText: '600' }).waitFor();
    await page.locator('.message-assistant').getByRole('button', { name: '没帮助', exact: true }).first().waitFor();
    await screenshot('04-chat-desktop');
    await page.locator('.citation-chip').first().click();
    await page.getByRole('dialog').waitFor();
    assert.ok((await page.getByRole('dialog').innerText()).includes('600'));
    await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).first().click();
    await page.getByRole('button', { name: '纠错', exact: true }).first().click();
    await page.locator('#feedback-correction').fill('一线城市住宿上限为每人每晚600元。');
    await page.getByRole('button', { name: '提交反馈', exact: true }).click();
    await page.getByText(/已提交纠错/).waitFor();

    await page.setViewportSize({ width: 390, height: 844 });
    await screenshot('05-chat-mobile');
    const widths = await page.evaluate(() => ({ width: document.documentElement.clientWidth, scroll: document.documentElement.scrollWidth }));
    assert.ok(widths.scroll <= widths.width + 1, `手机页面出现横向溢出：${JSON.stringify(widths)}`);
    assert.ok(await page.getByRole('button', { name: '发送', exact: true }).isVisible());

    await page.setViewportSize({ width: 1440, height: 1000 });
    for (const route of ['/models', '/traces', '/feedback', '/evals', '/keys', '/system']) {
      await page.goto(runtime.baseUrl + route);
      await page.locator('h1').first().waitFor();
      assert.ok(!(await page.locator('body').innerText()).includes('页面正在实现'), `${route} 仍有占位内容`);
    }

    await page.goto(runtime.baseUrl + '/evals');
    await page.getByRole('button', { name: '新建评测集', exact: true }).first().click();
    await page.locator('#eval-name').fill('浏览器评测验收');
    await page.locator('#eval-app').selectOption(appId);
    await page.getByRole('button', { name: '创建评测集', exact: true }).click();
    await page.waitForURL(/\/evals\/[^/]+$/);
    await page.getByRole('button', { name: '添加用例', exact: true }).first().click();
    await page.locator('#case-question').fill('一线城市住宿标准是多少？');
    await page.locator('#case-expected').fill('600 元');
    await page.getByRole('dialog').getByRole('button', { name: '添加用例', exact: true }).click();
    await page.getByRole('cell', { name: '一线城市住宿标准是多少？', exact: true }).waitFor();
    await page.getByRole('button', { name: '导入 JSON / CSV', exact: true }).click();
    await page.getByRole('dialog').locator('input[type=file]').setInputFiles(path.join(root, 'examples/evaluation.json'));
    await page.getByRole('button', { name: '导入 15 条', exact: true }).click();
    await page.getByRole('tab', { name: /运行记录/ }).click();
    await page.getByRole('button', { name: '新建运行', exact: true }).first().click();
    await page.locator('#run-name').fill('界面两配置对比');
    await page.getByRole('checkbox', { name: /同时运行配置 B/ }).check();
    await page.getByRole('dialog').getByRole('button', { name: /开始运行/ }).click();
    await page.waitForURL(/\/evals\/[^/]+\/runs\/[^/]+$/);
    await page.getByText('已完成', { exact: true }).waitFor({ timeout: 20000 });
    assert.ok((await page.locator('body').innerText()).includes('未标注'));
    await page.getByRole('button', { name: '查看', exact: true }).first().click();
    const review = page.getByRole('dialog').locator('.review-form').first();
    await review.getByRole('button', { name: '是', exact: true }).nth(0).click();
    await review.getByRole('button', { name: '是', exact: true }).nth(1).click();
    await review.getByRole('button', { name: '保存评审', exact: true }).click();
    await page.getByText(/配置 A 的人工评审已保存/).waitFor();
    await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).first().click();
    await page.getByText(/已评审 1 \/ 16/).waitFor();
    await screenshot('06-evaluation');

    await page.goto(runtime.baseUrl + '/system');
    const parentOrgId = await page.getByRole('combobox', { name: '切换组织', exact: true }).inputValue();
    await page.getByRole('button', { name: '新建组织', exact: true }).click();
    await page.locator('#org-name').fill('浏览器子组织');
    await page.locator('#org-parent').selectOption(parentOrgId);
    await page.getByRole('dialog').getByRole('button', { name: '创建组织', exact: true }).click();
    await page.getByText('浏览器子组织', { exact: true }).first().waitFor();
    const admin = apiClient(runtime.baseUrl);
    await admin.request('POST', '/api/auth/login', { email: 'browser@example.test', password: 'BrowserAcceptance2026!' });
    admin.setOrg(parentOrgId);
    const childOrg = (await admin.request('GET', '/api/organizations')).data.items.find(org => org.name === '浏览器子组织');
    assert.ok(childOrg);
    const member = (await admin.request('POST', '/api/users', {
      name: '待加入子组织成员', email: 'child-member@example.test', password: 'BrowserAcceptance2026!', orgId: parentOrgId,
    })).data;
    const childRole = (await admin.request('POST', '/api/roles', {
      name: '子组织知识维护员', orgId: childOrg.id, permissions: ['knowledge:read'],
    })).data;
    await page.reload();
    await page.locator('.org-tree-button').filter({ hasText: '浏览器子组织' }).click();
    await page.getByRole('button', { name: '添加成员', exact: true }).click();
    await page.locator('#member-user').selectOption(member.id);
    await page.locator('#member-role').selectOption(childRole.id);
    await page.getByRole('dialog').getByRole('button', { name: '添加成员', exact: true }).click();
    const childMember = page.locator('.member-row').filter({ hasText: member.email });
    await childMember.getByText('子组织知识维护员', { exact: true }).waitFor();
    assert.equal(await page.getByRole('combobox', { name: '切换组织', exact: true }).inputValue(), parentOrgId,
      '组织树管理目标变化不应依赖切换当前会话组织');
    await screenshot('07-system');
    await childMember.getByRole('button', { name: '移除', exact: true }).click();
    await page.getByRole('dialog').getByRole('button', { name: '移除成员', exact: true }).click();
    await childMember.waitFor({ state: 'detached' });
    const parentMembers = (await admin.request('GET', `/api/organizations/${parentOrgId}/members`)).data.items;
    assert.ok(parentMembers.some(item => item.id === member.id), '从子组织移除不应影响父组织成员关系');
    assert.deepEqual(errors, [], '页面不应出现未捕获错误');
    assert.deepEqual(serverErrors, [], '浏览器操作不应触发服务端错误');
  } catch (error) {
    await screenshot('failure').catch(() => {});
    console.error('浏览器停留地址：', page.url());
    console.error((await page.locator('body').innerText()).slice(-5500));
    throw error;
  }
});

test('浏览器：普通搭建者与显式授权者可管理知识库授权，候选目录不泄露无关组织', { timeout: 90000 }, async t => {
  const runtime = await bootServer();
  t.after(() => runtime.close());
  const admin = apiClient(runtime.baseUrl);
  const call = async (method, route, body) => (await admin.request(method, route, body)).data;
  const password = 'GrantAcceptance2026!';
  const setup = await call('POST', '/api/auth/setup', {
    name: '授权验收管理员', email: 'grant-admin@example.test', password, orgName: '授权验收组织',
  });
  const orgId = setup.organizations[0].id;
  admin.setOrg(orgId);
  const builderRole = await call('POST', '/api/roles', {
    name: '普通知识搭建者', orgId, permissions: ['knowledge:read', 'knowledge:write', 'knowledge:grant'],
  });
  const readerRole = await call('POST', '/api/roles', { name: '知识读者', orgId, permissions: ['knowledge:read'] });
  const builder = await call('POST', '/api/users', {
    name: '普通搭建者', email: 'builder@example.test', password, orgId, roleId: builderRole.id,
  });
  const delegate = await call('POST', '/api/users', {
    name: '显式授权者', email: 'delegate@example.test', password, orgId, roleId: readerRole.id,
  });
  const peer = await call('POST', '/api/users', { name: '同组织读者', email: 'peer@example.test', password, orgId, roleId: readerRole.id });
  const outside = await call('POST', '/api/organizations', { name: '无关组织', parentId: null });
  const outsider = await call('POST', '/api/users', {
    name: '无关组织用户', email: 'outside-private@example.test', password, orgId: outside.id,
  });
  const builderApi = apiClient(runtime.baseUrl);
  await builderApi.request('POST', '/api/auth/login', { email: builder.email, password });
  builderApi.setOrg(orgId);
  const kb = (await builderApi.request('POST', '/api/knowledge-bases', { name: '搭建者私有知识库', visibility: 'private' })).data;
  const chrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const executablePath = process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH || (existsSync(chrome) ? chrome : undefined);
  const browser = await chromium.launch({ headless: true, ...(executablePath ? { executablePath } : {}) });
  t.after(() => browser.close());
  const errors = [];
  async function grantAs(email, targetId, level) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 } });
    const page = await context.newPage();
    page.on('pageerror', error => errors.push(error.message));
    await page.goto(runtime.baseUrl + '/login');
    await page.locator('#login-email').fill(email);
    await page.locator('#login-password').fill(password);
    await page.getByRole('button', { name: '登录', exact: true }).click();
    await page.waitForURL('**/knowledge');
    await page.goto(`${runtime.baseUrl}/knowledge/${kb.id}`);
    await page.getByRole('tab', { name: '权限', exact: true }).click();
    await page.getByRole('button', { name: '添加成员', exact: true }).first().click();
    const candidateValues = await page.locator('#grant-user option').evaluateAll(options => options.map(option => option.value));
    assert.ok(candidateValues.includes(targetId));
    assert.ok(!candidateValues.includes(outsider.id), '知识库授权者不应看到无关组织用户');
    await page.locator('#grant-user').selectOption(targetId);
    await page.locator('#grant-level').selectOption(level);
    await page.getByRole('button', { name: '授予权限', exact: true }).click();
    await page.getByRole('dialog').waitFor({ state: 'hidden' });
    assert.ok(!(await page.locator('body').innerText()).includes('无法读取授权候选人'));
    await context.close();
  }
  await grantAs(builder.email, delegate.id, 'grant');
  await grantAs(delegate.email, peer.id, 'view');
  const grants = (await call('GET', `/api/knowledge-bases/${kb.id}/grants`)).items;
  assert.equal(grants.find(item => item.userId === delegate.id)?.level, 'grant');
  assert.equal(grants.find(item => item.userId === peer.id)?.level, 'view');
  assert.deepEqual(errors, []);
});
