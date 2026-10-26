import type { components } from "../../api/schema";

/** 引用快照里的一条参考内容（应用技术方案 8.2）。 */
export type Citation = components["schemas"]["CitationOut"];

/** 一个角标片段或普通文字片段。 */
export type CitationSegment =
  | { type: "text"; text: string }
  | { type: "citation"; n: number; citation: Citation };

/** `[1]`、`[1][2]`、全角 `【1】`。 */
const CITATION_PATTERN = /\[(\d+)\]|【(\d+)】/g;

/** 角标转成的相对锚点前缀（react-markdown 会丢弃 `cite:` 这类未知协议，用相对锚点最稳）。 */
export const CITATION_HREF_PREFIX = "#cite-";

function citationNumbers(citations: Citation[]): Set<number> {
  return new Set(citations.map((citation) => citation.n));
}

/**
 * 按累积的全文解析角标（应用技术方案 8.1）：跨片段的 `[1` 和 `]` 在拼接后自然合并；
 * 超出引用范围的编号按普通文字保留，不渲染成角标。
 */
export function parseCitations(content: string, citations: Citation[]): CitationSegment[] {
  const known = citationNumbers(citations);
  const segments: CitationSegment[] = [];
  let cursor = 0;

  for (const match of content.matchAll(CITATION_PATTERN)) {
    const n = Number(match[1] ?? match[2]);
    if (!known.has(n)) {
      continue;
    }
    const index = match.index ?? 0;
    if (index > cursor) {
      segments.push({ type: "text", text: content.slice(cursor, index) });
    }
    segments.push({
      type: "citation",
      n,
      citation: citations.find((citation) => citation.n === n) as Citation,
    });
    cursor = index + match[0].length;
  }

  if (cursor < content.length) {
    segments.push({ type: "text", text: content.slice(cursor) });
  }
  return segments;
}

/** 取引用的编号集合（回答下方只显示实际引用的条目时用）。 */
export function citedNumbers(content: string): number[] {
  const numbers = new Set<number>();
  for (const match of content.matchAll(CITATION_PATTERN)) {
    numbers.add(Number(match[1] ?? match[2]));
  }
  return [...numbers];
}

/**
 * 把引用范围内的角标换成 Markdown 锚点链接（`[1](#cite-1)`），交给 `SafeMarkdown` 渲染；
 * 渲染时把锚点换成角标。超范围的编号原样保留。
 */
export function toCitationMarkdown(content: string, citations: Citation[]): string {
  const known = citationNumbers(citations);
  return content.replace(CITATION_PATTERN, (match, decimal: string, fullwidth: string) => {
    const n = Number(decimal ?? fullwidth);
    return known.has(n) ? `[${n}](${CITATION_HREF_PREFIX}${n})` : match;
  });
}
