/**
 * The role picker's job is to always have something to pick.
 *
 * `qualifying` alone leaves a user stranded twice over — an unreachable
 * server empties it, and an unprobed download reports no capabilities — and
 * both read as "my pick jumped back" (BUG-188). These tests pin the two
 * groups and the fact that neither list can swallow an installed model.
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { LocalModelRow, RoleRow } from "@/hooks/useLocalModels";

import { RolePicker, splitChoices } from "./RolePicker";

vi.mock("@/i18n", () => ({
  useT: () => (key: string) => key,
  fill: (template: string, values: Record<string, string>) =>
    `${template}${Object.values(values).join("|")}`,
}));

function role(extra: Partial<RoleRow> = {}): RoleRow {
  return {
    id: "chat",
    label_key: "local_models.role_chat",
    config_key: "brain.providers.ollama.model",
    current: "",
    installed: false,
    required: [],
    recommended_capabilities: [],
    qualifying: [],
    recommended: "",
    writable: true,
    advanced: false,
    note: "",
    ...extra,
  } as RoleRow;
}

const model = (name: string): LocalModelRow =>
  ({ name, capabilities: [], probed: true, used_by: [] }) as unknown as LocalModelRow;

describe("splitChoices", () => {
  it("puts the qualifying models first and every other download after them", () => {
    const { fits, others } = splitChoices(
      role({ qualifying: ["gemma4:12b", "qwen3.5:4b"] }),
      [model("gemma4:12b"), model("qwen3.5:4b"), model("deepseek-r1:14b")],
    );
    expect(fits).toEqual(["gemma4:12b", "qwen3.5:4b"]);
    expect(others).toEqual(["deepseek-r1:14b"]);
  });

  it("still offers the installed models when nothing qualifies", () => {
    // The state the section used to get stuck in: no shortlist, so the only
    // option was the tag already configured.
    const { fits, others } = splitChoices(
      role({ current: "ornith:9b", qualifying: [] }),
      [model("gemma4:12b"), model("qwen3.5:4b"), model("ornith:9b")],
    );
    expect(fits).toEqual([]);
    expect(others).toEqual(["gemma4:12b", "qwen3.5:4b", "ornith:9b"]);
  });

  it("keeps a configured tag that is gone from disk, exactly once", () => {
    const { fits, others } = splitChoices(
      role({ current: "removed:7b", qualifying: ["qwen3.5:4b"] }),
      [model("qwen3.5:4b")],
    );
    expect(fits).toEqual(["qwen3.5:4b"]);
    expect(others).toEqual(["removed:7b"]);
  });

  it("treats a bare tag and its :latest as one download", () => {
    const { fits, others } = splitChoices(
      role({ current: "bge-m3", qualifying: ["bge-m3:latest"] }),
      [model("bge-m3:latest")],
    );
    expect(fits).toEqual(["bge-m3:latest"]);
    expect(others).toEqual([]);
  });
});

describe("RolePicker", () => {
  it("groups the options and reports the pick", () => {
    const onPick = vi.fn();
    render(
      <RolePicker
        row={role({ current: "gemma4:12b", qualifying: ["gemma4:12b"] })}
        models={[model("gemma4:12b"), model("deepseek-r1:14b")]}
        onPick={onPick}
      />,
    );
    const select = screen.getByTestId("role-picker-chat") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual([
      "",
      "gemma4:12b",
      "deepseek-r1:14b",
    ]);
    expect(
      [...select.querySelectorAll("optgroup")].map((g) => g.label),
    ).toEqual([
      "local_models.roles.pick_group_fits",
      "local_models.roles.pick_group_others",
    ]);

    fireEvent.change(select, { target: { value: "deepseek-r1:14b" } });
    expect(onPick).toHaveBeenCalledWith("deepseek-r1:14b");
  });

  it("marks a configured model that is no longer on the server", () => {
    render(
      <RolePicker
        row={role({ current: "removed:7b" })}
        models={[model("qwen3.5:4b")]}
        onPick={vi.fn()}
      />,
    );
    const select = screen.getByTestId("role-picker-chat") as HTMLSelectElement;
    const gone = [...select.options].find((o) => o.value === "removed:7b");
    expect(gone?.textContent).toContain(
      "local_models.roles.pick_missing_suffix",
    );
  });

  it("offers no discovery option for the embedding role once one is set", () => {
    render(
      <RolePicker
        row={role({ id: "embedding", current: "bge-m3:latest" })}
        models={[model("bge-m3:latest")]}
        onPick={vi.fn()}
      />,
    );
    const select = screen.getByTestId(
      "role-picker-embedding",
    ) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["bge-m3:latest"]);
  });
});
