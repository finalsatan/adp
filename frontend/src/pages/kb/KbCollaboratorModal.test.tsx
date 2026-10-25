import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App as AntApp } from "antd";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useUserSearch } from "../../components/resource/api";
import { useAddKbMember, type Kb } from "./api";
import { KbCollaboratorModal } from "./KbCollaboratorModal";

vi.mock("../../components/resource/api", () => ({ useUserSearch: vi.fn() }));
vi.mock("./api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./api")>();
  return {
    ...actual,
    useAddKbMember: vi.fn(),
  };
});

const mockedUseUserSearch = vi.mocked(useUserSearch);
const mockedUseAddKbMember = vi.mocked(useAddKbMember);

const KB = { id: 1, name: "员工制度库" } as Kb;

beforeEach(() => {
  mockedUseUserSearch.mockReturnValue({
    data: [{ id: 7, username: "wangwu", name: "王五", org: { id: 2, name: "平台组" } }],
  } as unknown as ReturnType<typeof useUserSearch>);
  mockedUseAddKbMember.mockReturnValue({
    isPending: false,
    mutate: vi.fn(),
  } as unknown as ReturnType<typeof useAddKbMember>);
});

afterEach(() => {
  vi.clearAllMocks();
});

function renderModal() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <AntApp>
        <KbCollaboratorModal open kb={KB} existingIds={new Set()} onClose={vi.fn()} />
      </AntApp>
    </QueryClientProvider>,
  );
}

describe("KbCollaboratorModal", () => {
  it("选中用户后提交，带上知识库与用户 ID", async () => {
    const mutate = vi.fn();
    mockedUseAddKbMember.mockReturnValue({
      isPending: false,
      mutate,
    } as unknown as ReturnType<typeof useAddKbMember>);

    renderModal();
    const search = screen.getByRole("combobox");
    fireEvent.change(search, { target: { value: "wang" } });
    fireEvent.click(await screen.findByText("wangwu 王五 · 平台组"));
    fireEvent.click(screen.getByRole("button", { name: /添\s*加/ }));

    await waitFor(() => expect(mutate).toHaveBeenCalledTimes(1));
    expect(mutate.mock.calls[0]![0]).toEqual({ id: 1, userId: 7 });
  });
});
