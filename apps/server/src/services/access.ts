import {
  all,
  get,
  type AppRow,
  type Database,
  type KnowledgeBaseRow,
  type ModelRow,
  type OrganizationRow,
  type RoleRow,
  type UserRow,
} from '../db/index.js';
import { forbidden, unauthorized } from '../lib/errors.js';
import { ALL_PERMISSIONS, hasPermission, parsePermissions, type Permission } from '../lib/permissions.js';

export interface AuthUser {
  id: string;
  name: string;
  email: string;
  isSystemAdmin: boolean;
}

export interface OrgAccess {
  id: string;
  name: string;
  parentId: string | null;
  role: string;
  permissions: string[];
}

interface BaseCaller {
  user: AuthUser;
  userRow: UserRow;
  orgId: string | null;
  organizations: OrgAccess[];
  permissions: Set<string>;
}

export interface SessionCaller extends BaseCaller {
  kind: 'session';
  sessionHash: string;
}

export interface AppKeyCaller extends BaseCaller {
  kind: 'appkey';
  appId: string;
  keyId: string;
}

export type Caller = SessionCaller | AppKeyCaller;

export function toAuthUser(row: UserRow): AuthUser {
  return {
    id: row.id,
    name: row.name,
    email: row.email,
    isSystemAdmin: row.is_system_admin === 1,
  };
}

export function listUserOrganizations(db: Database, user: UserRow): OrgAccess[] {
  if (user.is_system_admin === 1) {
    return all<OrganizationRow>(db, 'SELECT * FROM organizations ORDER BY created_at ASC').map((org) => ({
      id: org.id,
      name: org.name,
      parentId: org.parent_id,
      role: '系统管理员',
      permissions: [...ALL_PERMISSIONS],
    }));
  }
  return all<OrganizationRow & { role_name: string; permissions: string; role_id: string }>(
    db,
    `SELECT o.*, r.name AS role_name, r.permissions, r.id AS role_id
     FROM memberships m
     JOIN organizations o ON o.id = m.org_id
     JOIN roles r ON r.id = m.role_id
     WHERE m.user_id = ?
     ORDER BY o.created_at ASC`,
    user.id,
  ).map((row) => ({
    id: row.id,
    name: row.name,
    parentId: row.parent_id,
    role: row.role_name,
    permissions: parsePermissions(row.permissions),
  }));
}

export function resolveCurrentOrg(
  organizations: OrgAccess[],
  preferredOrgId: string | null | undefined,
  requestedOrgId: string | undefined,
): OrgAccess | undefined {
  if (requestedOrgId) {
    return organizations.find((org) => org.id === requestedOrgId);
  }
  if (preferredOrgId) {
    const preferred = organizations.find((org) => org.id === preferredOrgId);
    if (preferred) return preferred;
  }
  return organizations[0];
}

export function buildCaller(
  userRow: UserRow,
  organizations: OrgAccess[],
  org: OrgAccess | undefined,
  sessionHash: string,
): SessionCaller {
  const permissions = new Set<string>(userRow.is_system_admin === 1 ? [...ALL_PERMISSIONS] : org?.permissions ?? []);
  return {
    kind: 'session',
    user: toAuthUser(userRow),
    userRow,
    orgId: org?.id ?? null,
    organizations,
    permissions,
    sessionHash,
  };
}

export function requireSession(caller: Caller | undefined): SessionCaller {
  if (!caller) throw unauthorized();
  if (caller.kind !== 'session') throw forbidden('该接口不接受应用 API Key');
  return caller;
}

export function requireAuth(caller: Caller | undefined): Caller {
  if (!caller) throw unauthorized();
  return caller;
}

export function requirePermission(caller: Caller, permission: Permission): void {
  if (caller.user.isSystemAdmin) return;
  if (!hasPermission([...caller.permissions], permission)) {
    throw forbidden(`缺少权限 ${permission}`);
  }
}

export function requireCurrentOrg(caller: Caller): string {
  if (!caller.orgId) throw forbidden('当前没有可用的组织');
  return caller.orgId;
}

export function requireSystemAdmin(caller: Caller): void {
  if (!caller.user.isSystemAdmin) throw forbidden('该操作仅限系统管理员');
}

export function requireSessionPermission(caller: Caller | undefined, permission: Permission): SessionCaller {
  const session = requireSession(caller);
  requirePermission(session, permission);
  return session;
}

/** Permissions come from the role bound to the target organization, never the currently selected org. */
export function permissionsForOrg(caller: Caller, orgId: string): string[] {
  if (caller.user.isSystemAdmin) return [...ALL_PERMISSIONS];
  return caller.organizations.find((org) => org.id === orgId)?.permissions ?? [];
}

export function isMemberOf(caller: Caller, orgId: string): boolean {
  return caller.user.isSystemAdmin || caller.organizations.some((org) => org.id === orgId);
}

export interface KnowledgeBaseAccess {
  view: boolean;
  edit: boolean;
  grant: boolean;
}

