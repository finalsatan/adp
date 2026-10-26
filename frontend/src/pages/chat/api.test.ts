import dayjs from "dayjs";
import { describe, expect, it } from "vitest";

import {
  buildFeedbackBody,
  conversationPath,
  groupConversations,
  type Conversation,
} from "./api";

function conversation(id: number, lastActiveAt: string): Conversation {
  return {
    id,
    app_id: 1,
    title: `会话 ${id}`,
    created_at: lastActiveAt,
    last_active_at: lastActiveAt,
  };
}

describe("groupConversations", () => {
  const now = new Date("2025-10-21T12:00:00+08:00");

  it("按最后活动时间分成今天 / 更早", () => {
    const groups = groupConversations(
      [
        conversation(1, dayjs(now).subtract(1, "hour").toISOString()),
        conversation(2, dayjs(now).subtract(3, "day").toISOString()),
        conversation(3, dayjs(now).subtract(2, "hour").toISOString()),
      ],
      now,
    );

    expect(groups.map((group) => group.key)).toEqual(["today", "earlier"]);
    expect(groups[0]?.items.map((item) => item.id)).toEqual([1, 3]);
    expect(groups[1]?.items.map((item) => item.id)).toEqual([2]);
  });

  it("只有一类时只返回一组", () => {
    const groups = groupConversations(
      [conversation(1, dayjs(now).subtract(10, "minute").toISOString())],
      now,
    );
    expect(groups.map((group) => group.key)).toEqual(["today"]);
  });
});

describe("buildFeedbackBody", () => {
  it("点赞不带原因与纠错", () => {
    expect(buildFeedbackBody("up")).toEqual({ rating: "up", reasons: [], correction: null });
  });

  it("点踩带选中的原因与去空白的纠错内容", () => {
    expect(buildFeedbackBody("down", ["wrong_answer", "other"], "  正确答案  ")).toEqual({
      rating: "down",
      reasons: ["wrong_answer", "other"],
      correction: "正确答案",
    });
  });

  it("纠错为空时提交 null", () => {
    expect(buildFeedbackBody("down", ["wrong_citation"], "   ")).toEqual({
      rating: "down",
      reasons: ["wrong_citation"],
      correction: null,
    });
  });
});

describe("conversationPath", () => {
  it("新会话不带会话 ID，已有会话带上", () => {
    expect(conversationPath(3)).toBe("/chat/3");
    expect(conversationPath(3, 12)).toBe("/chat/3/12");
  });
});
