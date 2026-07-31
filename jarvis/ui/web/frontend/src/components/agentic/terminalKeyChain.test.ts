import { describe, expect, it, vi } from "vitest";

import { createKeyEventChain } from "./terminalKeyChain";

/** Stand-in for xterm's single-handler slot. */
function fakeHost(): {
  attachCustomKeyEventHandler: (h: (e: KeyboardEvent) => boolean) => void;
  press: () => boolean;
  attachCount: number;
} {
  const host = {
    handler: ((() => true) as (e: KeyboardEvent) => boolean),
    attachCount: 0,
    attachCustomKeyEventHandler(h: (e: KeyboardEvent) => boolean) {
      host.handler = h;
      host.attachCount += 1;
    },
    press: () => host.handler(new KeyboardEvent("keydown", { key: "a" })),
  };
  return host;
}

describe("sharing xterm's one custom key handler", () => {
  /**
   * The regression guard for what the chain exists to prevent: xterm's
   * `attachCustomKeyEventHandler` is a setter, so wiring two bridges directly
   * would leave only the last one working.
   */
  it("claims the single slot once, however many members are added", () => {
    const host = fakeHost();
    const chain = createKeyEventChain(host);
    chain.add(() => true);
    chain.add(() => true);

    expect(host.attachCount).toBe(1);
  });

  it("offers a key to every member while none of them claims it", () => {
    const host = fakeHost();
    const chain = createKeyEventChain(host);
    const first = vi.fn(() => true);
    const second = vi.fn(() => true);
    chain.add(first);
    chain.add(second);

    expect(host.press()).toBe(true);
    expect(first).toHaveBeenCalledOnce();
    expect(second).toHaveBeenCalledOnce();
  });

  it("stops at the member that claims the key", () => {
    const host = fakeHost();
    const chain = createKeyEventChain(host);
    const later = vi.fn(() => true);
    chain.add(() => false);
    chain.add(later);

    expect(host.press()).toBe(false);
    expect(later).not.toHaveBeenCalled();
  });

  it("forgets a removed member", () => {
    const host = fakeHost();
    const chain = createKeyEventChain(host);
    const remove = chain.add(() => false);

    remove();

    expect(host.press()).toBe(true);
  });

  it("survives a member that removes itself while running", () => {
    const host = fakeHost();
    const chain = createKeyEventChain(host);
    const second = vi.fn(() => true);
    let remove = () => undefined as void;
    remove = chain.add(() => {
      remove();
      return true;
    });
    chain.add(second);

    expect(host.press()).toBe(true);
    expect(second).toHaveBeenCalledOnce();
  });
});
