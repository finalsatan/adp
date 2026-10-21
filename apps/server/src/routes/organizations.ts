import type { FastifyInstance } from 'fastify';
import { z } from 'zod';
import {
  all,
  get,
  run,
  transaction,
  type OrganizationRow,
  type RoleRow,
  type UserRow,
} from '../db/index.js';
import type { ServerServices } from '../context.js';
import { conflict, forbidden, notFound } from '../lib/errors.js';
import { pageOptions, parseBody, parseParams, requireCaller, requireSessionCaller } from '../lib/http.js';
import { ALL_PERMISSIONS, isPermission, parsePermissions } from '../lib/permissions.js';
import {
  idParams,
  memberAddSchema,
  orgCreateSchema,
  roleCreateSchema,
  userCreateSchema,
} from '../lib/schemas.js';
import { newId, nowIso, passwordHash } from '../lib/util.js';
import { permissionsForOrg } from '../services/access.js';
import { writeAudit, serializeAudit } from '../services/audit.js';
import { createOrganization, createUser, ensureMemberRole, listAllRoles } from '../services/seed.js';
import { membershipsForUser, serializeRole, serializeUser } from '../services/serializers.js';

function requireManageOrg(caller: ReturnType<typeof requireCaller>, orgId: string): void {
  if (caller.user.isSystemAdmin) return;
  if (!permissionsForOrg(caller, orgId).includes('system:manage')) {
    throw forbidden('需要目标组织的管理权限');
  }
}

function requireManageCurrent(caller: ReturnType<typeof requireCaller>): string {
  const orgId = caller.orgId;
  if (!orgId) throw forbidden('当前没有可用的组织');
  requireManageOrg(caller, orgId);
  return orgId;
}

