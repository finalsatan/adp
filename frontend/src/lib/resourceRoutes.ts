/**
 * 资源类型 → 控制台详情页路径的映射。
 *
 * 第一期还没有任何资源类型，映射表为空；第三期起各资源类型上线时往这里加。
 * 总览里没有映射的类型不显示“查看 / 编辑”。
 */
export const RESOURCE_DETAIL_PATHS: Record<string, (id: number) => string> = {};

/** 该类型的详情页路径；没有映射时返回 null。 */
export function resourceDetailPath(type: string, id: number): string | null {
  const build = RESOURCE_DETAIL_PATHS[type];
  return build ? build(id) : null;
}
