import { randomBytes } from 'node:crypto';
import { existsSync, readFileSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('..', import.meta.url));
process.chdir(root);
if (Number(process.versions.node.split('.')[0]) !== 24) {
  throw new Error('ADP 需要 Node.js 24，请切换版本后重试。');
}
const envPath = path.join(root, '.env');
if (!existsSync(envPath)) {
  writeFileSync(envPath, readFileSync('.env.example', 'utf8')
    .replace('APP_MASTER_KEY=', `APP_MASTER_KEY=${randomBytes(32).toString('hex')}`)
    .replace('PARSER_TOKEN=', `PARSER_TOKEN=${randomBytes(24).toString('hex')}`), { mode: 0o600 });
  console.log('已创建 .env；请妥善保留其中的加密密钥。');
}
async function run(command, args, options = {}) {
  const child = spawn(command, args, { cwd: root, stdio: 'inherit', ...options });
  await new Promise((resolve, reject) => {
    child.on('error', reject);
    child.on('exit', (code) => code === 0 ? resolve() : reject(new Error(`${command} 退出码 ${code}`)));
  });
}
const python = path.join(root, 'apps/parser/.venv/bin/python');
if (!existsSync('apps/parser/requirements.txt')) throw new Error('缺少解析服务 requirements.txt。');
const requirements = existsSync('apps/parser/requirements-dev.txt') ? 'apps/parser/requirements-dev.txt' : 'apps/parser/requirements.txt';
const env = { ...process.env, UV_CACHE_DIR: process.env.UV_CACHE_DIR || '/tmp/adp-uv-cache' };
try {
  if (!existsSync(python)) await run('uv', ['venv', 'apps/parser/.venv', '--python', '3.12'], { env });
  await run('uv', ['pip', 'install', '--python', python, '-r', requirements], { env });
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
  if (!existsSync(python)) await run('python3', ['-m', 'venv', 'apps/parser/.venv']);
  await run(python, ['-m', 'pip', 'install', '-r', requirements]);
}
console.log('环境准备完成。运行 npm run dev，在浏览器首次创建管理员。');
