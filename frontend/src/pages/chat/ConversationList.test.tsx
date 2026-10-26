import { App as AntApp } from "antd";
import { fireEvent, render, screen, within } from "@testing-library/react";
import dayjs from "dayjs";
import { describe, expect, it, vi } from "vitest";

import { ConversationList } from "./ConversationList";
import type { Conversation } from "./api";

function conversation(id: number, title: string, lastActiveAt: string): Conversation {
  return {
    id,
    app_id: 1,
    title,
    created_at: lastActiveAt,
    last_active_at: lastActiveAt,
  };
}

interface RenderOptions {
  conversations?: Conversation[];
  activeId?: number | null;
}

function renderList({ conversations = [], activeId = null }: RenderOptions = {}) {
  const onNew = vi.fn();
  const onSelect = vi.fn();
  const onRename = vi.fn();
  const onDelete = vi.fn();
  const result = render(
    <AntApp>
      <ConversationList
        conversations={conversations}
        activeId={activeId}
        onNew={onNew}
        onSelect={onSelect}
        onRename={onRename}
        onDelete={onDelete}
      />
    </AntApp>,
  );
  return { ...result, onNew, onSelect, onRename, onDelete };
}

describe("ConversationList", () => {
  it("按今天 / 更早分组，点击选中", () => {
    const today = conversation(1, "年假折算", dayjs().subtract(1, "hour").toISOString());
    const earlier = conversation(2, "公积金提取", dayjs().subtract(3, "day").toISOString());
    const { onSelect } = renderList({ conversations: [today, earlier] });

    expect(screen.getByText("今天")).toBeInTheDocument();
    expect(screen.getByText("更早")).toBeInTheDocument();
    expect(screen.getByTestId("conversation-1")).toBeInTheDocument();
    expect(screen.getByTestId("conversation-2")).toBeInTheDocument();

    fireEvent.click(screen.getByTestId("conversation-1"));
    expect(onSelect).toHaveBeenCalledWith(1);
  });

  it("新建会话调用 onNew", () => {
    const { onNew } = renderList();
    fireEvent.click(screen.getByTestId("new-conversation"));
    expect(onNew).toHaveBeenCalledTimes(1);
  });

  it("重命名带上新标题", async () => {
    const item = conversation(1, "年假折算", dayjs().toISOString());
    const { onRename } = renderList({ conversations: [item] });

    fireEvent.click(screen.getByRole("button", { name: "会话操作 年假折算" }));
    fireEvent.click(await screen.findByText("重命名"));

    const input = await screen.findByLabelText("会话名称");
    fireEvent.change(input, { target: { value: "年假新标题" } });
    fireEvent.click(screen.getByRole("button", { name: /保\s*存/ }));

    expect(onRename).toHaveBeenCalledWith(item, "年假新标题");
  });

  it("删除确认框写明对话日志仍保留", async () => {
    const item = conversation(1, "年假折算", dayjs().toISOString());
    const { onDelete } = renderList({ conversations: [item] });

    fireEvent.click(screen.getByRole("button", { name: "会话操作 年假折算" }));
    fireEvent.click(await screen.findByText("删除"));

    expect(
      within(await screen.findByRole("dialog")).getByText(/只从你的列表中移除/),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /删\s*除/ }));

    expect(onDelete).toHaveBeenCalledWith(item);
  });
});
