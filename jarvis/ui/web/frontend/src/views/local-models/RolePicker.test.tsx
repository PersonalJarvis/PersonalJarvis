/**
 * The role picker's job is to offer every REAL choice, name the cost of the
 * rest, and never hide a model without saying why.
 *
 * The verdict per download comes from the backend (`row.choices`, one rule
 * for every surface); these tests pin what the picker makes of it — the
 * groups, the greyed "not for this job" rows with their reason, the
 * configured tag that is gone from disk, the download group, and the
 * fallback for a payload that carries no verdicts at all (BUG-188: a list
 * must never collapse to a single option).
 */
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { LocalModelRow, RoleRow } from "@/hooks/useLocalModels";

import { buildGroups, RolePicker } from "./RolePicker";

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
    layout: "card",
    current: "",
    installed: false,
    current_fit: "",
    current_reason: "",
    required: ["completion"],
    recommended_capabilities: [],
    qualifying: [],
    choices: [],
    downloads: [],
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
  label = "",
): LocalModelRow {
  return {
    name,
    display_label: label,
    capabilities: caps,
    probed,
    size_bytes: sizeGb * GIB,
    quantization_level: "Q4_K_M",
    context_length: 32768,
    used_by: [],
  } as unknown as LocalModelRow;
}

describe("buildGroups", () => {
  it("lays the backend's verdicts out as fits / slow / unknown / unfit, in that order", () => {
    const voice = role({
      id: "voice",
      max_size_gb: 6,
      choices: [
        { tag: "deepseek-llm:latest", fit: "unfit", reason: "no tool calls" },
        { tag: "ornith:9b", fit: "fits", reason: "" },
        { tag: "mystery:7b", fit: "unknown", reason: "Jarvis could not read what this model can do." },
        { tag: "gemma4:12b-it-qat", fit: "slow", reason: "6.7 GB — over 6 GB, slower to answer" },
      ],
    });
    const groups = buildGroups(voice, [
      model("deepseek-llm:latest", ["completion"], 3.7),
      model("ornith:9b", ["completion", "tools"], 5.2, true, "Ornith 9B"),
      model("mystery:7b", [], 4, false),
      model("gemma4:12b-it-qat", ["completion", "tools", "vision"], 6.7),
    ]);
    expect(groups.map((g) => g.id)).toEqual(["fits", "slow", "unknown", "unfit"]);
    const unfit = groups.find((g) => g.id === "unfit")!.entries[0];
    // Listed, not hidden — with the reason, and not selectable.
    expect(unfit.tag).toBe("deepseek-llm:latest");
    expect(unfit.reason).toBe("no tool calls");
    expect(unfit.disabled).toBe(true);
    const fit = groups.find((g) => g.id === "fits")!.entries[0];
    expect(fit.label).toBe("Ornith 9B");
    expect(fit.facts).toContain("5.2 GB");
    expect(fit.facts).toContain("tools");
  });

  it("falls back to every installed download when the payload carries no verdicts", () => {
    // An older snapshot, a silent server: still every download, never one option.
    const groups = buildGroups(role(), [model("a:1b"), model("b:1b"), model("c:1b")]);
    expect(groups).toHaveLength(1);
    expect(groups[0].id).toBe("unknown");
    expect(groups[0].entries.map((e) => e.tag)).toEqual(["a:1b", "b:1b", "c:1b"]);
  });

  it("keeps a configured tag that is gone from disk, exactly once, marked absent", () => {
    const groups = buildGroups(
      role({ current: "removed:7b", choices: [{ tag: "qwen3.5:4b", fit: "fits", reason: "" }] }),
      [model("qwen3.5:4b")],
    );
    const unknown = groups.find((g) => g.id === "unknown")!;
    expect(unknown.entries).toHaveLength(1);
    expect(unknown.entries[0]).toMatchObject({ tag: "removed:7b", fit: "absent", installed: false });
  });

  it("treats a bare tag and its :latest as one download", () => {
    const groups = buildGroups(
      role({
        current: "bge-m3",
        required: ["embedding"],
        choices: [{ tag: "bge-m3:latest", fit: "fits", reason: "" }],
      }),
      [model("bge-m3:latest", ["embedding"], 1)],
    );
    expect(groups.map((g) => g.entries.map((e) => e.tag))).toEqual([["bge-m3:latest"]]);
  });

  it("offers the shortlist's downloads for the job as the last group", () => {
    const groups = buildGroups(
      role({
        choices: [{ tag: "qwen3.5:4b", fit: "fits", reason: "" }],
        downloads: [
          { tag: "qwen3.5", label: "Qwen 3.5 9B", size_gb: 6.6, fit: "comfortable", note: "" },
        ],
      }),
      [model("qwen3.5:4b")],
    );
    expect(groups.map((g) => g.id)).toEqual(["fits", "downloads"]);
    expect(groups[1].entries[0]).toMatchObject({ tag: "qwen3.5", label: "Qwen 3.5 9B" });
  });

  it("filters by the query on tag and label", () => {
    const groups = buildGroups(
      role({
        choices: [
          { tag: "qwen3.5:4b", fit: "fits", reason: "" },
          { tag: "ornith:9b", fit: "fits", reason: "" },
        ],
      }),
      [model("qwen3.5:4b", undefined, 3, true, "Qwen 3.5 4B"), model("ornith:9b")],
      "qwen",
    );
    expect(groups[0].entries.map((e) => e.tag)).toEqual(["qwen3.5:4b"]);
  });
});

