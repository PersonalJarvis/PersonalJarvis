import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { DeferredSocietyDialog } from "./DeferredSocietyDialog";

vi.mock("@/i18n", () => ({ useT: () => (key: string) => key }));
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

type LoadedProps = { onClose: () => void };
function LoadedDialog({ onClose }: LoadedProps) {
  const [draft, setDraft] = useState("");
  return <Dialog.Root open onOpenChange={(open) => { if (!open) onClose(); }}><Dialog.Portal>
    <Dialog.Overlay />
    <Dialog.Content>
      <Dialog.Title>Ready dialog</Dialog.Title><Dialog.Description>Edit the draft</Dialog.Description>
      <input aria-label="Dialog draft" value={draft} onChange={(event) => setDraft(event.target.value)} />
      <Dialog.Close>Close ready dialog</Dialog.Close>
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}
type Module = { default: typeof LoadedDialog };
function deferred() {
  let resolve!: (module: Module) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<Module>((ok, fail) => { resolve = ok; reject = fail; });
  return { promise, resolve, reject };
}
function Harness({ load, title = "Opening dialog" }: { load: () => Promise<Module>; title?: string }) {
  const [open, setOpen] = useState(false);
  return <><p>Existing workspace</p><button onClick={() => setOpen(true)}>Open dialog</button>
    {open && <DeferredSocietyDialog load={load} title={title} onClose={() => setOpen(false)} dialogProps={{ onClose: () => setOpen(false) }} />}
  </>;
}
function open() {
  const trigger = screen.getByRole("button", { name: "Open dialog" });
  trigger.focus();
  fireEvent.click(trigger);
  return trigger;
}

it("loads only after opening, with an accessible pending dialog that Escape cancels", async () => {
  const pending = deferred();
  const load = vi.fn(() => pending.promise);
  render(<Harness load={load} />);
  expect(load).not.toHaveBeenCalled();
  const trigger = open();
  expect(load).toHaveBeenCalledTimes(1);
  expect(screen.getByRole("dialog", { name: "Opening dialog" })).toBeTruthy();
  expect(screen.getByRole("status").getAttribute("aria-busy")).toBe("true");
  fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  await waitFor(() => expect(document.activeElement).toBe(trigger));
  await act(async () => pending.resolve({ default: LoadedDialog }));
  expect(screen.queryByLabelText("Dialog draft")).toBeNull();
});

it("preserves the loaded draft across parent updates and resets it on close/reopen", async () => {
  const load = vi.fn(async () => ({ default: LoadedDialog }));
  const view = render(<Harness load={load} />);
  const trigger = open();
  const draft = await screen.findByLabelText("Dialog draft");
  fireEvent.change(draft, { target: { value: "Unsent draft" } });
  view.rerender(<Harness load={load} title="Updated label" />);
  expect(screen.getByLabelText("Dialog draft")).toBe(draft);
  expect((draft as HTMLInputElement).value).toBe("Unsent draft");
  expect(load).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole("button", { name: "Close ready dialog" }));
  await waitFor(() => expect(document.activeElement).toBe(trigger));
  open();
  expect((await screen.findByLabelText("Dialog draft") as HTMLInputElement).value).toBe("");
  expect(load).toHaveBeenCalledTimes(2);
});

it("contains a failed import and permits a new load after closing and reopening", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  const pending = deferred();
  const load = vi.fn<() => Promise<Module>>()
    .mockImplementationOnce(() => pending.promise)
    .mockResolvedValue({ default: LoadedDialog });
  render(<Harness load={load} />);
  open();
  await act(async () => pending.reject(new Error("Unavailable dialog chunk")));
  expect(await screen.findByRole("alert")).toBeTruthy();
  expect(screen.getByText("Existing workspace")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "society.card.close" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  open();
  expect(await screen.findByLabelText("Dialog draft")).toBeTruthy();
  expect(load).toHaveBeenCalledTimes(2);
});

it("contains a loaded dialog's render error without unmounting the workspace", async () => {
  vi.spyOn(console, "error").mockImplementation(() => undefined);
  function BrokenDialog(): never { throw new Error("Broken dialog render"); }
  render(<Harness load={async () => ({ default: BrokenDialog })} />);
  open();
  expect(await screen.findByRole("alert")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "society.card.close" }));
  expect(screen.getByText("Existing workspace")).toBeTruthy();
  expect(screen.queryByRole("dialog")).toBeNull();
});
