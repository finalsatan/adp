import type { components } from "../../../api/schema";

/**
 * 原文定位与高亮的纯函数（知识库技术方案 7、13）。
 *
 * 位置数据来自检索结果或分块：PDF 为 `{page, bbox, origin}`，Word 等文本类为
 * `{ref}`（元素引用，如 `#/texts/12`）。PDF 的 `bbox` 是 `[左, 上, 右, 下]` 点坐标，
 * `origin` 说明原点在左下（`BOTTOMLEFT`）还是左上（`TOPLEFT`）。
 */

export type ParseElement = components["schemas"]["ParseElement"];

export interface PdfPosition {
  page: number;
  bbox: number[];
  origin: string | null;
}

export type Position = Record<string, unknown>;

export interface HighlightRect {
  left: number;
  top: number;
  width: number;
  height: number;
}

/** PDF 单页尺寸，单位为点（未缩放）。 */
export interface PageSize {
  width: number;
  height: number;
}

export interface LocateParams {
  chunkId: number | null;
  page: number | null;
  ref: string | null;
}

export interface LocateState {
  page: number | null;
  ref: string | null;
  positions: Position[];
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** 把接口里的 `unknown[]` 收成位置对象列表，非对象项忽略。 */
export function asPositions(raw: unknown): Position[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  return raw.filter(isRecord);
}

export function isPdfPosition(position: Position): position is PdfPosition & Position {
  return typeof position.page === "number" && Array.isArray(position.bbox);
}

/** 取指定页的 PDF 位置；不传页码时返回全部。 */
export function pdfPositions(positions: Position[], page?: number | null): PdfPosition[] {
  const all = positions.filter(isPdfPosition);
  if (page == null) {
    return all;
  }
  return all.filter((position) => position.page === page);
}

/**
 * PDF 点坐标换算为画布像素（补充：按 pdf.js viewport 的等比缩放，暂不处理页面旋转）。
 * `origin` 缺省按左下（知识库技术方案 7）。
 */
export function bboxToRect(
  bbox: readonly number[],
  origin: string | null | undefined,
  pageSize: PageSize,
  scale: number,
): HighlightRect {
  const [left = 0, top = 0, right = 0, bottom = 0] = bbox;
  const bottomLeft = (origin ?? "BOTTOMLEFT").toUpperCase() === "BOTTOMLEFT";
  const x1 = left * scale;
  const x2 = right * scale;
  const y1 = bottomLeft ? (pageSize.height - top) * scale : top * scale;
  const y2 = bottomLeft ? (pageSize.height - bottom) * scale : bottom * scale;
  return {
    left: Math.min(x1, x2),
    top: Math.min(y1, y2),
    width: Math.abs(x2 - x1),
    height: Math.abs(y2 - y1),
  };
}

/** 某页上的全部高亮框：同页多个 bbox 生成多个框。 */
export function pageRects(
  positions: Position[],
  page: number,
  pageSize: PageSize,
  scale: number,
): HighlightRect[] {
  return pdfPositions(positions, page).map((position) =>
    bboxToRect(position.bbox, position.origin, pageSize, scale),
  );
}

/** 位置数据里出现的元素引用，去重并保持顺序。 */
export function elementRefs(positions: Position[]): string[] {
  const refs: string[] = [];
  for (const position of positions) {
    const ref = position.ref;
    if (typeof ref === "string" && ref.length > 0 && !refs.includes(ref)) {
      refs.push(ref);
    }
  }
  return refs;
}

/** 归一化文本：去掉常见 Markdown 标记和空白，用于元素文本与渲染段落的比较。 */
export function normalizeText(text: string): string {
  return text
    .replace(/[*_`#>~]/g, "")
    .replace(/\s+/g, "")
    .trim();
}

/** 需要高亮的元素文本（按元素引用匹配，位置数据里的人工修改内容没有 ref）。 */
export function highlightedTexts(elements: ParseElement[], refs: string[]): string[] {
  if (refs.length === 0) {
    return [];
  }
  const wanted = new Set(refs);
  return elements
    .filter((element) => wanted.has(element.ref))
    .map((element) => normalizeText(element.text))
    .filter((text) => text.length > 0);
}

/** 渲染后的段落是否命中高亮元素。 */
export function textIsHighlighted(blockText: string, highlights: string[]): boolean {
  const normalized = normalizeText(blockText);
  if (normalized.length === 0) {
    return false;
  }
  return highlights.some((highlight) => {
    if (normalized.includes(highlight)) {
      return true;
    }
    return normalized.length >= 8 && highlight.includes(normalized);
  });
}

/** 解析 URL 查询串里的定位参数：`chunk`（分块 id）、`page`、`ref`。 */
export function parseLocateSearch(search: string): LocateParams {
  const params = new URLSearchParams(search);
  const chunkRaw = params.get("chunk");
  const pageRaw = params.get("page");
  const refRaw = params.get("ref");

  const chunkId = chunkRaw != null && chunkRaw !== "" ? Number(chunkRaw) : null;
  const page = pageRaw != null && pageRaw !== "" ? Number(pageRaw) : null;

  return {
    chunkId: chunkId != null && Number.isFinite(chunkId) ? Math.floor(chunkId) : null,
    page: page != null && Number.isFinite(page) && page > 0 ? Math.floor(page) : null,
    ref: refRaw != null && refRaw !== "" ? refRaw : null,
  };
}

/**
 * 读取命中测试结果通过路由 state 带来的定位数据（P3-17 点“原文位置”）。
 * 没有分块位置接口时，位置随 state 传过来，避免再查一次。
 */
export function locateFromState(state: unknown): LocateState {
  if (!isRecord(state)) {
    return { page: null, ref: null, positions: [] };
  }
  const page = typeof state.page === "number" ? state.page : null;
  const ref = typeof state.ref === "string" ? state.ref : null;
  return { page, ref, positions: asPositions(state.positions) };
}

/** 综合路由 state 与查询参数得到目标页：state 优先，其次查询参数，最后取位置里的第一页。 */
export function resolveTargetPage(
  state: LocateState,
  params: LocateParams,
  positions: Position[],
): number | null {
  if (state.page != null) {
    return state.page;
  }
  if (params.page != null) {
    return params.page;
  }
  const first = positions.find((position) => typeof position.page === "number");
  return first ? (first.page as number) : null;
}
