export const ALL_PERMISSIONS = [
  'knowledge:read',
  'knowledge:write',
  'knowledge:grant',
  'apps:read',
  'apps:write',
  'apps:publish',
  'models:manage',
  'eval:manage',
  'observe:read',
  'keys:manage',
  'system:manage',
] as const;

export type Permission = (typeof ALL_PERMISSIONS)[number];

export const TERMINAL_PERMISSIONS: Permission[] = ['apps:read'];

export function isPermission(value: string): value is Permission {
  return (ALL_PERMISSIONS as readonly string[]).includes(value);
}

export function parsePermissions(raw: string | null | undefined): string[] {
  if (!raw) return [];
  try {
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter((item): item is string => typeof item === 'string' && isPermission(item));
  } catch {
    return [];
  }
}

export function hasPermission(permissions: readonly string[], permission: Permission): boolean {
  return permissions.includes(permission);
}
