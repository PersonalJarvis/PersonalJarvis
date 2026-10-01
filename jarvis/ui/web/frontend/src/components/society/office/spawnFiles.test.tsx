import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { WORKSPACE_PATH_TYPE, setWorkspaceDragPaths } from "@/components/agentic/paneDrop";
import { NATIVE_DROP_EVENT } from "@/lib/nativeDrop";
import { useSpawnFiles } from "./spawnFiles";

function Holder() {
  const { handlers, held } = useSpawnFiles();
  return <div data-testid="holder" {...handlers}>{JSON.stringify(held.map(({ path, file }) => ({ path, file: file?.name })))}</div>;
}

function transfer(files: File[] = []) {
  const data = new Map<string, string>();
  return {
    types: ["Files", WORKSPACE_PATH_TYPE, "text/uri-list"],
    files,
    items: [],
    getData: (type: string) => data.get(type) ?? "",
    setData: (type: string, value: string) => data.set(type, value),
  } as unknown as DataTransfer;
}

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("spawn attachments keep the drag source boundary", () => {
  it("rejects a forged workspace path and file URI", () => {
    render(<Holder />);
    const dt = transfer();
    dt.setData(WORKSPACE_PATH_TYPE, "/private/secret.txt");
    dt.setData("text/uri-list", "file:///private/secret.txt");
    fireEvent.drop(screen.getByTestId("holder"), { dataTransfer: dt });
    expect(screen.getByTestId("holder").textContent).toBe("[]");
  });

  it("retains granted bytes without following native path hints", async () => {
    vi.stubGlobal("__JARVIS_EMBEDDED_DESKTOP", true);
    render(<Holder />);
    await act(async () => {
      fireEvent.drop(screen.getByTestId("holder"), {
        dataTransfer: transfer([new File(["offered"], "photo.png", { type: "image/png" })]),
      });
      window.dispatchEvent(new CustomEvent(NATIVE_DROP_EVENT, {
        detail: { paths: ["/private/photo.png"], names: ["photo.png"] },
      }));
    });
    expect(JSON.parse(screen.getByTestId("holder").textContent!)).toEqual([{ file: "photo.png" }]);
  });

  it("retains the selected internal explorer path", () => {
    render(<Holder />);
    const dt = transfer();
    setWorkspaceDragPaths(dt, ["/project/selected.txt"]);
    fireEvent.drop(screen.getByTestId("holder"), { dataTransfer: dt });
    expect(JSON.parse(screen.getByTestId("holder").textContent!)).toEqual([{ path: "/project/selected.txt" }]);
  });
});
