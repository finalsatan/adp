import type { Components } from "react-markdown";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * 只渲染 Markdown：不解析原始 HTML，图片只显示为链接，链接在新窗口打开
 * （应用技术方案 7.2：防止文档/模型输出把内容拼进图片地址外发）。
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

export function SafeMarkdown({ content }: { content: string }) {
  return (
    <div className="safe-markdown" style={{ wordBreak: "break-word" }}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components} skipHtml>
        {content}
      </ReactMarkdown>
    </div>
  );
}
