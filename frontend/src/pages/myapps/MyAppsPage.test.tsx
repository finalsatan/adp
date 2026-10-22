import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import MyAppsPage from "./MyAppsPage";

describe("MyAppsPage", () => {
  it("显示标题、说明和空状态", () => {
    render(<MyAppsPage />);
    expect(screen.getByText("我的应用")).toBeInTheDocument();
    expect(
      screen.getByText("你能使用的、已发布的应用。"),
    ).toBeInTheDocument();
    expect(screen.getByText("暂无可用的应用")).toBeInTheDocument();
  });
});
