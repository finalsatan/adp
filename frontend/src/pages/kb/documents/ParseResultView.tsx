import { Alert } from "antd";
import dayjs from "dayjs";
import { useEffect, useRef } from "react";

import { SafeMarkdown } from "../../../components/SafeMarkdown";
import type { ParseResultOut } from "./api";
import { highlightedTexts, textIsHighlighted } from "./locate";

const HIGHLIGHT_BACKGROUND = "#fffbe6";
const BLOCK_SELECTOR = "p, li, td, th, h1, h2, h3, h4, h5, h6";

interface ParseResultViewProps {
  result: ParseResultOut;
  /** 需要高亮的元素引用（如 `#/texts/12`），来自位置数据。 */
  highlightRefs?: string[];
}

/**
 * 只读的解析结果视图：用 SafeMarkdown 渲染解析出的 Markdown。
 * 文本类文档按元素引用高亮对应段落；PDF 的定位走原文预览，不在这里高亮
 * （知识库技术方案 13）。
 */
export function ParseResultView({ result, highlightRefs = [] }: ParseResultViewProps) {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) {
      return;
    }
    const highlights = highlightedTexts(result.elements, highlightRefs);
    container.querySelectorAll<HTMLElement>(BLOCK_SELECTOR).forEach((block) => {
      block.style.backgroundColor = "";
      if (highlights.length > 0 && textIsHighlighted(block.textContent ?? "", highlights)) {
        block.style.backgroundColor = HIGHLIGHT_BACKGROUND;
      }
    });
  }, [result.markdown, result.elements, highlightRefs]);

  return (
    <div>
      {result.edited_at ? (
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 12 }}
          message={`解析结果已被${result.edited_by?.name ?? "他人"}于 ${dayjs(
            result.edited_at,
          ).format("MM-DD HH:mm")} 修改。`}
        />
      ) : null}
      <div ref={containerRef} data-testid="parse-result">
        <SafeMarkdown content={result.markdown} />
      </div>
    </div>
  );
}
