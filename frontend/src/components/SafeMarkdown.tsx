import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * 只渲染 Markdown：不解析原始 HTML，图片只显示为链接，链接在新窗口打开
 * （应用技术方案 7.2：防止文档/模型输出把内容拼进图片地址外发）。
 */
/**
 * 只渲染 Markdown：不解析原始 HTML，图片只显示为链接，链接在新窗口打开
 * （应用技术方案 7.2：防止文档/模型输出把内容拼进图片地址外发）。
 * `components` 可覆盖默认的元素渲染（对话页用 `cite:` 链接渲染引用角标）。
 */
const components: Components = {
  a: ({ href, children }) => (
    <a href={href} target="_blank" rel="noopener noreferrer">
      {children}
    </a>
  ),
  img: ({ src, alt }) => (
    <a href={src} target="_blank" rel="noopener noreferrer">
      {alt && alt.length > 0 ? alt : (src ?? "图片")}
    </a>
  ),
};

interface SafeMarkdownProps {
  content: string;
  /** 覆盖默认的元素渲染，未覆盖的沿用安全默认值。 */
  components?: Components;
}

export function SafeMarkdown({ content, components: overrides }: SafeMarkdownProps) {
  const merged: Components = overrides ? { ...components, ...overrides } : components;
  return (
    <div className="safe-markdown" style={{ wordBreak: "break-word" }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={merged} skipHtml>
        {content}
      </ReactMarkdown>
    </div>
  );
}
