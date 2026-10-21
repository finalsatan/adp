import { useEffect, useMemo, useRef, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import {
  Activity,
  BookOpen,
  Bot,
  Building2,
  ChevronDown,
  Cpu,
  FlaskConical,
  KeyRound,
  LayoutDashboard,
  LogOut,
  Menu,
  MessageSquareWarning,
  Settings2,
  ShieldCheck,
  X,
} from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { BrandMark, Button, IconButton } from '../components/ui';
import { initials } from '../utils/format';
import type { Permission } from '../api/types';

interface NavItem {
  to: string;
  label: string;
  icon: typeof BookOpen;
  permission?: Permission;
  end?: boolean;
}

interface NavGroup {
  label: string;
  items: NavItem[];
}

const NAV_GROUPS: NavGroup[] = [
  {
    label: '工作台',
    items: [
      { to: '/overview', label: '概览', icon: LayoutDashboard, end: true },
      { to: '/knowledge', label: '知识库', icon: BookOpen, permission: 'knowledge:read' },
    ],
  },
  {
    label: '应用开发',
    items: [{ to: '/apps', label: '知识问答', icon: Bot, permission: 'apps:read' }],
  },
  {
    label: '应用观测',
    items: [
      { to: '/traces', label: '链路追踪', icon: Activity, permission: 'observe:read' },
      { to: '/feedback', label: '用户反馈', icon: MessageSquareWarning, permission: 'observe:read' },
      { to: '/evals', label: '效果评测', icon: FlaskConical, permission: 'eval:manage' },
    ],
  },
  {
    label: '平台管理',
    items: [
      { to: '/models', label: '模型管理', icon: Cpu, permission: 'models:manage' },
      { to: '/keys', label: 'API Key', icon: KeyRound, permission: 'keys:manage' },
      { to: '/system', label: '系统管理', icon: Settings2, permission: 'system:manage' },
    ],
  },
];

const PAGE_TITLES: Array<{ prefix: string; title: string }> = [
  { prefix: '/overview', title: '概览' },
  { prefix: '/knowledge', title: '知识库' },
  { prefix: '/apps', title: '知识问答' },
  { prefix: '/traces', title: '链路追踪' },
  { prefix: '/feedback', title: '用户反馈' },
  { prefix: '/evals', title: '效果评测' },
  { prefix: '/models', title: '模型管理' },
  { prefix: '/keys', title: 'API Key' },
  { prefix: '/system', title: '系统管理' },
];

export function AppShell() {
  const { user, branding, currentOrg, organizations, setCurrentOrg, can, logout } = useAuth();
  const [mobileOpen, setMobileOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [health, setHealth] = useState<{ ok: boolean; version?: string } | null>(null);
  const location = useLocation();
  const navigate = useNavigate();
  const menuRef = useRef<HTMLDivElement>(null);

  const groups = useMemo(
    () =>
      NAV_GROUPS.map((group) => ({
        ...group,
        items: group.items.filter((item) => !item.permission || can(item.permission)),
      })).filter((group) => group.items.length > 0),
    [can],
  );

  const pageTitle = useMemo(() => {
    const matched = PAGE_TITLES.find((item) => location.pathname.startsWith(item.prefix));
    return matched?.title ?? 'ADP';
  }, [location.pathname]);

  useEffect(() => {
    setMobileOpen(false);
    setMenuOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    let cancelled = false;
    fetch('/health', { credentials: 'include' })
      .then(async (response) => {
        if (!response.ok) throw new Error('unhealthy');
        const data = (await response.json()) as { status?: string; version?: string };
        if (!cancelled) setHealth({ ok: data.status === 'ok', version: data.version });
      })
      .catch(() => {
        if (!cancelled) setHealth({ ok: false });
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const handler = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [menuOpen]);

  const handleLogout = async () => {
    await logout();
    navigate('/login', { replace: true });
  };

  return (
    <div className="app-shell">
      <button
        type="button"
        className={`sidebar-backdrop${mobileOpen ? ' is-visible' : ''}`}
        aria-label="关闭菜单"
        onClick={() => setMobileOpen(false)}
      />
      <aside className={`sidebar${mobileOpen ? ' is-open' : ''}`}>
        <div className="sidebar-brand">
          <NavLink to="/" className="brand-link" aria-label="返回首页">
            <BrandMark size="md" />
            <span className="brand-text">
              <strong>{branding.name}</strong>
              <small>{branding.subtitle}</small>
            </span>
          </NavLink>
          <IconButton label="收起菜单" className="sidebar-close" onClick={() => setMobileOpen(false)}>
            <X size={17} />
          </IconButton>
        </div>

        <div className="org-switcher">
          <div className="org-switcher-label">
            <Building2 size={13} />
            当前组织
          </div>
          <div className="org-select-wrap">
            <select
              value={currentOrg?.id ?? ''}
              onChange={(event) => setCurrentOrg(event.target.value)}
              aria-label="切换组织"
              disabled={organizations.length <= 1}
            >
              {organizations.map((org) => (
                <option key={org.id} value={org.id}>
                  {org.name}
                </option>
              ))}
            </select>
            <ChevronDown size={14} aria-hidden />
          </div>
          <div className="org-role">
            <ShieldCheck size={12} />
            {currentOrg?.role || '成员'}
            <span className="org-perm-count">{currentOrg?.permissions?.length ?? 0} 项权限</span>
          </div>
        </div>

        <nav className="sidebar-nav" aria-label="主菜单">
          {groups.map((group) => (
            <div key={group.label} className="nav-group">
              <div className="nav-group-label">{group.label}</div>
              {group.items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.end}
                    className={({ isActive }) => `nav-item${isActive ? ' is-active' : ''}`}
                  >
                    <Icon size={17} strokeWidth={1.9} />
                    <span>{item.label}</span>
                  </NavLink>
                );
              })}
            </div>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className={`service-status${health?.ok ? ' is-ok' : health ? ' is-down' : ''}`}>
            <span className="service-dot" />
            <span>
              {health == null ? '检查服务…' : health.ok ? `API 正常${health.version ? ` · v${health.version}` : ''}` : 'API 未连接'}
            </span>
          </div>
          <div className="sidebar-foot-note">文档入库 → 带引用问答 → 反馈 → 评测</div>
        </div>
      </aside>

      <div className="app-main">
        <header className="topbar">
          <div className="topbar-left">
            <IconButton label="打开菜单" className="mobile-menu" onClick={() => setMobileOpen(true)}>
              <Menu size={19} />
            </IconButton>
            <span className="topbar-title">{pageTitle}</span>
            <span className="topbar-divider" aria-hidden />
            <span className="topbar-context">{currentOrg?.name ?? '未选择组织'}</span>
          </div>
          <div className="topbar-right">
            <div className="user-menu" ref={menuRef}>
              <button
                type="button"
                className="user-trigger"
                onClick={() => setMenuOpen((value) => !value)}
                aria-expanded={menuOpen}
                aria-haspopup="menu"
              >
                <span className="user-avatar">{initials(user?.name)}</span>
                <span className="user-copy">
                  <strong>{user?.name}</strong>
                  <small>{user?.isSystemAdmin ? '系统管理员' : currentOrg?.role || '成员'}</small>
                </span>
                <ChevronDown size={14} className={menuOpen ? 'is-rotated' : ''} />
              </button>
              {menuOpen && (
                <div className="user-dropdown" role="menu">
                  <div className="user-dropdown-head">
                    <span className="user-avatar user-avatar-lg">{initials(user?.name)}</span>
                    <div>
                      <strong>{user?.name}</strong>
                      <span>{user?.email}</span>
                    </div>
                  </div>
                  <div className="user-dropdown-org">
                    <span>{currentOrg?.name}</span>
                    <span>{currentOrg?.role || '成员'}</span>
                  </div>
                  <Button
                    variant="ghost"
                    icon={<LogOut size={15} />}
                    onClick={handleLogout}
                    className="user-logout"
                  >
                    退出登录
                  </Button>
                </div>
              )}
            </div>
          </div>
        </header>

        <main className="content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
