import { useEffect, useMemo, useState, type FormEvent } from 'react';
import {
  Building2,
  ChevronRight,
  FileClock,
  GitBranch,
  KeyRound,
  Plus,
  Search,
  Shield,
  ShieldCheck,
  UserPlus,
  Users,
  X,
} from 'lucide-react';
import type { AuditItem, Organization, User } from '../../api/types';
import { auditApi, organizationApi, roleApi, userApi } from '../../api/client';
import { useApi, useDocumentTitle } from '../../hooks';
import { useAuth } from '../../contexts/AuthContext';
import { useToast } from '../../contexts/ToastContext';
import {
  Avatar,
  Badge,
  Button,
  Callout,
  Card,
  DescriptionList,
  EmptyState,
  ErrorState,
  Field,
  Input,
  JsonBlock,
  LoadingState,
  Modal,
  PageHeader,
  PermissionDenied,
  SearchInput,
  Select,
  Switch,
  Tabs,
  useConfirm,
} from '../../components/ui';
import { PERMISSIONS } from '../../utils/permissions';
import { errorMessage, formatDateTime, formatNumber } from '../../utils/format';

type SystemTab = 'orgs' | 'users' | 'roles' | 'audit';

const ACTION_LABELS: Record<string, string> = {
  create: '创建',
  update: '更新',
  delete: '删除',
  publish: '发布',
  grant: '授权',
  revoke: '吊销',
  login: '登录',
  logout: '退出',
  setup: '初始化',
  add_member: '添加成员',
  remove_member: '移除成员',
  reprocess: '重新处理',
  upload: '上传',
};

const permissionLabel = (key: string) => PERMISSIONS.find((permission) => permission.key === key)?.label ?? key;

