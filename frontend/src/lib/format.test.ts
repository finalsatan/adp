import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatRelative, formatTime } from "./format";

describe("formatTime", () => {
  it("按 YYYY-MM-DD HH:mm 显示", () => {
    expect(formatTime("2025-10-20 09:12")).toBe("2025-10-20 09:12");
  });

  it("空值显示 -", () => {
    expect(formatTime(null)).toBe("-");
  });
});

describe("formatRelative", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date(2025, 9, 20, 10, 0, 0));
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("今天只显示时间", () => {
    expect(formatRelative("2025-10-20 09:12")).toBe("今天 09:12");
  });

  it("昨天加前缀", () => {
    expect(formatRelative("2025-10-19 17:05")).toBe("昨天 17:05");
  });

  it("更早显示月-日 时间", () => {
    expect(formatRelative("2025-10-15 11:03")).toBe("10-15 11:03");
  });
});
