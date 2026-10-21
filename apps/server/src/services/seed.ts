import {
  all,
  get,
  run,
  transaction,
  type Database,
  type ModelRow,
  type OrganizationRow,
  type RoleRow,
  type UserRow,
} from '../db/index.js';
import { ALL_PERMISSIONS, TERMINAL_PERMISSIONS } from '../lib/permissions.js';
import { newId, nowIso, passwordHash } from '../lib/util.js';

const DEMO_MODELS = [
  {
    name: '本地演示 · 对话（摘录回答）',
    kind: 'chat',
    model: 'demo-chat-extractive',
  },
  {
    name: '本地演示 · Embedding（词面哈希向量）',
    kind: 'embedding',
    model: 'demo-embedding-lexical',
  },
] as const;

export function seedDemoModels(db: Database): void {
  for (const demo of DEMO_MODELS) {
    const existing = get<ModelRow>(
      db,
      "SELECT * FROM models WHERE provider = 'demo' AND scope = 'platform' AND kind = ?",
      demo.kind,
    );
    if (existing) continue;
    run(
      db,
      `INSERT INTO models (id, name, kind, provider, base_url, model, scope, org_id, enabled, api_key_enc, dimensions, created_at, created_by)
       VALUES (?, ?, ?, 'demo', '', ?, 'platform', NULL, 1, NULL, ?, ?, NULL)`,
      newId(),
      demo.name,
      demo.kind,
      demo.model,
      demo.kind === 'embedding' ? 256 : null,
      nowIso(),
    );
  }
}

export function seedGlobalRoles(db: Database): void {
  if (get<RoleRow>(db, "SELECT * FROM roles WHERE is_global = 1 AND name = '平台终端用户'")) return;
  run(
    db,
    'INSERT INTO roles (id, name, permissions, is_global, org_id, created_at) VALUES (?, ?, ?, 1, NULL, ?)',
    newId(),
    '平台终端用户',
    JSON.stringify(TERMINAL_PERMISSIONS),
    nowIso(),
  );
  run(
    db,
    'INSERT INTO roles (id, name, permissions, is_global, org_id, created_at) VALUES (?, ?, ?, 1, NULL, ?)',
    newId(),
    '平台搭建者',
    JSON.stringify(ALL_PERMISSIONS.filter((permission) => permission !== 'system:manage')),
    nowIso(),
  );
}

export function createOrganization(
  db: Database,
  input: { name: string; parentId?: string | null; creatorId: string; addCreatorAsAdmin?: boolean },
): { organization: OrganizationRow; adminRole: RoleRow; memberRole: RoleRow } {
  return transaction(db, () => {
    const orgId = newId();
    const createdAt = nowIso();
    run(
      db,
      'INSERT INTO organizations (id, name, parent_id, created_at) VALUES (?, ?, ?, ?)',
      orgId,
      input.name,
      input.parentId ?? null,
      createdAt,
    );
    const adminRole: RoleRow = {
      id: newId(),
      name: '组织管理员',
      permissions: JSON.stringify(ALL_PERMISSIONS),
      is_global: 0,
      org_id: orgId,
      created_at: createdAt,
    };
    const memberRole: RoleRow = {
      id: newId(),
      name: '终端用户',
      permissions: JSON.stringify(TERMINAL_PERMISSIONS),
      is_global: 0,
      org_id: orgId,
      created_at: createdAt,
    };
    for (const role of [adminRole, memberRole]) {
      run(
        db,
        'INSERT INTO roles (id, name, permissions, is_global, org_id, created_at) VALUES (?, ?, ?, ?, ?, ?)',
        role.id,
        role.name,
        role.permissions,
        role.is_global,
        role.org_id,
        role.created_at,
      );
    }
    if (input.addCreatorAsAdmin !== false) {
      run(
        db,
        'INSERT INTO memberships (id, org_id, user_id, role_id, created_at) VALUES (?, ?, ?, ?, ?)',
        newId(),
        orgId,
        input.creatorId,
        adminRole.id,
        createdAt,
      );
    }
    const organization = get<OrganizationRow>(db, 'SELECT * FROM organizations WHERE id = ?', orgId)!;
    return { organization, adminRole, memberRole };
  });
}

export function createUser(
  db: Database,
  input: {
    name: string;
    email: string;
    password: string;
    isSystemAdmin?: boolean;
  },
): UserRow {
  const id = newId();
  const createdAt = nowIso();
  run(
    db,
    `INSERT INTO users (id, name, email, password_hash, is_system_admin, oidc_subject, created_at)
     VALUES (?, ?, ?, ?, ?, NULL, ?)`,
    id,
    input.name,
    input.email.trim().toLowerCase(),
    input.password ? passwordHash(input.password) : null,
    input.isSystemAdmin ? 1 : 0,
    createdAt,
  );
  return get<UserRow>(db, 'SELECT * FROM users WHERE id = ?', id)!;
}

export function ensureMemberRole(db: Database, orgId: string): RoleRow {
  const existing = get<RoleRow>(
    db,
    "SELECT * FROM roles WHERE org_id = ? AND is_global = 0 AND name = '终端用户'",
    orgId,
  );
  if (existing) return existing;
  const role: RoleRow = {
    id: newId(),
    name: '终端用户',
    permissions: JSON.stringify(TERMINAL_PERMISSIONS),
    is_global: 0,
    org_id: orgId,
    created_at: nowIso(),
  };
  run(
    db,
    'INSERT INTO roles (id, name, permissions, is_global, org_id, created_at) VALUES (?, ?, ?, ?, ?, ?)',
    role.id,
    role.name,
    role.permissions,
    role.is_global,
    role.org_id,
    role.created_at,
  );
  return role;
}

export function findDefaultEmbeddingModel(db: Database, orgId: string | null): ModelRow | undefined {
  return (
    get<ModelRow>(
      db,
      "SELECT * FROM models WHERE kind = 'embedding' AND enabled = 1 AND scope = 'organization' AND org_id = ? ORDER BY created_at LIMIT 1",
      orgId,
    ) ??
    get<ModelRow>(
      db,
      "SELECT * FROM models WHERE kind = 'embedding' AND enabled = 1 AND scope = 'platform' ORDER BY created_at LIMIT 1",
    )
  );
}

export function initializeFirstUser(
  db: Database,
  input: { name: string; email: string; password: string; orgName: string },
): { user: UserRow; organization: OrganizationRow } {
  return transaction(db, () => {
    if ((get<{ n: number }>(db, 'SELECT COUNT(*) AS n FROM users')?.n ?? 0) > 0) {
      throw new Error('ALREADY_INITIALIZED');
    }
    seedDemoModels(db);
    seedGlobalRoles(db);
    const user = createUser(db, {
      name: input.name,
      email: input.email,
      password: input.password,
      isSystemAdmin: true,
    });
    const { organization } = createOrganization(db, {
      name: input.orgName,
      creatorId: user.id,
      addCreatorAsAdmin: true,
    });
    return { user, organization };
  });
}

export function listAllRoles(db: Database, orgId: string | null): RoleRow[] {
  if (orgId) {
    return all<RoleRow>(
      db,
      'SELECT * FROM roles WHERE is_global = 1 OR org_id = ? ORDER BY is_global DESC, created_at ASC',
      orgId,
    );
  }
  return all<RoleRow>(db, 'SELECT * FROM roles ORDER BY is_global DESC, created_at ASC');
}
