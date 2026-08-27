import { describe, expect, it } from "vitest";

import { isRuntimePosition, missingNames } from "./missingNameGate";

const TSC_OUTPUT = [
  "src/components/agentic/AgenticGrid.tsx(1009,29): error TS2304: Cannot find name 'PANE_ACTIVITY_EVENT'.",
  "src/components/agentic/AgenticGrid.tsx(780,10): error TS6133: 'drafting' is declared but its value is never read.",
  "src/store/newPaneChat.ts(179,35): error TS2345: Argument of type 'X' is not assignable to parameter of type 'Y'.",
  "  Type 'X' is not assignable to type 'Y'.",
  "src/lib/x.ts(3,7): error TS2552: Cannot find name 'wnidow'. Did you mean 'window'?",
  "",
].join("\r\n");

describe("missingNames", () => {
  it("keeps only the two 'Cannot find name' codes, with their position", () => {
    expect(missingNames(TSC_OUTPUT)).toEqual([
      {
        file: "src/components/agentic/AgenticGrid.tsx",
        line: 1009,
        column: 29,
        message: "Cannot find name 'PANE_ACTIVITY_EVENT'.",
      },
      {
        file: "src/lib/x.ts",
        line: 3,
        column: 7,
        message: "Cannot find name 'wnidow'. Did you mean 'window'?",
      },
    ]);
  });

  it("reads a clean run as nothing", () => {
    expect(missingNames("")).toEqual([]);
  });
});

/** (line, column) of the first `needle` in `text`, 1-based like tsc. */
function at(text: string, needle: string): [number, number] {
  const index = text.indexOf(needle);
  if (index < 0) throw new Error(`no ${needle}`);
  const before = text.slice(0, index).split("\n");
  return [before.length, before[before.length - 1].length + 1];
}

describe("isRuntimePosition", () => {
  const check = (text: string, needle: string): boolean =>
    isRuntimePosition("probe.tsx", text, ...at(text, needle));

  it("a bare value use is live — the crash that shipped", () => {
    const text = [
      "export function attach() {",
      "  window.addEventListener(PANE_ACTIVITY_EVENT, () => {});",
      "}",
    ].join("\n");
    expect(check(text, "PANE_ACTIVITY_EVENT")).toBe(true);
  });

  it("a generic argument, an annotation and a cast are erased", () => {
    const text = [
      "const [drops, setDrops] = useState<ChatAttachment[]>([]);",
      "let one: ChatAttachment | null = null;",
      "const two = drops[0] as ChatAttachment;",
      "export { setDrops, one, two };",
    ].join("\n");
    expect(check(text, "ChatAttachment[]")).toBe(false);
    expect(check(text, "ChatAttachment | null")).toBe(false);
    expect(check(text, "ChatAttachment;")).toBe(false);
  });

  it("an interface body and a type alias are erased", () => {
    const text = [
      "export interface Row { activity: PaneActivity; }",
      "export type Change = Omit<Row, 'activity'>;",
    ].join("\n");
    expect(check(text, "PaneActivity;")).toBe(false);
    expect(check(text, "Omit<")).toBe(false);
  });

  it("typeof in a type is erased; typeof in a value is live", () => {
    const text = [
      "export type Store = typeof missingStore;",
      "export const kind = typeof missingValue;",
    ].join("\n");
    expect(check(text, "missingStore")).toBe(false);
    expect(check(text, "missingValue")).toBe(true);
  });

  it("a class's extends runs; implements and an interface's extends do not", () => {
    const text = [
      "class A extends MissingBase {}",
      "class B implements MissingShape {}",
      "interface C extends MissingParent {}",
      "export { A, B }; export type { C };",
    ].join("\n");
    expect(check(text, "MissingBase")).toBe(true);
    expect(check(text, "MissingShape")).toBe(false);
    expect(check(text, "MissingParent")).toBe(false);
  });

  it("a JSX tag is live", () => {
    const text = "export const view = <MissingPill activity=\"working\" />;";
    expect(check(text, "MissingPill")).toBe(true);
  });

  it("a position the file no longer has is treated as live", () => {
    expect(isRuntimePosition("probe.ts", "const a = 1;\n", 40, 3)).toBe(true);
  });
});
