import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import net from 'node:net';
import { randomBytes } from 'node:crypto';

export const root = fileURLToPath(new URL('../..', import.meta.url));
export const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

export async function freePort() {
  const server = net.createServer();
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  return port;
}

export async function eventually(operation, predicate, timeoutMs = 20000) {
  const deadline = Date.now() + timeoutMs;
  let latest;
  do {
    latest = await operation();
    if (predicate(latest)) return latest;
    await delay(100);
  } while (Date.now() < deadline);
  throw new Error(`等待状态超时：${JSON.stringify(latest)}`);
}

export async function bootServer(extraEnv = {}) {
  const dataDir = await mkdtemp(path.join(tmpdir(), 'adp-system-test-'));
  const port = await freePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  let output = '';
  const env = {
    ...process.env,
    NODE_ENV: 'test', HOST: '127.0.0.1', PORT: String(port), DATA_DIR: dataDir,
    COOKIE_SECURE: 'false', APP_MASTER_KEY: randomBytes(32).toString('hex'),
    ...extraEnv,
  };
  let child;
  async function start() {
    child = spawn(process.execPath, ['apps/server/dist/index.js'], {
      cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'],
    });
    child.stdout.on('data', data => { output += data.toString(); });
    child.stderr.on('data', data => { output += data.toString(); });
    await eventually(async () => {
      if (child.exitCode !== null) throw new Error(`API 启动失败：${output}`);
      try { return (await fetch(`${baseUrl}/health`)).ok; } catch { return false; }
    }, ready => ready);
  }
  async function stop() {
    if (!child || child.exitCode !== null) return;
    const exited = new Promise(resolve => child.once('exit', resolve));
    child.kill('SIGTERM');
    const force = setTimeout(() => child.kill('SIGKILL'), 4000);
    await exited;
    clearTimeout(force);
  }
  try { await start(); } catch (error) { await stop(); await rm(dataDir, { recursive: true, force: true }); throw error; }
  return {
    baseUrl, dataDir, env,
    get output() { return output; },
    restart: async () => { await stop(); await start(); },
    close: async () => { await stop(); await rm(dataDir, { recursive: true, force: true }); },
  };
}

export async function bootParser() {
  const port = await freePort();
  const baseUrl = `http://127.0.0.1:${port}`;
  const token = randomBytes(24).toString('hex');
  let output = '';
  const child = spawn(path.join(root, 'apps/parser/.venv/bin/python'), [
    '-m', 'uvicorn', 'app:app', '--app-dir', 'apps/parser', '--host', '127.0.0.1', '--port', String(port),
  ], { cwd: root, env: { ...process.env, PARSER_TOKEN: token }, stdio: ['ignore', 'pipe', 'pipe'] });
  child.stdout.on('data', data => { output += data.toString(); });
  child.stderr.on('data', data => { output += data.toString(); });
  const close = async () => {
    if (child.exitCode !== null) return;
    const exited = new Promise(resolve => child.once('exit', resolve));
    child.kill('SIGTERM');
    const force = setTimeout(() => child.kill('SIGKILL'), 4000);
    await exited;
    clearTimeout(force);
  };
  try {
    await eventually(async () => {
      if (child.exitCode !== null) throw new Error(`解析服务启动失败：${output}`);
      try { return (await fetch(`${baseUrl}/health`)).ok; } catch { return false; }
    }, ready => ready);
  } catch (error) { await close(); throw error; }
  return { baseUrl, token, close };
}

export function apiClient(baseUrl) {
  let cookie = '';
  let orgId = '';
  return {
    setOrg(value) { orgId = value; },
    getCookie() { return cookie; },
    async request(method, route, body, options = {}) {
      const headers = new Headers(options.headers);
      if (cookie && !options.anonymous) headers.set('cookie', cookie);
      if (orgId) headers.set('X-Org-Id', orgId);
      if (body !== undefined && !(body instanceof FormData)) headers.set('content-type', 'application/json');
      const response = await fetch(`${baseUrl}${route}`, {
        method, headers,
        body: body === undefined ? undefined : body instanceof FormData ? body : JSON.stringify(body),
      });
      const cookies = response.headers.getSetCookie();
      if (cookies.length) cookie = cookies.map(value => value.split(';')[0]).join('; ');
      const text = await response.text();
      let data;
      try { data = text ? JSON.parse(text) : undefined; } catch { data = text; }
      if (options.status !== undefined && response.status !== options.status) {
        throw new Error(`${method} ${route}: 期望 ${options.status}，实际 ${response.status}，${text.slice(0, 1200)}`);
      }
      if (options.status === undefined && !response.ok) {
        throw new Error(`${method} ${route}: ${response.status} ${text.slice(0, 1200)}`);
      }
      return { status: response.status, data, response };
    },
  };
}
