import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { SafeMarkdown } from "./SafeMarkdown";

describe("SafeMarkdown", () => {
  it("不渲染原始 HTML（script 被丢弃）", () => {
    const { container } = render(
      <SafeMarkdown content={'<script>alert("x")</script>'} />,
    );
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).not.toContain("alert");
  });

  it("图片只显示为链接，不渲染 img", () => {
    const { container } = render(
      <SafeMarkdown content={"![架构图](https://evil.test/a.png)"} />,
    );
    expect(container.querySelector("img")).toBeNull();
    const link = screen.getByRole("link", { name: "架构图" });
    expect(link).toHaveAttribute("href", "https://evil.test/a.png");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("链接在新窗口打开且带 rel", () => {
    render(<SafeMarkdown content={"[官网](https://example.com)"} />);
    const link = screen.getByRole("link", { name: "官网" });
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });

  it("渲染 GFM 表格", () => {
    render(
      <SafeMarkdown content={"| 名称 | 值 |\n| --- | --- |\n| a | 1 |"} />,
    );
    expect(screen.getByRole("table")).toBeInTheDocument();
    expect(screen.getByText("名称")).toBeInTheDocument();
  });
});