export function knowledgeBaseAccess(
  db: Database,
  caller: Caller,
  kb: KnowledgeBaseRow,
): KnowledgeBaseAccess {
  if (caller.user.isSystemAdmin) return { view: true, edit: true, grant: true };

  const targetPermissions = permissionsForOrg(caller, kb.org_id);
  const targetRead = targetPermissions.includes('knowledge:read');
  const targetManage = targetPermissions.includes('system:manage');
  const currentRead = [...caller.permissions].includes('knowledge:read');
  const isOwner = kb.owner_id === caller.user.id;
  const member = isMemberOf(caller, kb.org_id);

  const grantRow = get<{ level: 'view' | 'edit' | 'grant' }>(
    db,
    'SELECT level FROM kb_grants WHERE kb_id = ? AND user_id = ?',
    kb.id,
    caller.user.id,
  );
  const explicitView = Boolean(grantRow);
  const explicitEdit = grantRow?.level === 'edit' || grantRow?.level === 'grant';
  const explicitGrant = grantRow?.level === 'grant';

  const orgVisible = kb.visibility === 'organization' && member && targetRead;
  const platformVisible = kb.visibility === 'platform' && caller.kind === 'session' && currentRead;
  const view = isOwner || explicitView || orgVisible || platformVisible;

  // Organization visibility grants viewing. Editing someone else's KB requires
  // an explicit edit/grant share or an organization-management role.
  const edit = isOwner || explicitEdit || (orgVisible && targetManage);
  const grant = isOwner || explicitGrant || (member && targetManage);
  return { view, edit, grant };
}

export function assertKbView(db: Database, caller: Caller, kb: KnowledgeBaseRow): KnowledgeBaseAccess {
  const access = knowledgeBaseAccess(db, caller, kb);
  if (!access.view) throw forbidden('无权访问该知识库');
  return access;
}

export function assertKbEdit(db: Database, caller: Caller, kb: KnowledgeBaseRow): KnowledgeBaseAccess {
  const access = knowledgeBaseAccess(db, caller, kb);
  if (!access.edit) throw forbidden('无权编辑该知识库');
  return access;
}

export interface AppAccess {
  view: boolean;
  edit: boolean;
  publish: boolean;
}

export function appAccess(db: Database, caller: Caller, app: AppRow): AppAccess {
  if (caller.user.isSystemAdmin) return { view: true, edit: true, publish: true };

  const targetPermissions = permissionsForOrg(caller, app.org_id);
  const targetRead = targetPermissions.includes('apps:read');
  const targetWrite = targetPermissions.includes('apps:write');
  const targetPublish = targetPermissions.includes('apps:publish');
  const targetManage = targetPermissions.includes('system:manage');
  const currentRead = [...caller.permissions].includes('apps:read');
  const isOwner = app.owner_id === caller.user.id;
  const member = isMemberOf(caller, app.org_id);

  const orgVisible = app.visibility === 'organization' && member && targetRead;
  const platformVisible = app.visibility === 'platform' && caller.kind === 'session' && currentRead;
  const view = isOwner || orgVisible || platformVisible;

  // Same as KB: organization visibility is view-only. Ownership or an
  // organization-management role is required to change another user's app.
  const edit = targetWrite && (isOwner || (orgVisible && targetManage));
  const publish = edit && targetPublish;
  return { view, edit, publish };
}

/** Visibility for the terminal (published) surface; app keys are handled by their binding. */
export function publishedAppVisible(db: Database, caller: Caller, app: AppRow): boolean {
  if (caller.kind === 'appkey') return caller.appId === app.id;
  if (caller.user.isSystemAdmin) return true;
  if (app.owner_id === caller.user.id) return true;
  if (app.visibility === 'platform') return true;
  if (app.visibility === 'organization') {
    return caller.organizations.some((org) => org.id === app.org_id);
  }
  return false;
}

export function canManageModel(caller: Caller, model: ModelRow): boolean {
  if (caller.user.isSystemAdmin) return true;
  if (!hasPermission([...caller.permissions], 'models:manage')) return false;
  if (model.scope === 'platform') return false;
  return model.org_id === caller.orgId && caller.organizations.some((org) => org.id === model.org_id);
}

export function canUseModel(caller: Caller, model: ModelRow): boolean {
  if (model.enabled !== 1) return false;
  if (model.scope === 'platform') return true;
  if (!model.org_id) return false;
  return caller.organizations.some((org) => org.id === model.org_id);
}

export function roleBelongsToOrg(role: RoleRow, orgId: string): boolean {
  return role.is_global === 1 || role.org_id === orgId;
}

export function isOrgReviewer(caller: Caller, orgId: string): boolean {
  if (caller.user.isSystemAdmin) return true;
  const org = caller.organizations.find((item) => item.id === orgId);
  return Boolean(org?.permissions.includes('system:manage'));
}

export function auditOrgScope(caller: Caller): string | null {
  return caller.orgId;
}
