import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

export function nowIso(): string {
  return new Date().toISOString();
}

export function newId(prefix?: string): string {
  const id = crypto.randomUUID();
  return prefix ? `${prefix}_${id}` : id;
}

export function randomToken(bytes = 32): string {
  return crypto.randomBytes(bytes).toString('base64url');
}

export function sha256(value: string): string {
  return crypto.createHash('sha256').update(value, 'utf8').digest('hex');
}

export function timingSafeEqualHex(a: string, b: string): boolean {
  const left = Buffer.from(a, 'hex');
  const right = Buffer.from(b, 'hex');
  return left.length === right.length && crypto.timingSafeEqual(left, right);
}

export function passwordHash(password: string): string {
  const salt = crypto.randomBytes(16);
  const derived = crypto.scryptSync(password, salt, 64, {
    N: 16_384,
    r: 8,
    p: 1,
    maxmem: 64 * 1024 * 1024,
  });
  return `scrypt$16384$8$1$${salt.toString('base64url')}$${derived.toString('base64url')}`;
}

export function verifyPassword(password: string, stored: string | null | undefined): boolean {
  if (!stored) return false;
  const parts = stored.split('$');
  if (parts.length !== 6 || parts[0] !== 'scrypt') return false;
  const [, n, r, p, saltB64, hashB64] = parts;
  try {
    const salt = Buffer.from(saltB64!, 'base64url');
    const expected = Buffer.from(hashB64!, 'base64url');
    const actual = crypto.scryptSync(password, salt, expected.length, {
      N: Number(n),
      r: Number(r),
      p: Number(p),
      maxmem: 64 * 1024 * 1024,
    });
    return expected.length === actual.length && crypto.timingSafeEqual(expected, actual);
  } catch {
    return false;
  }
}

/**
 * AES-256-GCM encryption for model API keys. The persisted key file is created
 * once per data directory so that encrypted values survive restarts.
 */
export class SecretBox {
  private readonly key: Buffer;

  constructor(dataDir: string, configuredKey?: string) {
    fs.mkdirSync(dataDir, { recursive: true });
    if (configuredKey) {
      this.key = crypto.createHash('sha256').update(configuredKey, 'utf8').digest();
      return;
    }
    const keyPath = path.join(dataDir, '.encryption-key');
    if (fs.existsSync(keyPath)) {
      const existing = fs.readFileSync(keyPath);
      if (existing.length !== 32) throw new Error('数据目录中的 .encryption-key 无效');
      this.key = existing;
    } else {
      this.key = crypto.randomBytes(32);
      fs.writeFileSync(keyPath, this.key, { mode: 0o600 });
    }
  }

  encrypt(plaintext: string): string {
    const iv = crypto.randomBytes(12);
    const cipher = crypto.createCipheriv('aes-256-gcm', this.key, iv);
    const ciphertext = Buffer.concat([cipher.update(plaintext, 'utf8'), cipher.final()]);
    const tag = cipher.getAuthTag();
    return `v1.${iv.toString('base64url')}.${tag.toString('base64url')}.${ciphertext.toString('base64url')}`;
  }

  decrypt(value: string | null | undefined): string | undefined {
    if (!value) return undefined;
    const parts = value.split('.');
    if (parts.length !== 4 || parts[0] !== 'v1') {
      throw new Error('无法解密模型密钥：密文格式无效');
    }
    try {
      const iv = Buffer.from(parts[1]!, 'base64url');
      const tag = Buffer.from(parts[2]!, 'base64url');
      const ciphertext = Buffer.from(parts[3]!, 'base64url');
      const decipher = crypto.createDecipheriv('aes-256-gcm', this.key, iv);
      decipher.setAuthTag(tag);
      return Buffer.concat([decipher.update(ciphertext), decipher.final()]).toString('utf8');
    } catch {
      throw new Error('无法解密模型密钥：加密密钥不匹配或密文损坏');
    }
  }
}

export function redactSecrets(text: string, secrets: Array<string | undefined | null>): string {
  let result = text;
  for (const secret of secrets) {
    if (!secret || secret.length < 4) continue;
    result = result.split(secret).join('[REDACTED]');
  }
  return result;
}

export function maskSecret(secret: string | null | undefined): string {
  if (!secret) return '';
  if (secret.length <= 4) return '****';
  return `****${secret.slice(-4)}`;
}

export function sanitizeFileName(name: string): string {
  const base = path.basename(name.replaceAll('\\', '/')).replace(/[\u0000-\u001f]/g, '');
  const cleaned = base.replace(/[^a-zA-Z0-9._\-\u4e00-\u9fff ]+/g, '_').trim();
  return (cleaned || 'document.txt').slice(0, 160);
}

export function safeJsonParse<T>(value: string | null | undefined, fallback: T): T {
  if (!value) return fallback;
  try {
    return JSON.parse(value) as T;
  } catch {
    return fallback;
  }
}

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}
