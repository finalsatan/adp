import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('..', import.meta.url));
process.chdir(root);
if (existsSync(path.join(root, '.env'))) process.loadEnvFile(path.join(root, '.env'));
process.env.DATA_DIR = path.resolve(root, process.env.DATA_DIR || 'data');
const python = path.join(root, 'apps/parser/.venv/bin/python');
if (!existsSync(python)) throw new Error('请先运行 npm run setup，安装解析服务依赖。');
const children = [];
let stopping = false;
function terminate(child, signal) {
  try {
    if (process.platform !== 'win32' && child.pid) process.kill(-child.pid, signal);
    else child.kill(signal);
  } catch (error) {
    if (error.code !== 'ESRCH') console.error(error.message);
  }
}
function stop(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) terminate(child, 'SIGTERM');
  const force = setTimeout(() => {
    for (const child of children) terminate(child, 'SIGKILL');
    process.exit(code);
  }, 3000);
  force.unref();
  process.exitCode = code;
}
function launch(command, args) {
  const child = spawn(command, args, { cwd: root, env: process.env, stdio: 'inherit', detached: process.platform !== 'win32' });
  children.push(child);
  child.on('error', (error) => { console.error(error.message); stop(1); });
  child.on('exit', (code) => { if (!stopping) stop(code || 1); });
}
process.on('SIGINT', () => stop());
process.on('SIGTERM', () => stop());
launch(python, ['-m', 'uvicorn', 'app:app', '--app-dir', 'apps/parser', '--host', '127.0.0.1', '--port', '8001']);
launch('npm', ['run', 'dev', '--workspace', 'apps/server']);
launch('npm', ['run', 'dev', '--workspace', 'apps/web']);
console.log('\nADP 开发环境：http://localhost:5173\nAPI：http://127.0.0.1:3001\n解析服务：http://127.0.0.1:8001\n');