export async function registerOrganizationRoutes(app: FastifyInstance, services: ServerServices): Promise<void> {
  const { db } = services;

  app.get('/api/organizations', async (request) => {
    const caller = requireSessionCaller(request);
    return { items: caller.organizations };
  });

  app.post('/api/organizations', async (request) => {
    const caller = requireSessionCaller(request);
    const body = parseBody(request, orgCreateSchema);
    const parentId = body.parentId === undefined ? caller.orgId : body.parentId;
    if (parentId) {
      const parent = get<OrganizationRow>(db, 'SELECT * FROM organizations WHERE id = ?', parentId);
      if (!parent) throw notFound('父组织');
      requireManageOrg(caller, parentId);
    } else if (!caller.user.isSystemAdmin) {
      throw forbidden('普通用户必须指定可管理的父组织');
    }
    const created = createOrganization(db, {
      name: body.name,
      parentId: parentId ?? null,
      creatorId: caller.user.id,
      addCreatorAsAdmin: true,
    });
    writeAudit(db, {
      orgId: created.organization.id,
      actor: caller.user,
      action: 'organization.create',
      resourceType: 'organization',
      resourceId: created.organization.id,
      detail: { name: body.name, parentId },
    });
    return {
      id: created.organization.id,
      name: created.organization.name,
      parentId: created.organization.parent_id,
      role: '组织管理员',
      permissions: [...ALL_PERMISSIONS],
      createdAt: created.organization.created_at,
    };
  });

  app.get('/api/users', async (request) => {
    const caller = requireSessionCaller(request);
    const orgId = requireManageCurrent(caller);
    const rows = caller.user.isSystemAdmin
      ? all<UserRow>(db, 'SELECT * FROM users ORDER BY created_at ASC')
      : all<UserRow>(
          db,
          `SELECT DISTINCT u.* FROM users u JOIN memberships m ON m.user_id = u.id
           WHERE m.org_id = ? ORDER BY u.created_at ASC`,
          orgId,
        );
    const managedOrgIds = new Set(
      caller.organizations.filter((org) => org.permissions.includes('system:manage')).map((org) => org.id),
    );
    return {
      items: rows.map((row) => {
        const memberships = membershipsForUser(db, row.id);
        const visibleMemberships = caller.user.isSystemAdmin
          ? memberships
          : memberships.filter((membership) => managedOrgIds.has(membership.orgId));
        return serializeUser(row, visibleMemberships);
      }),
    };
  });

  app.post('/api/users', async (request) => {
    const caller = requireSessionCaller(request);
    const body = parseBody(request, userCreateSchema);
    requireManageOrg(caller, body.orgId);
    const org = get<OrganizationRow>(db, 'SELECT * FROM organizations WHERE id = ?', body.orgId);
    if (!org) throw notFound('组织');
    if (body.isSystemAdmin && !caller.user.isSystemAdmin) {
      throw forbidden('只有系统管理员可以创建系统管理员');
    }
    const existing = get<UserRow>(db, 'SELECT * FROM users WHERE email = ?', body.email.toLowerCase());
    if (existing) throw conflict('该邮箱已存在');
    let role: RoleRow | undefined;
    if (body.roleId) {
      role = get<RoleRow>(db, 'SELECT * FROM roles WHERE id = ?', body.roleId);
      if (!role) throw notFound('角色');
      if (role.is_global !== 1 && role.org_id !== body.orgId) {
        throw forbidden('角色不属于目标组织');
      }
    } else {
      role = ensureMemberRole(db, body.orgId);
    }
    const created = transaction(db, () => {
      const user = createUser(db, {
        name: body.name,
        email: body.email,
        password: body.password,
        isSystemAdmin: body.isSystemAdmin ?? false,
      });
      run(
        db,
        'INSERT INTO memberships (id, org_id, user_id, role_id, created_at) VALUES (?, ?, ?, ?, ?)',
        newId(),
        body.orgId,
        user.id,
        role!.id,
        nowIso(),
      );
      return user;
    });
    writeAudit(db, {
      orgId: body.orgId,
      actor: caller.user,
      action: 'user.create',
      resourceType: 'user',
      resourceId: created.id,
      detail: { email: body.email, roleId: role!.id, isSystemAdmin: body.isSystemAdmin ?? false },
    });
    return serializeUser(created, membershipsForUser(db, created.id));
  });

  app.get('/api/roles', async (request) => {
    const caller = requireSessionCaller(request);
    const orgId = requireManageCurrent(caller);
    const roles = listAllRoles(db, orgId);
    return { items: roles.map(serializeRole) };
  });

  app.post('/api/roles', async (request) => {
    const caller = requireSessionCaller(request);
    const body = parseBody(request, roleCreateSchema);
    if (body.global && !caller.user.isSystemAdmin) {
      throw forbidden('只有系统管理员可以创建全局角色');
    }
    const orgId = body.global ? null : body.orgId ?? caller.orgId;
    if (!orgId && !body.global) throw forbidden('当前没有可用的组织');
    if (orgId) requireManageOrg(caller, orgId);
    const role: RoleRow = {
      id: newId(),
      name: body.name,
      permissions: JSON.stringify(body.permissions.filter(isPermission)),
      is_global: body.global ? 1 : 0,
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
    writeAudit(db, {
      orgId,
      actor: caller.user,
      action: 'role.create',
      resourceType: 'role',
      resourceId: role.id,
      detail: { name: role.name, permissions: body.permissions, global: body.global },
    });
    return serializeRole(role);
  });

  app.get('/api/organizations/:id/members', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    requireManageOrg(caller, id);
    const org = get<OrganizationRow>(db, 'SELECT * FROM organizations WHERE id = ?', id);
    if (!org) throw notFound('组织');
    const rows = all<{
      id: string;
      name: string;
      email: string;
      role_id: string;
      role_name: string;
    }>(
      db,
      `SELECT u.id, u.name, u.email, m.role_id, r.name AS role_name
       FROM memberships m
       JOIN users u ON u.id = m.user_id
       JOIN roles r ON r.id = m.role_id
       WHERE m.org_id = ?
       ORDER BY u.name ASC, u.created_at ASC`,
      id,
    );
    return {
      items: rows.map((row) => ({
        id: row.id,
        name: row.name,
        email: row.email,
        roleId: row.role_id,
        roleName: row.role_name,
      })),
    };
  });

  app.get('/api/organizations/:id/roles', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    requireManageOrg(caller, id);
    const org = get<OrganizationRow>(db, 'SELECT * FROM organizations WHERE id = ?', id);
    if (!org) throw notFound('组织');
    return { items: listAllRoles(db, id).map(serializeRole) };
  });

  app.get('/api/organizations/:id/member-candidates', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    requireManageOrg(caller, id);
    const org = get<OrganizationRow>(db, 'SELECT * FROM organizations WHERE id = ?', id);
    if (!org) throw notFound('组织');
    if (caller.user.isSystemAdmin) {
      const rows = all<UserRow>(db, 'SELECT * FROM users ORDER BY name ASC, created_at ASC');
      return { items: rows.map((row) => ({ id: row.id, name: row.name, email: row.email })) };
    }
    const managedOrgIds = caller.organizations
      .filter((item) => item.permissions.includes('system:manage'))
      .map((item) => item.id);
    if (managedOrgIds.length === 0) {
      return { items: [] };
    }
    const placeholders = managedOrgIds.map(() => '?').join(',');
    const rows = all<UserRow>(
      db,
      `SELECT DISTINCT u.*
       FROM users u
       JOIN memberships m ON m.user_id = u.id
       WHERE m.org_id IN (${placeholders})
       ORDER BY u.name ASC, u.created_at ASC`,
      ...managedOrgIds,
    );
    return { items: rows.map((row) => ({ id: row.id, name: row.name, email: row.email })) };
  });

  app.post('/api/organizations/:id/members', async (request) => {
    const caller = requireSessionCaller(request);
    const { id } = parseParams(request, idParams);
    requireManageOrg(caller, id);
    const org = get<OrganizationRow>(db, 'SELECT * FROM organizations WHERE id = ?', id);
    if (!org) throw notFound('组织');
    const body = parseBody(request, memberAddSchema);
    const user = get<UserRow>(db, 'SELECT * FROM users WHERE id = ?', body.userId);
    if (!user) throw notFound('用户');
    const role = get<RoleRow>(db, 'SELECT * FROM roles WHERE id = ?', body.roleId);
    if (!role) throw notFound('角色');
    if (role.is_global !== 1 && role.org_id !== id) throw forbidden('角色不属于目标组织');
    const existing = get<{ id: string }>(
      db,
      'SELECT id FROM memberships WHERE org_id = ? AND user_id = ?',
      id,
      body.userId,
    );
    if (existing) throw conflict('该用户已经是组织成员');
    run(
      db,
      'INSERT INTO memberships (id, org_id, user_id, role_id, created_at) VALUES (?, ?, ?, ?, ?)',
      newId(),
      id,
      body.userId,
      body.roleId,
      nowIso(),
    );
    writeAudit(db, {
      orgId: id,
      actor: caller.user,
      action: 'organization.member.add',
      resourceType: 'membership',
      resourceId: body.userId,
      detail: { roleId: body.roleId },
    });
    return { ok: true, userId: body.userId, orgId: id, roleId: body.roleId };
  });

  app.delete('/api/organizations/:id/members/:userId', async (request) => {
    const caller = requireSessionCaller(request);
    const params = parseParams(request, z.object({ id: z.string().min(1), userId: z.string().min(1) }));
    requireManageOrg(caller, params.id);
    const membership = get<{ role_id: string }>(
      db,
      'SELECT role_id FROM memberships WHERE org_id = ? AND user_id = ?',
      params.id,
      params.userId,
    );
    if (!membership) throw notFound('组织成员');
    const role = get<RoleRow>(db, 'SELECT * FROM roles WHERE id = ?', membership.role_id);
    const permissions = parsePermissions(role?.permissions);
    if (permissions.includes('system:manage')) {
      const admins = all<{ role_id: string }>(db, 'SELECT role_id FROM memberships WHERE org_id = ?', params.id).filter(
        (item) => parsePermissions(get<RoleRow>(db, 'SELECT * FROM roles WHERE id = ?', item.role_id)?.permissions).includes('system:manage'),
      );
      if (admins.length <= 1) throw conflict('不能移除组织最后一名管理员');
    }
    run(db, 'DELETE FROM memberships WHERE org_id = ? AND user_id = ?', params.id, params.userId);
    writeAudit(db, {
      orgId: params.id,
      actor: caller.user,
      action: 'organization.member.remove',
      resourceType: 'membership',
      resourceId: params.userId,
    });
    return { ok: true };
  });

  app.get('/api/audit', async (request) => {
    const caller = requireSessionCaller(request);
    const canRead =
      caller.user.isSystemAdmin ||
      caller.permissions.has('observe:read') ||
      caller.permissions.has('system:manage');
    if (!canRead) throw forbidden('缺少审计日志查看权限');
    const { limit, offset } = pageOptions(request, 100);
    const rows = caller.user.isSystemAdmin
      ? all<Record<string, unknown>>(db, 'SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT ? OFFSET ?', limit, offset)
      : all<Record<string, unknown>>(
          db,
          'SELECT * FROM audit_logs WHERE org_id = ? ORDER BY created_at DESC LIMIT ? OFFSET ?',
          caller.orgId,
          limit,
          offset,
        );
    return { items: rows.map(serializeAudit) };
  });
}