describe("RolePicker", () => {
  it("opens a list with the groups, the reasons, and reports the pick", () => {
    const onPick = vi.fn();
    render(
      <RolePicker
        row={role({
          current: "gemma4:12b",
          installed: true,
          choices: [
            { tag: "gemma4:12b", fit: "fits", reason: "" },
            { tag: "blind:7b", fit: "unfit", reason: "no vision" },
            { tag: "mystery:7b", fit: "unknown", reason: "" },
          ],
        })}
        models={[
          model("gemma4:12b", undefined, 7, true, "Gemma 4 12B"),
          model("blind:7b"),
          model("mystery:7b", [], 4, false),
        ]}
        onPick={onPick}
      />,
    );
    const button = screen.getByTestId("role-picker-chat");
    // The button names the model by its readable label, not the tag.
    expect(button.textContent).toContain("Gemma 4 12B");
    expect(screen.queryByRole("listbox")).toBeNull();

    fireEvent.click(button);
    expect(screen.getByRole("listbox")).toBeDefined();
    expect(
      [...screen.getAllByTestId(/^role-picker-group-/)].map((g) =>
        g.getAttribute("data-testid"),
      ),
    ).toEqual([
      "role-picker-group-fits",
      "role-picker-group-unknown",
      "role-picker-group-unfit",
    ]);
    // The brain roles may go back to discovery.
    expect(screen.getByTestId("role-option-discovery")).toBeDefined();
    // The unfit row is greyed with its reason, and a click does nothing.
    const blind = screen.getByTestId("role-option-blind:7b");
    expect(blind.getAttribute("aria-disabled")).toBe("true");
    expect(screen.getByTestId("role-option-verdict-blind:7b").textContent).toBe("no vision");
    fireEvent.click(blind);
    expect(onPick).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("role-option-mystery:7b"));
    expect(onPick).toHaveBeenCalledWith("mystery:7b");
    // Picking closes the list.
    expect(screen.queryByRole("listbox")).toBeNull();
  });

  it("names the size class on the slow heading and offers no discovery for voice", () => {
    render(
      <RolePicker
        row={role({
          id: "voice",
          current: "ornith:9b",
          installed: true,
          max_size_gb: 6,
          choices: [
            { tag: "ornith:9b", fit: "fits", reason: "" },
            { tag: "deepseek-r1:14b", fit: "slow", reason: "8.4 GB — over 6 GB, slower to answer" },
          ],
        })}
        models={[model("ornith:9b", undefined, 5.2), model("deepseek-r1:14b", undefined, 8.4)]}
        onPick={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("role-picker-voice"));
    expect(screen.getByText("local_models.roles.pick_group_over_size6")).toBeDefined();
    expect(screen.queryByTestId("role-option-discovery")).toBeNull();
    // The current pick is marked, not repeated as a verdict.
    expect(screen.getByTestId("role-option-verdict-ornith:9b").textContent).toContain(
      "local_models.roles.pick_current",
    );
  });

  it("marks a configured model that is no longer on the server", () => {
    render(
      <RolePicker
        row={role({ current: "removed:7b", choices: [{ tag: "qwen3.5:4b", fit: "fits", reason: "" }] })}
        models={[model("qwen3.5:4b")]}
        onPick={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("role-picker-chat"));
    expect(screen.getByTestId("role-option-removed:7b").textContent).toContain(
      "local_models.roles.pick_missing_suffix",
    );
  });

  it("hands a download row to onInstall instead of onPick", () => {
    const onPick = vi.fn();
    const onInstall = vi.fn();
    render(
      <RolePicker
        row={role({
          choices: [],
          downloads: [
            { tag: "qwen3.5", label: "Qwen 3.5 9B", size_gb: 6.6, fit: "comfortable", note: "" },
          ],
        })}
        models={[]}
        onPick={onPick}
        onInstall={onInstall}
      />,
    );
    fireEvent.click(screen.getByTestId("role-picker-chat"));
    fireEvent.click(screen.getByTestId("role-option-qwen3.5"));
    expect(onInstall).toHaveBeenCalledWith("qwen3.5");
    expect(onPick).not.toHaveBeenCalled();
  });

  it("closes on Escape and filters as you type", () => {
    render(
      <RolePicker
        row={role({
          choices: [
            { tag: "qwen3.5:4b", fit: "fits", reason: "" },
            { tag: "ornith:9b", fit: "fits", reason: "" },
          ],
        })}
        models={[model("qwen3.5:4b"), model("ornith:9b")]}
        onPick={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByTestId("role-picker-chat"));
    fireEvent.change(screen.getByTestId("role-picker-search-chat"), { target: { value: "orn" } });
    expect(screen.queryByTestId("role-option-qwen3.5:4b")).toBeNull();
    expect(screen.getByTestId("role-option-ornith:9b")).toBeDefined();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("listbox")).toBeNull();
  });
});
