import { describe, expect, it } from "vitest";

import {
  asPositions,
  bboxToRect,
  elementRefs,
  highlightedTexts,
  locateFromState,
  pageRects,
  parseLocateSearch,
  resolveTargetPage,
  textIsHighlighted,
  type Position,
} from "./locate";

const PAGE = { width: 595, height: 842 }; // A4，单位点

describe("locate", () => {
  it("PDF 点坐标（原点左下）换算为画布像素，含缩放与页面尺寸", () => {
    // bbox 为 [左, 上, 右, 下]，左下原点：上边 500、下边 460
    const rect = bboxToRect([72, 500, 272, 460], "BOTTOMLEFT", PAGE, 2);
    expect(rect).toEqual({
      left: 144,
      top: (842 - 500) * 2,
      width: 400,
      height: 80,
    });
  });

  it("左上原点的 bbox 按 y 向下换算", () => {
    const rect = bboxToRect([10, 20, 30, 60], "TOPLEFT", PAGE, 1);
    expect(rect).toEqual({ left: 10, top: 20, width: 20, height: 40 });
  });

  it("多个 bbox 生成多个高亮框，且只取目标页", () => {
    const positions: Position[] = [
      { page: 3, bbox: [0, 10, 10, 0], origin: "BOTTOMLEFT" },
      { page: 3, bbox: [20, 30, 40, 10], origin: "BOTTOMLEFT" },
      { page: 4, bbox: [0, 10, 10, 0], origin: "BOTTOMLEFT" },
    ];
    const rects = pageRects(positions, 3, PAGE, 1);
    expect(rects).toHaveLength(2);
    expect(rects[0]).toEqual({ left: 0, top: 832, width: 10, height: 10 });
    expect(rects[1]).toEqual({ left: 20, top: 812, width: 20, height: 20 });
  });

  it("从位置数据取元素引用并去重", () => {
    const positions = asPositions([
      { ref: "#/texts/12" },
      { ref: "#/texts/12" },
      { page: 1, bbox: [0, 0, 1, 1] },
      { ref: "#/texts/13" },
      "bad",
    ]);
    expect(elementRefs(positions)).toEqual(["#/texts/12", "#/texts/13"]);
  });

  it("元素引用匹配到 Markdown 段落文本", () => {
    const elements = [
      { ref: "#/texts/11", type: "text", text: "第十条 员工出差住宿费实行限额标准。" },
      {
        ref: "#/texts/12",
        type: "text",
        text: "第十一条 一类城市：普通员工每人每晚不超过 500 元。",
      },
    ];
    const texts = highlightedTexts(elements, ["#/texts/12"]);
    expect(texts).toHaveLength(1);
    // 渲染后的段落带加粗标记也能匹配
    expect(textIsHighlighted("**第十一条** 一类城市：普通员工每人每晚不超过 500 元。", texts)).toBe(
      true,
    );
    expect(textIsHighlighted("第十二条 二类城市：不超过 400 元。", texts)).toBe(false);
  });

  it("没有命中引用时不产生高亮文本", () => {
    expect(highlightedTexts([], [])).toEqual([]);
    expect(textIsHighlighted("任意段落", [])).toBe(false);
  });

  it("解析定位参数：chunk、page、ref，非法值归零", () => {
    expect(parseLocateSearch("?chunk=42&page=3&ref=%23%2Ftexts%2F12")).toEqual({
      chunkId: 42,
      page: 3,
      ref: "#/texts/12",
    });
    expect(parseLocateSearch("")).toEqual({ chunkId: null, page: null, ref: null });
    expect(parseLocateSearch("?chunk=abc&page=-1")).toEqual({
      chunkId: null,
      page: null,
      ref: null,
    });
  });

  it("读取路由 state 的定位数据，并解析目标页", () => {
    const state = locateFromState({
      page: 3,
      positions: [{ page: 5, bbox: [0, 0, 1, 1] }],
    });
    expect(state.page).toBe(3);
    expect(state.positions).toHaveLength(1);
    expect(resolveTargetPage(state, { chunkId: 1, page: 9, ref: null }, state.positions)).toBe(3);

    const empty = locateFromState(undefined);
    expect(resolveTargetPage(empty, { chunkId: null, page: null, ref: null }, [{ page: 2 }])).toBe(2);
    expect(resolveTargetPage(empty, { chunkId: null, page: 7, ref: null }, [])).toBe(7);
  });
});
