import type { FastifyInstance } from 'fastify';
import { describe, expect, it } from 'vitest';
import { setupAdmin, startTestServer, TestClient } from './helpers.js';

async function login(app: FastifyInstance, email: string, orgId?: string) {
  const client = new TestClient(app);
  await client.request('POST', '/api/auth/login', { email, password: 'Password123!' });
  if (orgId) client.setOrg(orgId);
  return client;
}

describe('delegated administration contracts', () => {
  it('lets KB owners and explicit grantors query scoped grant candidates', async () => {
    const server = await startTestServer();
    try {
      const setup = await setupAdmin(server.client);
      const orgA = setup.organizations[0].id;
      const rolesA = (await server.client.request('GET', '/api/roles')).data.items as Array<{
        id: string;
        name: string;
        global: boolean;
      }>;
      const terminalA = rolesA.find((role) => !role.global && role.name === '终端用户')!;
      const adminA = rolesA.find((role) => !role.global && role.name === '组织管理员')!;
      const builderRole = await server.client.request('POST', '/api/roles', {
        name: '知识搭建者',
        permissions: ['knowledge:read', 'knowledge:write', 'knowledge:grant', 'apps:read'],
      });
      const builder = await server.client.request('POST', '/api/users', {
        name: '知识搭建者',
        email: 'builder@example.test',
        password: 'Password123!',
        orgId: orgA,
        roleId: builderRole.data.id,
      });
      const grantee = await server.client.request('POST', '/api/users', {
        name: '查看授权用户',
        email: 'grantee@example.test',
        password: 'Password123!',
        orgId: orgA,
        roleId: terminalA.id,
      });
      const delegated = await server.client.request('POST', '/api/users', {
        name: '授权代理人',
        email: 'delegated@example.test',
        password: 'Password123!',
        orgId: orgA,
        roleId: terminalA.id,
      });
      await server.client.request('POST', '/api/users', {
        name: 'A组织管理员',
        email: 'admin-a@example.test',
        password: 'Password123!',
        orgId: orgA,
        roleId: adminA.id,
      });

      const orgB = await server.client.request('POST', '/api/organizations', { name: 'B组织', parentId: orgA });
      const orgBId = orgB.data.id;
      server.client.setOrg(orgBId);
      const rolesB = (await server.client.request('GET', '/api/roles')).data.items as Array<{
        id: string;
        name: string;
        global: boolean;
      }>;
      const adminB = rolesB.find((role) => !role.global && role.name === '组织管理员')!;
      const terminalB = rolesB.find((role) => !role.global && role.name === '终端用户')!;
      const orgBAdmin = await server.client.request('POST', '/api/users', {
        name: 'B组织管理员',
        email: 'admin-b@example.test',
        password: 'Password123!',
        orgId: orgBId,
        roleId: adminB.id,
      });
      const orgBUser = await server.client.request('POST', '/api/users', {
        name: 'B组织成员',
        email: 'member-b@example.test',
        password: 'Password123!',
        orgId: orgBId,
        roleId: terminalB.id,
      });
      server.client.setOrg(orgA);

      const builderClient = await login(server.app, 'builder@example.test', orgA);
      const kb = await builderClient.request('POST', '/api/knowledge-bases', {
        name: '搭建者知识库',
        visibility: 'organization',
      });
      await builderClient.request(
        'GET',
        '/api/users',
        undefined,
        { expected: 403 },
      );
      await builderClient.request('POST', `/api/knowledge-bases/${kb.data.id}/grants`, {
        userId: grantee.data.id,
        level: 'view',
      });
      await builderClient.request('POST', `/api/knowledge-bases/${kb.data.id}/grants`, {
        userId: delegated.data.id,
        level: 'grant',
      });
      const ownerCandidates = await builderClient.request(
        'GET',
        `/api/knowledge-bases/${kb.data.id}/grant-candidates`,
      );
      const ownerIds = ownerCandidates.data.items.map((item: { id: string }) => item.id);
      expect(ownerIds).toContain(builder.data.id);
      expect(ownerIds).toContain(grantee.data.id);
      expect(ownerIds).toContain(delegated.data.id);
      expect(ownerIds).not.toContain(orgBAdmin.data.id);
      expect(ownerIds).not.toContain(orgBUser.data.id);
      for (const item of ownerCandidates.data.items) {
        expect(Object.keys(item).sort()).toEqual(['email', 'id', 'name']);
      }

      const granteeClient = await login(server.app, 'grantee@example.test', orgA);
      await granteeClient.request(
        'GET',
        `/api/knowledge-bases/${kb.data.id}/grant-candidates`,
        undefined,
        { expected: 403 },
      );
      const delegatedClient = await login(server.app, 'delegated@example.test', orgA);
      const delegatedCandidates = await delegatedClient.request(
        'GET',
        `/api/knowledge-bases/${kb.data.id}/grant-candidates`,
      );
      expect(delegatedCandidates.data.items.some((item: { id: string }) => item.id === grantee.data.id)).toBe(true);

      const adminCandidates = await server.client.request(
        'GET',
        `/api/knowledge-bases/${kb.data.id}/grant-candidates`,
      );
      expect(adminCandidates.data.items.some((item: { id: string }) => item.id === orgBUser.data.id)).toBe(true);

      // Organization-scoped endpoints must use target-org permissions, never the selected org.
      const adminAClient = await login(server.app, 'admin-a@example.test', orgA);
      await adminAClient.request('GET', `/api/organizations/${orgBId}/members`, undefined, { expected: 403 });
      const aMembers = await adminAClient.request('GET', `/api/organizations/${orgA}/members`);
      expect(aMembers.data.items.some((item: { id: string }) => item.id === builder.data.id)).toBe(true);
      for (const item of aMembers.data.items) {
        expect(Object.keys(item).sort()).toEqual(['email', 'id', 'name', 'roleId', 'roleName']);
      }

      const adminBClient = await login(server.app, 'admin-b@example.test', orgBId);
      const bMembers = await adminBClient.request('GET', `/api/organizations/${orgBId}/members`);
      const bMemberIds = bMembers.data.items.map((item: { id: string }) => item.id);
      expect(bMemberIds).toContain(orgBAdmin.data.id);
      expect(bMemberIds).toContain(orgBUser.data.id);
      expect(bMemberIds).not.toContain(builder.data.id);
      expect(bMembers.data.items.find((item: { id: string }) => item.id === orgBAdmin.data.id).roleId).toBe(adminB.id);

      const bRoles = await adminBClient.request('GET', `/api/organizations/${orgBId}/roles`);
      const bRoleIds = bRoles.data.items.map((role: { id: string }) => role.id);
      expect(bRoleIds).toContain(adminB.id);
      expect(bRoleIds).not.toContain(adminA.id);
      expect(bRoles.data.items.some((role: { global: boolean }) => role.global)).toBe(true);

      const bCandidates = await adminBClient.request('GET', `/api/organizations/${orgBId}/member-candidates`);
      const bCandidateIds = bCandidates.data.items.map((item: { id: string }) => item.id);
      expect(bCandidateIds).toContain(orgBUser.data.id);
      expect(bCandidateIds).not.toContain(builder.data.id);
      for (const item of bCandidates.data.items) {
        expect(Object.keys(item).sort()).toEqual(['email', 'id', 'name']);
      }

      await adminAClient.request(
        'POST',
        `/api/organizations/${orgBId}/members`,
        { userId: orgBUser.data.id, roleId: adminB.id },
        { expected: 403 },
      );
      await adminAClient.request(
        'POST',
        '/api/users',
        {
          name: '越权用户',
          email: 'forbidden-create@example.test',
          password: 'Password123!',
          orgId: orgBId,
          roleId: terminalB.id,
        },
        { expected: 403 },
      );

      // A shared user's memberships outside the manager's scope are private.
      await server.client.request('POST', `/api/organizations/${orgA}/members`, {
        userId: orgBUser.data.id,
        roleId: terminalA.id,
      });
      const aDirectory = await adminAClient.request('GET', '/api/users');
      const sharedInA = aDirectory.data.items.find((item: { id: string }) => item.id === orgBUser.data.id);
      expect(sharedInA.memberships.map((membership: { orgId: string }) => membership.orgId)).toEqual([orgA]);
      const sharedClient = await login(server.app, 'member-b@example.test', orgBId);
      const sharedMe = await sharedClient.request('GET', '/api/auth/me');
      expect(sharedMe.data.organizations.map((org: { id: string }) => org.id).sort()).toEqual([orgA, orgBId].sort());
      const systemDirectory = await server.client.request('GET', '/api/users');
      const sharedForSystem = systemDirectory.data.items.find((item: { id: string }) => item.id === orgBUser.data.id);
      expect(sharedForSystem.memberships.map((membership: { orgId: string }) => membership.orgId).sort())
        .toEqual([orgA, orgBId].sort());

      // When a manager explicitly manages two organizations, candidates are the union.
      await server.client.request('POST', `/api/organizations/${orgA}/members`, {
        userId: orgBAdmin.data.id,
        roleId: adminA.id,
      });
      const multiCandidates = await adminBClient.request('GET', `/api/organizations/${orgBId}/member-candidates`);
      const multiIds = multiCandidates.data.items.map((item: { id: string }) => item.id);
      expect(multiIds).toContain(builder.data.id);
      expect(multiIds).toContain(orgBUser.data.id);
      const multiDirectory = await adminBClient.request('GET', '/api/users');
      const sharedForManager = multiDirectory.data.items.find((item: { id: string }) => item.id === orgBUser.data.id);
      expect(sharedForManager.memberships.map((membership: { orgId: string }) => membership.orgId).sort())
        .toEqual([orgA, orgBId].sort());

      const systemCandidates = await server.client.request(
        'GET',
        `/api/organizations/${orgBId}/member-candidates`,
      );
      expect(systemCandidates.data.items.some((item: { id: string }) => item.id === orgBUser.data.id)).toBe(true);
    } finally {
      await server.close();
    }
  });
});
