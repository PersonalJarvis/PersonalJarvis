/**
 * The role picker's job is to offer every REAL choice and no fake one.
 *
 * Two ways it went wrong: `qualifying` alone left one option after an
 * offline sweep (BUG-188), and offering every installed download then put
 * an embedding-only model in the speech list. These tests pin the rule that
 * replaced both — a download is listed when it has the job's capabilities,
 * called a fit when it also sits inside the job's size class, and never
 * listed when it lacks a capability.
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

const GIB = 1024 ** 3;

function role(extra: Partial<RoleRow> = {}): RoleRow {
  return {
    id: "chat",
    label_key: "local_models.role_chat",
    config_key: "brain.providers.ollama.model",
    current: "",
    installed: false,
    required: ["completion"],
    recommended_capabilities: [],
    qualifying: [],
    recommended: "",
    writable: true,
    advanced: false,
    note: "",
    ...extra,
  } as RoleRow;
}

function model(
  name: string,
  caps: string[] = ["completion", "tools"],
  sizeGb = 4,
  probed = true,
): LocalModelRow {
  return {
    name,
    capabilities: caps,
    probed,
    size_bytes: sizeGb * GIB,
    used_by: [],
  } as unknown as LocalModelRow;
}

describe("splitChoices", () => {
  it("lists only downloads that have the job's capabilities", () => {
    const { fits, others } = splitChoices(role({ required: ["completion"] }), [
      model("ornith:9b"),
      model("bge-m3:latest", ["embedding"], 1),
      model("deepseek-llm:latest", ["completion"]),
    ]);
    // An embedding-only model cannot answer; it is not a choice at all.
    expect(fits).toEqual(["ornith:9b", "deepseek-llm:latest"]);
    expect(others).toEqual([]);
  });

  it("calls a download a fit only inside the job's size class", () => {
    // The voice brain must answer within a breath: under 6 GB.
    const voice = role({ id: "voice", required: ["completion"], max_size_gb: 6 });
    const { fits, others } = splitChoices(voice, [
      model("ornith:9b", ["completion", "tools"], 5.2),
      model("qwen3.5:4b", ["completion", "tools"], 3.2),
      model("nemotron-cascade-2:latest", ["completion", "tools"], 22.6),
      model("deepseek-r1:14b", ["completion", "tools"], 8.4),
      model("bge-m3:latest", ["embedding"], 1.1),
    ]);
    expect(fits).toEqual(["ornith:9b", "qwen3.5:4b"]);
    // Still able, still offered — under a heading that says what it costs.
    expect(others).toEqual(["nemotron-cascade-2:latest", "deepseek-r1:14b"]);
  });

  it("requires every capability, not any", () => {
    const tools = role({ id: "tools_screen", required: ["tools", "vision"] });
    const { fits, others } = splitChoices(tools, [
      model("gemma4:12b-it-qat", ["completion", "vision", "tools"]),
      model("ornith:9b", ["completion", "tools"]),
      model("qwen3-vl:2b", ["completion", "vision"]),
    ]);
    expect(fits).toEqual(["gemma4:12b-it-qat"]);
    expect(others).toEqual([]);
  });

  it("offers an unprobed download without calling it a fit", () => {
    const { fits, others } = splitChoices(role(), [
      model("known:7b"),
      model("mystery:7b", [], 4, false),
    ]);
    expect(fits).toEqual(["known:7b"]);
    expect(others).toEqual(["mystery:7b"]);
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
      role({ current: "bge-m3", required: ["embedding"], qualifying: ["bge-m3:latest"] }),
      [model("bge-m3:latest", ["embedding"], 1)],
    );
    expect(fits).toEqual(["bge-m3:latest"]);
    expect(others).toEqual([]);
  });

  it("trusts the backend's qualifying list for a row the inventory has not caught up with", () => {
    const { fits } = splitChoices(role({ qualifying: ["fresh:1b"] }), []);
    expect(fits).toEqual(["fresh:1b"]);
  });
});

describe("RolePicker", () => {
  it("groups the options and reports the pick", () => {
    const onPick = vi.fn();
    render(
      <RolePicker
        row={role({ current: "gemma4:12b", qualifying: ["gemma4:12b"] })}
        models={[model("gemma4:12b"), model("mystery:7b", [], 4, false)]}
        onPick={onPick}
      />,
    );
    const select = screen.getByTestId("role-picker-chat") as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual([
      "",
      "gemma4:12b",
      "mystery:7b",
    ]);
    expect(
      [...select.querySelectorAll("optgroup")].map((g) => g.label),
    ).toEqual([
      "local_models.roles.pick_group_fits",
      "local_models.roles.pick_group_others",
    ]);

    fireEvent.change(select, { target: { value: "mystery:7b" } });
    expect(onPick).toHaveBeenCalledWith("mystery:7b");
  });

  it("names the size class on the second heading when the job has one", () => {
    render(
      <RolePicker
        row={role({ id: "voice", current: "ornith:9b", max_size_gb: 6 })}
        models={[
          model("ornith:9b", ["completion"], 5.2),
          model("deepseek-r1:14b", ["completion"], 8.4),
        ]}
        onPick={vi.fn()}
      />,
    );
    const select = screen.getByTestId("role-picker-voice") as HTMLSelectElement;
    expect(
      [...select.querySelectorAll("optgroup")].map((g) => g.label),
    ).toEqual([
      "local_models.roles.pick_group_fits",
      "local_models.roles.pick_group_over_size6",
    ]);
    // The voice server cannot discover a model: no "Let Jarvis pick".
    expect([...select.options].map((o) => o.value)).not.toContain("");
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
        row={role({ id: "embedding", required: ["embedding"], current: "bge-m3:latest" })}
        models={[model("bge-m3:latest", ["embedding"], 1)]}
        onPick={vi.fn()}
      />,
    );
    const select = screen.getByTestId(
      "role-picker-embedding",
    ) as HTMLSelectElement;
    expect([...select.options].map((o) => o.value)).toEqual(["bge-m3:latest"]);
  });
});
