import { describe, expect, it } from "vitest";

import { formatCount, formatShare, formatToken, sharePercent } from "./numberFormat";

describe("formatCount", () => {
  it("千分位显示，不加单位", () => {
    expect(formatCount(9999)).toBe("9,999");
    expect(formatCount(48216)).toBe("48,216");
  });
});

describe("formatToken", () => {
  it("小于 1 万显示原数", () => {
    expect(formatToken(9999)).toBe("9,999");
    expect(formatToken(0)).toBe("0");
  });

  it("大于等于 1 万用万为单位", () => {
    expect(formatToken(61200000)).toBe("6,120 万");
    expect(formatToken(10000)).toBe("1 万");
  });

  it("不足 1 万的万位保留一位小数", () => {
    expect(formatToken(12345)).toBe("1.2 万");
  });
});

describe("formatShare", () => {
  it("按调用次数计算百分比", () => {
    expect(formatShare(312, 48216)).toBe("0.6%");
    expect(formatShare(18420, 48216)).toBe("38.2%");
  });

  it("合计为 0 时显示破折号", () => {
    expect(formatShare(0, 0)).toBe("—");
    expect(sharePercent(0, 0)).toBe(0);
  });
});
