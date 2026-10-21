import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import { ApiError, authApi, setActiveOrgId } from '../api/client';
import type { Branding, Organization, Permission, SessionPayload, User } from '../api/types';
import { errorMessage } from '../utils/format';

const ORG_STORAGE_KEY = 'adp.currentOrgId';

interface AuthContextValue {
  loading: boolean;
  bootError: string | null;
  initialized: boolean;
  oidcEnabled: boolean;
  branding: Branding;
  user: User | null;
  organizations: Organization[];
  currentOrg: Organization | null;
  setCurrentOrg: (orgId: string) => void;
  refresh: () => Promise<void>;
  login: (email: string, password: string) => Promise<void>;
  setup: (body: { name: string; email: string; password: string; orgName: string }) => Promise<void>;
  logout: () => Promise<void>;
  can: (permission: Permission | string) => boolean;
  canAny: (permissions: Array<Permission | string>) => boolean;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const defaultBranding: Branding = { name: 'ADP', subtitle: '智能体开发平台' };

export function AuthProvider({ children }: { children: ReactNode }) {
  const [loading, setLoading] = useState(true);
  const [bootError, setBootError] = useState<string | null>(null);
  const [initialized, setInitialized] = useState(false);
  const [oidcEnabled, setOidcEnabled] = useState(false);
  const [branding, setBranding] = useState<Branding>(defaultBranding);
  const [session, setSession] = useState<SessionPayload | null>(null);
  const [currentOrgId, setCurrentOrgId] = useState<string | null>(() => {
    try {
      return window.localStorage.getItem(ORG_STORAGE_KEY);
    } catch {
      return null;
    }
  });

  const applySession = useCallback(
    (next: SessionPayload | null) => {
      setSession(next);
      if (!next) {
        setActiveOrgId(null);
        return;
      }
      const stored = (() => {
        try {
          return window.localStorage.getItem(ORG_STORAGE_KEY);
        } catch {
          return null;
        }
      })();
      const preferred =
        next.organizations.find((org) => org.id === stored) ??
        next.organizations.find((org) => org.id === currentOrgId) ??
        next.organizations[0] ??
        null;
      setCurrentOrgId(preferred?.id ?? null);
      setActiveOrgId(preferred?.id ?? null);
      if (preferred) {
        try {
          window.localStorage.setItem(ORG_STORAGE_KEY, preferred.id);
        } catch {
          // Storage can be unavailable in private browsing; the session still works.
        }
      }
    },
    [currentOrgId],
  );

  const bootstrap = useCallback(async () => {
    setLoading(true);
    setBootError(null);
    try {
      const [brandingResult, statusResult] = await Promise.all([
        authApi.branding().catch(() => defaultBranding),
        authApi.status(),
      ]);
      setBranding(brandingResult);
      setInitialized(statusResult.initialized);
      setOidcEnabled(statusResult.oidcEnabled);
      if (statusResult.initialized) {
        try {
          const me = await authApi.me();
          applySession(me);
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 401) {
            throw error;
          }
          setSession(null);
        }
      } else {
        setSession(null);
      }
    } catch (error) {
      setBootError(errorMessage(error));
    } finally {
      setLoading(false);
    }
  }, [applySession]);

  useEffect(() => {
    void bootstrap();
  }, [bootstrap]);

  useEffect(() => {
    const handler = () => {
      setSession(null);
      setActiveOrgId(null);
    };
    window.addEventListener('adp:unauthorized', handler);
    return () => window.removeEventListener('adp:unauthorized', handler);
  }, []);

  const setCurrentOrg = useCallback(
    (orgId: string) => {
      if (!session?.organizations.some((org) => org.id === orgId)) return;
      setCurrentOrgId(orgId);
      setActiveOrgId(orgId);
      try {
        window.localStorage.setItem(ORG_STORAGE_KEY, orgId);
      } catch {
        // Ignore storage failures.
      }
    },
    [session],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      const result = await authApi.login({ email, password });
      setInitialized(true);
      applySession(result);
    },
    [applySession],
  );

  const setup = useCallback(
    async (body: { name: string; email: string; password: string; orgName: string }) => {
      const result = await authApi.setup(body);
      setInitialized(true);
      applySession(result);
    },
    [applySession],
  );

  const logout = useCallback(async () => {
    try {
      await authApi.logout();
    } finally {
      setSession(null);
      setActiveOrgId(null);
    }
  }, []);

  const currentOrg = useMemo(
    () => session?.organizations.find((org) => org.id === currentOrgId) ?? session?.organizations[0] ?? null,
    [session, currentOrgId],
  );

  const can = useCallback(
    (permission: Permission | string) => {
      if (!session?.user) return false;
      if (session.user.isSystemAdmin) return true;
      return currentOrg?.permissions?.includes(permission) ?? false;
    },
    [session, currentOrg],
  );

  const canAny = useCallback(
    (permissions: Array<Permission | string>) => permissions.some((permission) => can(permission)),
    [can],
  );

  const value = useMemo<AuthContextValue>(
    () => ({
      loading,
      bootError,
      initialized,
      oidcEnabled,
      branding,
      user: session?.user ?? null,
      organizations: session?.organizations ?? [],
      currentOrg,
      setCurrentOrg,
      refresh: bootstrap,
      login,
      setup,
      logout,
      can,
      canAny,
    }),
    [
      loading,
      bootError,
      initialized,
      oidcEnabled,
      branding,
      session,
      currentOrg,
      setCurrentOrg,
      bootstrap,
      login,
      setup,
      logout,
      can,
      canAny,
    ],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const context = useContext(AuthContext);
  if (!context) throw new Error('useAuth must be used inside AuthProvider');
  return context;
}