export function SystemPage() {
  useDocumentTitle('系统管理');
  const { can, user: currentUser, currentOrg } = useAuth();
  const toast = useToast();
  const confirm = useConfirm();
  const [tab, setTab] = useState<SystemTab>('orgs');
  const [selectedOrgId, setSelectedOrgId] = useState('');
  const [orgModalOpen, setOrgModalOpen] = useState(false);
  const [orgName, setOrgName] = useState('');
  const [parentId, setParentId] = useState('');
  const [creatingOrg, setCreatingOrg] = useState(false);
  const [orgError, setOrgError] = useState<unknown>(null);
  const [userModalOpen, setUserModalOpen] = useState(false);
  const [userName, setUserName] = useState('');
  const [userEmail, setUserEmail] = useState('');
  const [userPassword, setUserPassword] = useState('');
  const [userOrgId, setUserOrgId] = useState('');
  const [userRoleId, setUserRoleId] = useState('');
  const [creatingUser, setCreatingUser] = useState(false);
  const [userError, setUserError] = useState<unknown>(null);
  const [roleModalOpen, setRoleModalOpen] = useState(false);
  const [roleName, setRoleName] = useState('');
  const [roleGlobal, setRoleGlobal] = useState(false);
  const [rolePermissions, setRolePermissions] = useState<string[]>([]);
  const [creatingRole, setCreatingRole] = useState(false);
  const [roleError, setRoleError] = useState<unknown>(null);
  const [memberModalOpen, setMemberModalOpen] = useState(false);
  const [memberUserId, setMemberUserId] = useState('');
  const [memberRoleId, setMemberRoleId] = useState('');
  const [savingMember, setSavingMember] = useState(false);
  const [memberError, setMemberError] = useState<unknown>(null);
  const [auditSearch, setAuditSearch] = useState('');
  const [userSearch, setUserSearch] = useState('');
  const [auditDetail, setAuditDetail] = useState<AuditItem | null>(null);

  const orgsState = useApi(() => organizationApi.list(), []);
  const usersState = useApi(() => userApi.list(), []);
  const auditState = useApi(() => auditApi.list(), []);

  const orgs = orgsState.data ?? [];
  const users = usersState.data ?? [];
  const audits = auditState.data ?? [];

  const manageableOrgs = useMemo(
    () => orgs.filter((org) => currentUser?.isSystemAdmin || org.permissions?.includes('system:manage')),
    [orgs, currentUser?.isSystemAdmin],
  );
  const orgMap = useMemo(() => new Map(orgs.map((org) => [org.id, org])), [orgs]);
  const selectedOrg = orgs.find((org) => org.id === selectedOrgId) ?? null;
  const canManageSelected = Boolean(
    selectedOrg && (currentUser?.isSystemAdmin || selectedOrg.permissions?.includes('system:manage')),
  );

  const membersState = useApi(() => organizationApi.members(selectedOrgId), [selectedOrgId], {
    enabled: Boolean(selectedOrgId) && canManageSelected,
  });
  const orgRolesState = useApi(() => organizationApi.roles(selectedOrgId), [selectedOrgId], {
    enabled: Boolean(selectedOrgId) && canManageSelected,
  });
  const candidatesState = useApi(() => organizationApi.memberCandidates(selectedOrgId), [selectedOrgId], {
    enabled: Boolean(selectedOrgId) && canManageSelected,
  });
  const userOrgRolesState = useApi(() => organizationApi.roles(userOrgId), [userOrgId], {
    enabled: Boolean(userOrgId) && manageableOrgs.some((org) => org.id === userOrgId),
  });

  const members = membersState.data ?? [];
  const visibleOrgRoles = useMemo(
    () => (orgRolesState.data ?? []).filter((role) => role.global || !role.orgId || role.orgId === selectedOrgId),
    [orgRolesState.data, selectedOrgId],
  );
  const memberCandidates = candidatesState.data ?? [];
  const legalMemberRoles = useMemo(
    () => visibleOrgRoles.filter((role) => role.global || !role.orgId || role.orgId === selectedOrgId),
    [visibleOrgRoles, selectedOrgId],
  );
  const legalUserRoles = useMemo(
    () => (userOrgRolesState.data ?? []).filter((role) => role.global || !role.orgId || role.orgId === userOrgId),
    [userOrgRolesState.data, userOrgId],
  );

  useEffect(() => {
    if (orgs.length === 0) return;
    if (selectedOrgId && orgs.some((org) => org.id === selectedOrgId)) return;
    const preferred =
      orgs.find((org) => org.id === currentOrg?.id && (currentUser?.isSystemAdmin || org.permissions?.includes('system:manage'))) ??
      manageableOrgs[0] ??
      orgs.find((org) => org.id === currentOrg?.id) ??
      orgs[0];
    setSelectedOrgId(preferred.id);
  }, [orgs, selectedOrgId, currentOrg?.id, currentUser?.isSystemAdmin, manageableOrgs]);

  useEffect(() => {
    setMemberModalOpen(false);
    setRoleModalOpen(false);
    setUserModalOpen(false);
    setMemberUserId('');
    setMemberRoleId('');
    setUserRoleId('');
  }, [selectedOrgId]);

  useEffect(() => {
    if (!memberModalOpen) return;
    if (!memberUserId && memberCandidates[0]) setMemberUserId(memberCandidates[0].id);
    if (!memberRoleId && legalMemberRoles[0]) setMemberRoleId(legalMemberRoles[0].id);
  }, [memberModalOpen, memberCandidates, legalMemberRoles, memberUserId, memberRoleId]);

  useEffect(() => {
    if (!userModalOpen) return;
    if (userRoleId && legalUserRoles.some((role) => role.id === userRoleId)) return;
    if (userRoleId) setUserRoleId('');
  }, [userModalOpen, legalUserRoles, userRoleId]);

  const filteredUsers = useMemo(() => {
    const keyword = userSearch.trim().toLowerCase();
    if (!keyword) return users;
    return users.filter(
      (user) =>
        user.name.toLowerCase().includes(keyword) ||
        user.email.toLowerCase().includes(keyword),
    );
  }, [users, userSearch]);

  const filteredAudits = useMemo(() => {
    const keyword = auditSearch.trim().toLowerCase();
    if (!keyword) return audits;
    return audits.filter(
      (audit) =>
        audit.actorName?.toLowerCase().includes(keyword) ||
        audit.action?.toLowerCase().includes(keyword) ||
        audit.resourceType?.toLowerCase().includes(keyword) ||
        audit.resourceId?.toLowerCase().includes(keyword),
    );
  }, [audits, auditSearch]);

  if (!can('system:manage')) {
    return (
      <div className="page">
        <PageHeader title="系统管理" description="组织、用户、角色与审计。" />
        <Card><PermissionDenied permission="system:manage" /></Card>
      </div>
    );
  }

  const orgTree = buildTree(orgs);

  const openOrgModal = () => {
    setOrgName('');
    setParentId(canManageSelected ? selectedOrgId : '');
    setOrgError(null);
    setOrgModalOpen(true);
  };

  const submitOrg = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!orgName.trim()) {
      setOrgError(new Error('请填写组织名称。'));
      return;
    }
    setCreatingOrg(true);
    setOrgError(null);
    try {
      const created = await organizationApi.create({
        name: orgName.trim(),
        parentId: parentId || null,
      });
      toast.success('组织已创建');
      setOrgModalOpen(false);
      await orgsState.reload();
      setSelectedOrgId(created.id);
    } catch (error) {
      setOrgError(error);
    } finally {
      setCreatingOrg(false);
    }
  };

  const openUserModal = () => {
    const target = canManageSelected ? selectedOrg : manageableOrgs[0];
    if (!target) {
      toast.error('没有可管理的目标组织，无法创建用户。');
      return;
    }
    setUserName('');
    setUserEmail('');
    setUserPassword('');
    setUserOrgId(target.id);
    setUserRoleId('');
    setUserError(null);
    setUserModalOpen(true);
  };

  const submitUser = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!userName.trim() || !userEmail.trim()) {
      setUserError(new Error('请填写姓名与邮箱。'));
      return;
    }
    if (userPassword.length < 8) {
      setUserError(new Error('密码至少需要 8 个字符。'));
      return;
    }
    if (!userOrgId) {
      setUserError(new Error('请选择用户所属组织。'));
      return;
    }
    setCreatingUser(true);
    setUserError(null);
    try {
      await userApi.create({
        name: userName.trim(),
        email: userEmail.trim(),
        password: userPassword,
        orgId: userOrgId,
        roleId: userRoleId || undefined,
      });
      toast.success('用户已创建');
      setUserModalOpen(false);
      usersState.reload();
      orgsState.reload();
    } catch (error) {
      setUserError(error);
    } finally {
      setCreatingUser(false);
    }
  };

  const openRoleModal = () => {
    if (!canManageSelected) {
      toast.error('当前选中组织没有管理权限，不能创建角色。');
      return;
    }
    setRoleName('');
    setRoleGlobal(false);
    setRolePermissions([]);
    setRoleError(null);
    setRoleModalOpen(true);
  };

  const submitRole = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!roleName.trim()) {
      setRoleError(new Error('请填写角色名称。'));
      return;
    }
    if (roleGlobal && !currentUser?.isSystemAdmin) {
      setRoleError(new Error('只有系统管理员可以创建全局角色。'));
      return;
    }
    if (!roleGlobal && !canManageSelected) {
      setRoleError(new Error('当前选中组织没有管理权限，不能创建组织角色。'));
      return;
    }
    setCreatingRole(true);
    setRoleError(null);
    try {
      await roleApi.create({
        name: roleName.trim(),
        permissions: rolePermissions,
        global: roleGlobal,
        orgId: roleGlobal ? undefined : selectedOrgId,
      });
      toast.success('角色已创建');
      setRoleModalOpen(false);
      orgRolesState.reload();
    } catch (error) {
      setRoleError(error);
    } finally {
      setCreatingRole(false);
    }
  };

  const openMemberModal = () => {
    if (!selectedOrg || !canManageSelected) {
      toast.error('当前选中组织没有管理权限，不能添加成员。');
      return;
    }
    setMemberUserId(memberCandidates[0]?.id ?? '');
    setMemberRoleId(legalMemberRoles[0]?.id ?? '');
    setMemberError(null);
    setMemberModalOpen(true);
  };

  const submitMember = async (event?: FormEvent) => {
    event?.preventDefault();
    if (!selectedOrg) return;
    if (!memberUserId || !memberRoleId) {
      setMemberError(new Error('请选择成员与角色。'));
      return;
    }
    setSavingMember(true);
    setMemberError(null);
    try {
      await organizationApi.addMember(selectedOrg.id, { userId: memberUserId, roleId: memberRoleId });
      toast.success('成员已加入组织');
      setMemberModalOpen(false);
      membersState.reload();
      candidatesState.reload();
      usersState.reload();
    } catch (error) {
      setMemberError(error);
    } finally {
      setSavingMember(false);
    }
  };

  const removeMember = async (member: { id: string; name: string }) => {
    if (!selectedOrg) return;
    if (
      !(await confirm({
        title: '移除组织成员？',
        description: `将「${member.name}」从「${selectedOrg.name}」移除。该用户在其他组织的成员关系不受影响；其创建的资源仍会记录原归属。`,
        confirmLabel: '移除成员',
        danger: true,
      }))
    ) {
      return;
    }
    try {
      await organizationApi.removeMember(selectedOrg.id, member.id);
      toast.success('成员已移除');
      membersState.reload();
      candidatesState.reload();
      usersState.reload();
    } catch (error) {
      toast.error(errorMessage(error));
    }
  };

  return (
    <div className="page">
      <PageHeader
        eyebrow="平台管理"
        title="系统管理"
        description="组织树、用户与角色权限、成员关系与关键操作审计。子组织不自动继承上级组织资源。"
        actions={
          <>
            {tab === 'orgs' && (
              <Button variant="primary" icon={<Plus size={15} />} onClick={openOrgModal} disabled={orgs.length === 0}>
                新建组织
              </Button>
            )}
            {tab === 'users' && (
              <Button variant="primary" icon={<UserPlus size={15} />} onClick={openUserModal} disabled={manageableOrgs.length === 0}>
                新建用户
              </Button>
            )}
            {tab === 'roles' && (
              <Button variant="primary" icon={<Shield size={15} />} onClick={openRoleModal} disabled={!canManageSelected}>
                新建角色
              </Button>
            )}
            {tab === 'audit' && (
              <Button variant="secondary" icon={<Search size={15} />} onClick={auditState.reload} loading={auditState.loading}>
                刷新审计
              </Button>
            )}
          </>
        }
      />

      <div className="stat-grid stat-grid-compact">
        <div className="stat-card">
          <div className="stat-icon"><Building2 size={17} /></div>
          <div className="stat-copy"><span>组织</span><strong>{formatNumber(orgs.length)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><Users size={17} /></div>
          <div className="stat-copy"><span>当前组织用户</span><strong>{formatNumber(users.length)}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><ShieldCheck size={17} /></div>
          <div className="stat-copy"><span>选中组织角色</span><strong className="stat-small">{canManageSelected ? formatNumber(visibleOrgRoles.length) : '无权查看'}</strong></div>
        </div>
        <div className="stat-card">
          <div className="stat-icon"><FileClock size={17} /></div>
          <div className="stat-copy"><span>审计记录</span><strong>{formatNumber(audits.length)}</strong></div>
        </div>
      </div>

      <div className="system-tabs">
        <Tabs
          tabs={[
            { value: 'orgs', label: '组织与成员', count: orgs.length },
            { value: 'users', label: '用户', count: users.length },
            { value: 'roles', label: '角色权限', count: canManageSelected ? visibleOrgRoles.length : undefined },
            { value: 'audit', label: '审计日志', count: audits.length },
          ]}
          value={tab}
          onChange={setTab}
        />
      </div>

      {orgsState.loading && !orgsState.data ? (
        <LoadingState label="正在读取系统数据…" />
      ) : orgsState.error ? (
        <ErrorState error={orgsState.error} onRetry={orgsState.reload} />
      ) : (
        <>
          {tab === 'orgs' && (
            <div className="org-layout">
              <Card padded={false}>
                <div className="card-toolbar">
                  <div className="card-toolbar-stat"><Building2 size={16} /><span>组织树</span></div>
                  <Button size="sm" variant="ghost" icon={<Plus size={14} />} onClick={openOrgModal}>
                    新建
                  </Button>
                </div>
                {orgs.length === 0 ? (
                  <EmptyState compact icon={<Building2 size={22} />} title="还没有组织" description="先创建根组织，再添加子组织与成员。" />
                ) : (
                  <div className="card-pad">
                    <div className="org-tree">
                      {orgTree.map((node) => (
                        <OrgTreeNode
                          key={node.org.id}
                          node={node}
                          selectedId={selectedOrgId}
                          onSelect={setSelectedOrgId}
                          countChildren={(orgId) => orgs.filter((org) => org.parentId === orgId).length}
                        />
                      ))}
                    </div>
                  </div>
                )}
              </Card>

              <Card>
                {selectedOrg ? (
                  <>
                    <div className="org-detail-head">
                      <div>
                        <h3>{selectedOrg.name}</h3>
                        <p>
                          组织 ID <code className="code-text">{selectedOrg.id}</code>
                          {selectedOrg.parentId ? ` · 上级：${orgMap.get(selectedOrg.parentId)?.name ?? selectedOrg.parentId}` : ' · 根组织'}
                        </p>
                      </div>
                      <div className="row-actions">
                        <Badge tone={selectedOrg.role ? 'indigo' : 'neutral'}>{selectedOrg.role || '成员'}</Badge>
                        <Badge tone="neutral">{selectedOrg.permissions?.length ?? 0} 项权限</Badge>
                      </div>
                    </div>

                    {!canManageSelected ? (
                      <Callout tone="warn" title="无目标组织管理权限">
                        当前账号对「{selectedOrg.name}」没有 system:manage 权限，不能查看或修改该组织的成员与角色。请切换到有权限的组织，或联系系统管理员。系统不会自动继承上级组织权限。
                      </Callout>
                    ) : null}

                    <DescriptionList
                      columns={2}
                      items={[
                        { label: '成员数', value: canManageSelected ? `${members.length} 人` : '需要目标组织管理权限' },
                        { label: '子组织数', value: `${orgs.filter((org) => org.parentId === selectedOrg.id).length} 个` },
                      ]}
                    />

                    <div className="org-perm-list">
                      {(selectedOrg.permissions ?? []).map((permission) => (
                        <Badge key={permission} tone="neutral">{permissionLabel(permission)}</Badge>
                      ))}
                    </div>

                    {canManageSelected && (
                      <>
                        <div className="org-member-tools">
                          <Button
                            size="sm"
                            variant="primary"
                            icon={<UserPlus size={14} />}
                            onClick={openMemberModal}
                            disabled={candidatesState.loading || legalMemberRoles.length === 0}
                          >
                            添加成员
                          </Button>
                          <Button size="sm" variant="ghost" icon={<Plus size={14} />} onClick={openOrgModal}>
                            新建子组织
                          </Button>
                        </div>

                        <div className="section-header" style={{ marginTop: 20 }}>
                          <div>
                            <h2>成员与角色</h2>
                            <p>管理该组织的成员与角色。成员可属于多个组织，移除只影响当前组织。</p>
                          </div>
                          <Badge tone="neutral">目标组织：{selectedOrg.name}</Badge>
                        </div>

                        {membersState.loading && !membersState.data ? (
                          <LoadingState label="正在读取组织成员…" />
                        ) : membersState.error ? (
                          <ErrorState error={membersState.error} onRetry={membersState.reload} compact />
                        ) : members.length === 0 ? (
                          <p className="member-empty">该组织暂无成员。可通过“添加成员”邀请用户加入。</p>
                        ) : (
                          <div className="member-list">
                            {members.map((member) => (
                              <div key={member.id} className="member-row">
                                <Avatar name={member.name} size="sm" />
                                <div className="member-row-copy">
                                  <strong>{member.name}{member.id === currentUser?.id ? '（我）' : ''}</strong>
                                  <span>{member.email}</span>
                                </div>
                                <Badge tone="neutral">{member.roleName || '成员'}</Badge>
                                <div className="member-row-actions">
                                  <Button size="sm" variant="ghost" icon={<X size={13} />} onClick={() => removeMember(member)}>
                                    移除
                                  </Button>
                                </div>
                              </div>
                            ))}
                          </div>
                        )}

                        {orgRolesState.error && (
                          <Callout tone="warn" title="组织角色读取失败">
                            {errorMessage(orgRolesState.error)} 成员仍可查看，但添加成员需要可用的目标组织角色。
                          </Callout>
                        )}
                        {candidatesState.error && (
                          <Callout tone="warn" title="候选成员读取失败">
                            {errorMessage(candidatesState.error)}
                          </Callout>
                        )}
                      </>
                    )}
                  </>
                ) : (
                  <EmptyState icon={<GitBranch size={23} />} title="选择一个组织" description="在左侧组织树中选择组织，查看成员和权限。" />
                )}
              </Card>
            </div>
          )}

          {tab === 'users' && (
            <Card padded={false}>
              <div className="card-toolbar">
                <div className="card-toolbar-stat"><Users size={16} /><span>用户</span><span className="muted-text">{users.length} 人 · 当前组织规则</span></div>
                <div className="card-toolbar-actions">
                  <SearchInput value={userSearch} onChange={setUserSearch} placeholder="搜索姓名或邮箱" />
                  <Button size="sm" variant="primary" icon={<UserPlus size={14} />} onClick={openUserModal} disabled={manageableOrgs.length === 0}>新建用户</Button>
                </div>
              </div>
              {usersState.error ? (
                <div className="card-pad"><ErrorState error={usersState.error} onRetry={usersState.reload} compact /></div>
              ) : filteredUsers.length === 0 ? (
                <EmptyState icon={<Users size={23} />} title={users.length === 0 ? '还没有用户' : '没有匹配的用户'} description="用户创建后可通过组织成员管理分配到多个组织。" />
              ) : (
                <div className="table-wrap">
                  <table className="table">
                    <thead><tr><th>用户</th><th>邮箱</th><th>所属组织</th><th>系统角色</th><th>用户 ID</th></tr></thead>
                    <tbody>
                      {filteredUsers.map((user) => {
                        const orgNames = orgs.filter((org) => membershipOf(user, org.id)).map((org) => org.name);
                        return (
                          <tr key={user.id}>
                            <td>
                              <div className="member-cell">
                                <Avatar name={user.name} size="sm" />
                                <strong>{user.name}{user.id === currentUser?.id ? '（我）' : ''}</strong>
                              </div>
                            </td>
                            <td>{user.email}</td>
                            <td>
                              {orgNames.length > 0 ? (
                                <div className="case-tags">{orgNames.map((name) => <Badge key={name} tone="neutral">{name}</Badge>)}</div>
                              ) : (
                                <span className="muted-text">接口未返回</span>
                              )}
                            </td>
                            <td>{user.isSystemAdmin ? <Badge tone="purple">系统管理员</Badge> : <span className="muted-text">普通用户</span>}</td>
                            <td><code className="code-text">{user.id.slice(0, 12)}…</code></td>
                          </tr>
                        );
                      })}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="card-foot-note">
                用户列表仍按当前组织规则读取，不会因为某个无权子组织的成员请求失败而挡住页面。新建用户时可选择目标组织并载入该组织角色。
              </div>
            </Card>
          )}

          {tab === 'roles' && (
            <Card padded={false}>
              <div className="card-toolbar">
                <div className="card-toolbar-stat">
                  <Shield size={16} />
                  <span>角色权限</span>
                  <span className="muted-text">{selectedOrg ? `目标组织：${selectedOrg.name}` : '未选择组织'} · {canManageSelected ? `${visibleOrgRoles.length} 个角色` : '无管理权限'}</span>
                </div>
                <Button size="sm" variant="primary" icon={<Shield size={14} />} onClick={openRoleModal} disabled={!canManageSelected}>新建角色</Button>
              </div>
              {!selectedOrg ? (
                <EmptyState icon={<Shield size={23} />} title="先选择组织" description="角色按目标组织加载；切换组织后只展示该组织可用的全局与组织角色。" />
              ) : !canManageSelected ? (
                <div className="card-pad">
                  <Callout tone="warn" title="无目标组织管理权限">
                    当前账号对「{selectedOrg.name}」没有 system:manage 权限，不能查看或创建该组织的角色。请切换至可管理组织。
                  </Callout>
                </div>
              ) : orgRolesState.loading && !orgRolesState.data ? (
                <LoadingState label="正在读取目标组织角色…" />
              ) : orgRolesState.error ? (
                <div className="card-pad"><ErrorState error={orgRolesState.error} onRetry={orgRolesState.reload} compact /></div>
              ) : visibleOrgRoles.length === 0 ? (
                <EmptyState icon={<Shield size={23} />} title="目标组织暂无角色" description="组织自带组织管理员角色；也可以创建全局或组织内自定义角色。" />
              ) : (
                <div className="table-wrap">
                  <table className="table">
                    <thead><tr><th>角色</th><th>范围</th><th>权限</th><th>角色 ID</th></tr></thead>
                    <tbody>
                      {visibleOrgRoles.map((role) => (
                        <tr key={role.id}>
                          <td><strong>{role.name}</strong></td>
                          <td>
                            <span className="role-scope">
                              {role.global ? <ShieldCheck size={13} /> : <Building2 size={13} />}
                              <Badge tone={role.global ? 'purple' : 'neutral'}>
                                {role.global ? '全局角色' : (role.orgId ? orgMap.get(role.orgId)?.name ?? '组织角色' : '组织角色')}
                              </Badge>
                            </span>
                          </td>
                          <td>
                            <div className="role-perms">
                              {role.permissions.length > 0 ? (
                                role.permissions.map((permission) => (
                                  <Badge key={permission} tone="neutral">{permissionLabel(permission)}</Badge>
                                ))
                              ) : (
                                <span className="muted-text">无权限</span>
                              )}
                            </div>
                          </td>
                          <td><code className="code-text">{role.id.slice(0, 12)}…</code></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="card-foot-note">
                权限由服务端校验，前端隐藏菜单只是辅助。目标组织角色与全局角色会分别展示；成员与用户弹窗只使用目标组织合法 scope 的角色。
              </div>
            </Card>
          )}

          {tab === 'audit' && (
            <Card padded={false}>
              <div className="card-toolbar">
                <div className="card-toolbar-stat"><FileClock size={16} /><span>审计日志</span><span className="muted-text">权限变更、发布、删除与密钥操作</span></div>
                <div className="card-toolbar-actions">
                  <SearchInput value={auditSearch} onChange={setAuditSearch} placeholder="搜索操作者、动作或资源" />
                  <Button size="sm" variant="secondary" icon={<KeyRound size={14} />} onClick={auditState.reload} loading={auditState.loading}>刷新</Button>
                </div>
              </div>
              {auditState.error ? (
                <div className="card-pad"><ErrorState error={auditState.error} onRetry={auditState.reload} compact /></div>
              ) : filteredAudits.length === 0 ? (
                <EmptyState icon={<FileClock size={23} />} title={audits.length === 0 ? '暂无审计记录' : '没有匹配的审计记录'} description="关键操作发生后会写入审计日志，包括操作者、动作、资源与时间。" />
              ) : (
                <div className="table-wrap">
                  <table className="table">
                    <thead><tr><th>时间</th><th>操作者</th><th>动作</th><th>资源</th><th>资源 ID</th><th className="align-right">详情</th></tr></thead>
                    <tbody>
                      {filteredAudits.map((audit) => (
                        <tr key={audit.id}>
                          <td className="muted-cell">{formatDateTime(audit.createdAt)}</td>
                          <td>{audit.actorName || '—'}</td>
                          <td>
                            <span className="audit-action">{ACTION_LABELS[audit.action] ?? audit.action}</span>
                            <span className="cell-sub">{audit.action}</span>
                          </td>
                          <td><Badge tone="neutral">{audit.resourceType}</Badge></td>
                          <td><code className="code-text">{audit.resourceId?.slice(0, 14)}…</code></td>
                          <td className="align-right">
                            <button type="button" className="row-action" onClick={() => setAuditDetail(audit)}>
                              查看 <ChevronRight size={13} />
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              <div className="card-foot-note">审计日志只读；记录内容由服务端写入，前端不做本地伪造或补全。</div>
            </Card>
          )}
        </>
      )}

      <Modal
        open={orgModalOpen}
        onClose={() => setOrgModalOpen(false)}
        title="新建组织"
        description="组织是多级树结构；下级组织不会自动继承上级组织的资源。"
        size="md"
        footer={<><Button variant="secondary" onClick={() => setOrgModalOpen(false)}>取消</Button><Button variant="primary" loading={creatingOrg} onClick={() => submitOrg()}>创建组织</Button></>}
      >
        <form className="modal-form" onSubmit={(event) => { event.preventDefault(); void submitOrg(); }}>
          <Field label="组织名称" htmlFor="org-name" required>
            <Input id="org-name" value={orgName} onChange={(event) => setOrgName(event.target.value)} placeholder="例如：研发中心" autoFocus />
          </Field>
          <Field label="上级组织" htmlFor="org-parent" hint="留空则创建根组织；需对上级组织有管理权限。">
            <Select id="org-parent" value={parentId} onChange={(event) => setParentId(event.target.value)}>
              <option value="">无（根组织）</option>
              {manageableOrgs.map((org) => <option key={org.id} value={org.id}>{org.name}</option>)}
            </Select>
          </Field>
          {orgError != null && <Callout tone="danger" title="创建失败">{errorMessage(orgError)}</Callout>}
        </form>
      </Modal>

      <Modal
        open={userModalOpen}
        onClose={() => setUserModalOpen(false)}
        title="新建用户"
        description="创建登录账号并加入目标组织。角色从目标组织接口加载，只展示全局或该组织合法角色。"
        size="md"
        footer={<><Button variant="secondary" onClick={() => setUserModalOpen(false)}>取消</Button><Button variant="primary" loading={creatingUser} onClick={() => submitUser()}>创建用户</Button></>}
      >
        <form className="modal-form" onSubmit={(event) => { event.preventDefault(); void submitUser(); }}>
          <div className="form-grid two">
            <Field label="姓名" htmlFor="sys-user-name" required><Input id="sys-user-name" value={userName} onChange={(event) => setUserName(event.target.value)} autoFocus /></Field>
            <Field label="邮箱" htmlFor="sys-user-email" required><Input id="sys-user-email" type="email" value={userEmail} onChange={(event) => setUserEmail(event.target.value)} placeholder="name@company.com" autoComplete="off" /></Field>
          </div>
          <Field label="初始密码" htmlFor="sys-user-password" required hint="至少 8 个字符。请通过安全渠道告知用户。">
            <Input id="sys-user-password" type="password" value={userPassword} onChange={(event) => setUserPassword(event.target.value)} autoComplete="new-password" />
          </Field>
          <div className="form-grid two">
            <Field label="目标组织" htmlFor="sys-user-org" required hint="仅列出当前账号可管理的组织。">
              <Select id="sys-user-org" value={userOrgId} onChange={(event) => { setUserOrgId(event.target.value); setUserRoleId(''); }}>
                {manageableOrgs.map((org) => <option key={org.id} value={org.id}>{org.name}</option>)}
              </Select>
            </Field>
            <Field label="角色" htmlFor="sys-user-role" hint="留空使用目标组织默认角色。">
              <Select id="sys-user-role" value={userRoleId} onChange={(event) => setUserRoleId(event.target.value)} disabled={userOrgRolesState.loading}>
                <option value="">默认角色</option>
                {legalUserRoles.map((role) => <option key={role.id} value={role.id}>{role.name}{role.global ? '（全局）' : ''}</option>)}
              </Select>
            </Field>
          </div>
          {userOrgRolesState.loading && <p className="field-hint">正在读取目标组织角色…</p>}
          {userOrgRolesState.error != null && (
            <Callout tone="warn" title="目标组织角色读取失败">{errorMessage(userOrgRolesState.error)} 仍可使用默认角色创建。</Callout>
          )}
          {manageableOrgs.length === 0 && <Callout tone="warn" title="没有可管理的组织">当前账号没有可管理的目标组织，无法创建用户。</Callout>}
          {userError != null && <Callout tone="danger" title="创建失败">{errorMessage(userError)}</Callout>}
        </form>
      </Modal>

      <Modal
        open={roleModalOpen}
        onClose={() => setRoleModalOpen(false)}
        title="新建角色"
        description="角色按目标组织创建；全局角色可跨组织分配。权限由服务端再次校验。"
        size="lg"
        footer={<><Button variant="secondary" onClick={() => setRoleModalOpen(false)}>取消</Button><Button variant="primary" loading={creatingRole} onClick={() => submitRole()}>创建角色</Button></>}
      >
        <form className="modal-form" onSubmit={(event) => { event.preventDefault(); void submitRole(); }}>
          <div className="form-grid two">
            <Field label="角色名称" htmlFor="role-name" required><Input id="role-name" value={roleName} onChange={(event) => setRoleName(event.target.value)} placeholder="例如：知识库维护员" autoFocus /></Field>
            <Field label="适用范围" htmlFor="role-global" hint={currentUser?.isSystemAdmin ? '全局角色可跨组织分配。' : '仅系统管理员可创建全局角色。'}>
              <Switch checked={roleGlobal} onChange={setRoleGlobal} label="全局角色" disabled={!currentUser?.isSystemAdmin} />
            </Field>
          </div>
          <Callout tone="info" title="目标范围">
            {roleGlobal ? '全局角色：跨组织分配，仅系统管理员可创建。' : `组织角色：将创建在「${selectedOrg?.name ?? '未选择组织'}」。切换组织会改变目标范围。`}
          </Callout>
          <div className="permission-groups">
            {Array.from(new Set(PERMISSIONS.map((permission) => permission.group))).map((group) => (
              <div key={group}>
                <div className="permission-group-label">{group}</div>
                <div className="permission-grid">
                  {PERMISSIONS.filter((permission) => permission.group === group).map((permission) => {
                    const checked = rolePermissions.includes(permission.key);
                    return (
                      <label key={permission.key} className={`permission-check${checked ? ' is-checked' : ''}`}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(event) =>
                            setRolePermissions((current) =>
                              event.target.checked
                                ? [...current, permission.key]
                                : current.filter((key) => key !== permission.key),
                            )
                          }
                        />
                        <span className="permission-check-copy">
                          <strong>{permission.label}</strong>
                          <span>{permission.description}</span>
                          <code>{permission.key}</code>
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
            ))}
          </div>
          <Callout tone="info">已选择 {rolePermissions.length} 项权限。空权限角色只能登录，无法访问业务页面。</Callout>
          {roleError != null && <Callout tone="danger" title="创建失败">{errorMessage(roleError)}</Callout>}
        </form>
      </Modal>

      <Modal
        open={memberModalOpen}
        onClose={() => setMemberModalOpen(false)}
        title={`添加成员${selectedOrg ? ` · ${selectedOrg.name}` : ''}`}
        size="md"
        footer={<><Button variant="secondary" onClick={() => setMemberModalOpen(false)}>取消</Button><Button variant="primary" loading={savingMember} onClick={() => submitMember()} disabled={!memberUserId || !memberRoleId}>添加成员</Button></>}
      >
        <form className="modal-form" onSubmit={(event) => { event.preventDefault(); void submitMember(); }}>
          <Field label="候选成员" htmlFor="member-user" required hint="来自目标组织的 member-candidates，不读取其他组织成员目录。">
            <Select id="member-user" value={memberUserId} onChange={(event) => setMemberUserId(event.target.value)} disabled={candidatesState.loading || memberCandidates.length === 0}>
              {memberCandidates.length === 0 && <option value="">暂无可添加成员</option>}
              {memberCandidates.map((user) => <option key={user.id} value={user.id}>{user.name} · {user.email}</option>)}
            </Select>
          </Field>
          <Field label="角色" htmlFor="member-role" required hint="来自目标组织 /roles，仅展示全局或该组织可用角色。">
            <Select id="member-role" value={memberRoleId} onChange={(event) => setMemberRoleId(event.target.value)} disabled={orgRolesState.loading || legalMemberRoles.length === 0}>
              {legalMemberRoles.length === 0 && <option value="">暂无可用角色</option>}
              {legalMemberRoles.map((role) => <option key={role.id} value={role.id}>{role.name}{role.global ? '（全局）' : ''}</option>)}
            </Select>
          </Field>
          {candidatesState.loading && <p className="field-hint">正在读取目标组织候选成员…</p>}
          {candidatesState.error != null && <Callout tone="warn" title="候选成员读取失败">{errorMessage(candidatesState.error)}</Callout>}
          {orgRolesState.error != null && <Callout tone="warn" title="角色读取失败">{errorMessage(orgRolesState.error)}</Callout>}
          {memberError != null && <Callout tone="danger" title="添加失败">{errorMessage(memberError)}</Callout>}
        </form>
      </Modal>

      <Modal
        open={auditDetail != null}
        onClose={() => setAuditDetail(null)}
        title="审计记录详情"
        size="lg"
        footer={<Button variant="secondary" onClick={() => setAuditDetail(null)}>关闭</Button>}
      >
        {auditDetail && (
          <div>
            <div className="audit-detail">
              <div className="audit-detail-item"><span>操作者</span><strong>{auditDetail.actorName || '—'}</strong></div>
              <div className="audit-detail-item"><span>动作</span><strong>{ACTION_LABELS[auditDetail.action] ?? auditDetail.action}（{auditDetail.action}）</strong></div>
              <div className="audit-detail-item"><span>资源类型</span><strong>{auditDetail.resourceType}</strong></div>
              <div className="audit-detail-item"><span>资源 ID</span><strong>{auditDetail.resourceId}</strong></div>
              <div className="audit-detail-item"><span>时间</span><strong>{formatDateTime(auditDetail.createdAt)}</strong></div>
              <div className="audit-detail-item"><span>记录 ID</span><strong>{auditDetail.id}</strong></div>
            </div>
            <div className="span-io-label">detail</div>
            <JsonBlock value={auditDetail.detail ?? '（无附加详情）'} maxHeight={360} />
          </div>
        )}
      </Modal>
    </div>
  );
}

interface OrgNodeData {
  org: Organization;
  children: OrgNodeData[];
}

function buildTree(orgs: Organization[]): OrgNodeData[] {
  const map = new Map<string, OrgNodeData>();
  orgs.forEach((org) => map.set(org.id, { org, children: [] }));
  const roots: OrgNodeData[] = [];
  orgs.forEach((org) => {
    const node = map.get(org.id);
    if (!node) return;
    if (org.parentId && map.has(org.parentId)) {
      map.get(org.parentId)!.children.push(node);
    } else {
      roots.push(node);
    }
  });
  return roots;
}

function OrgTreeNode({
  node,
  selectedId,
  onSelect,
  countChildren,
}: {
  node: OrgNodeData;
  selectedId: string;
  onSelect: (id: string) => void;
  countChildren: (id: string) => number;
}) {
  return (
    <div className="org-tree-item-wrap">
      <div className={`org-tree-item${selectedId === node.org.id ? ' is-selected' : ''}`}>
        <button type="button" className="org-tree-button" onClick={() => onSelect(node.org.id)}>
          <span className="org-tree-icon"><Building2 size={14} /></span>
          <span className="org-tree-copy">
            <strong>{node.org.name}</strong>
            <span>{node.org.role || '成员'} · {countChildren(node.org.id)} 个子组织</span>
          </span>
          {selectedId === node.org.id && <ChevronRight size={14} />}
        </button>
      </div>
      {node.children.length > 0 && (
        <div className="org-tree-children">
          {node.children.map((child) => (
            <OrgTreeNode key={child.org.id} node={child} selectedId={selectedId} onSelect={onSelect} countChildren={countChildren} />
          ))}
        </div>
      )}
    </div>
  );
}

function membershipOf(user: User, orgId: string): { roleId?: string; role?: string; roleName?: string } | undefined {
  const lists = [user.organizations, user.memberships, user.orgs].filter(Boolean) as Array<Array<Record<string, unknown>>>;
  for (const list of lists) {
    for (const item of list) {
      const itemOrgId = (item.orgId ?? item.organizationId ?? (item.org as { id?: string } | undefined)?.id) as string | undefined;
      if (itemOrgId === orgId) {
        return {
          roleId: typeof item.roleId === 'string' ? item.roleId : undefined,
          role: typeof item.role === 'string' ? item.role : undefined,
          roleName: typeof item.roleName === 'string' ? item.roleName : undefined,
        };
      }
    }
  }
  return undefined;
}
