import type { components } from "../api/schema";

export type Org = components["schemas"]["OrgOut"];
export type Role = components["schemas"]["Role"];

export interface OrgTreeNode {
  org: Org;
  children: OrgTreeNode[];
  /** 含下级的用户数（自己 + 所有后代）。 */
  totalUserCount: number;
}

/** 把 GET /orgs 的平铺列表拼成树，并累加每个节点含下级的用户数。 */
export function buildOrgTree(orgs: Org[]): OrgTreeNode[] {
  const nodes = new Map<number, OrgTreeNode>();
  for (const org of orgs) {
    nodes.set(org.id, {
      org,
      children: [],
      totalUserCount: org.user_count,
    });
  }

  const roots: OrgTreeNode[] = [];
  for (const org of orgs) {
    const node = nodes.get(org.id);
    if (!node) {
      continue;
    }
    const parent = org.parent_id != null ? nodes.get(org.parent_id) : undefined;
    if (parent) {
      parent.children.push(node);
    } else {
      roots.push(node);
    }
  }

  for (const root of roots) {
    accumulate(root);
  }
  return roots;
}

/** 累加含下级的用户数，返回该节点的合计。 */
function accumulate(node: OrgTreeNode): number {
  let total = node.org.user_count;
  for (const child of node.children) {
    total += accumulate(child);
  }
  node.totalUserCount = total;
  return total;
}

export interface OrgSelectNode {
  value: number;
  title: string;
  children: OrgSelectNode[];
}

/** 转成 Ant Design TreeSelect 的 treeData。 */
export function toOrgSelectData(nodes: OrgTreeNode[]): OrgSelectNode[] {
  return nodes.map((node) => ({
    value: node.org.id,
    title: node.org.name,
    children: toOrgSelectData(node.children),
  }));
}

/** 组织树下级的全部名称（不含自己），用于“只能把下级组织（…）设为组织管理员”的提示。 */
export function descendantNames(orgs: Org[], orgPath: string | null): string[] {
  if (!orgPath) {
    return [];
  }
  return orgs
    .filter((org) => org.path.startsWith(orgPath) && org.path !== orgPath)
    .map((org) => org.name);
}

/** 判断组织管理员能否把目标组织（orgPath）的用户设为组织管理员（技术方案 7.2）。 */
export interface OperatorScope {
  role: Role;
  /** 操作人所属组织的 path；系统管理员为 null。 */
  orgPath: string | null;
}

export function canAppointOrgAdmin(
  me: OperatorScope | null | undefined,
  targetOrgPath: string | null | undefined,
): boolean {
  if (!me) {
    return false;
  }
  if (me.role === "sys_admin") {
    return true;
  }
  if (me.role !== "org_admin" || !me.orgPath || !targetOrgPath) {
    return false;
  }
  return targetOrgPath.startsWith(me.orgPath) && targetOrgPath !== me.orgPath;
}
