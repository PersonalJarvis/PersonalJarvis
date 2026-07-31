/**
 * MergeQueuePanel tests — the one place a human decides an identity.
 *
 * The load-bearing property is the ASYMMETRY between the two answers. "One
 * person" merges and can be undone; "two people" is permanent and outranks
 * every later piece of evidence. A UI that treats them as two equal buttons
 * turns a stray click into an unfixable statement about someone's identity,
 * so the arming step is tested as hard as the happy path.
 *
 * The second property is honesty: the store's refusal sentence ("undo merge
 * 12 first") is the entire value of a 409 and must reach the screen intact,
 * never re-worded and never swallowed.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

import { MergeQueuePanel } from "@/components/ultrawiki/MergeQueuePanel";
import type { UltraWikiMergeProposal } from "@/lib/ultrawikiIdentityApi";

const PROPOSAL: UltraWikiMergeProposal = {
  id: 7,
  status: "pending",
  score: 0.8724,
  evidence: [
    { tier: "probable", kind: "name_similar", value: "R. Vasquez", score: 0.87 },
  ],
  created_at: "2026-07-20T10:00:00Z",
  updated_at: "2026-07-20T10:00:00Z",
  decided_at: null,
  decided_by: null,
  left: { id: 11, kind: "person", display_name: "Rita Vasquez" },
  right: { id: 12, kind: "person", display_name: "R. Vasquez" },
};

function makeClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0, staleTime: 0 } },
  });
}

interface Call {
  url: string;
  method: string;
}

function installFetch(
  handler: (url: string, method: string) => { status?: number; body: unknown },
): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      calls.push({ url, method });
      const answer = handler(url, method);
      const status = answer.status ?? 200;
      return {
        ok: status < 400,
        status,
        json: async () => answer.body,
      } as unknown as Response;
    }),
  );
  return calls;
}

function renderQueue(
  proposals: UltraWikiMergeProposal[] = [PROPOSAL],
  onOpenPerson?: (id: number) => void,
) {
  return render(
    <QueryClientProvider client={makeClient()}>
      <MergeQueuePanel proposals={proposals} onOpenPerson={onOpenPerson} />
    </QueryClientProvider>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("asking the question", () => {
  it("names both sides and how alike they are", () => {
    renderQueue();

    const card = screen.getByTestId("merge-proposal-7");
    expect(card.textContent).toContain("Rita Vasquez");
    expect(card.textContent).toContain("R. Vasquez");
    // 0.8724 is a matcher's number, not a human's.
    expect(screen.getByTestId("merge-score-7").textContent).toContain("87%");
  });

  it("explains the evidence in words, never as the matcher's token", () => {
    renderQueue();

    const evidence = screen.getByTestId("merge-evidence-7");
    expect(evidence.textContent).toContain("Similar name");
    expect(evidence.textContent).not.toContain("name_similar");
  });

  it("says so when nothing is waiting", () => {
    renderQueue([]);
    expect(screen.getByTestId("identity-queue-empty")).toBeTruthy();
  });

  it("offers each side as a way into the full profile", () => {
    const onOpenPerson = vi.fn();
    renderQueue([PROPOSAL], onOpenPerson);

    fireEvent.click(screen.getByTestId("merge-side-12"));
    expect(onOpenPerson).toHaveBeenCalledWith(12);
  });
});

describe("saying yes", () => {
  it("merges on one click and points at the undo", async () => {
    const calls = installFetch(() => ({
      body: { ok: true, queue_id: 7, merge_id: 42 },
    }));
    renderQueue();

    fireEvent.click(screen.getByTestId("merge-confirm-7"));

    await waitFor(() => {
      expect(screen.getByTestId("merge-confirmed-7")).toBeTruthy();
    });
    expect(calls).toEqual([
      { url: "/api/ultrawiki/identity/queue/7/confirm", method: "POST" },
    ]);
    expect(screen.getByTestId("merge-confirmed-7").textContent).toContain("undo");
  });

  it("does not promise an undo that does not exist", async () => {
    // merge_id 0 = the pair had already become one entity by other evidence.
    // There is no merge row, so "you can undo this" would be a lie.
    installFetch(() => ({ body: { ok: true, queue_id: 7, merge_id: 0 } }));
    renderQueue();

    fireEvent.click(screen.getByTestId("merge-confirm-7"));

    await waitFor(() => {
      expect(screen.getByTestId("merge-confirmed-7").textContent).toContain(
        "already one person",
      );
    });
  });
});

describe("saying no", () => {
  it("does not reject on the first click — it warns first", () => {
    const calls = installFetch(() => ({ body: { ok: true } }));
    renderQueue();

    fireEvent.click(screen.getByTestId("merge-reject-7"));

    expect(screen.getByTestId("merge-reject-armed-7")).toBeTruthy();
    expect(screen.getByTestId("merge-reject-armed-7").textContent).toContain(
      "permanent",
    );
    expect(calls).toEqual([]);
  });

  it("rejects once the warning has been read and confirmed", async () => {
    const calls = installFetch(() => ({
      body: { ok: true, queue_id: 7, status: "rejected" },
    }));
    renderQueue();

    fireEvent.click(screen.getByTestId("merge-reject-7"));
    fireEvent.click(screen.getByTestId("merge-reject-confirm-7"));

    await waitFor(() => {
      expect(calls).toEqual([
        { url: "/api/ultrawiki/identity/queue/7/reject", method: "POST" },
      ]);
    });
  });

  it("lets the user back out of the armed state without deciding", () => {
    const calls = installFetch(() => ({ body: { ok: true } }));
    renderQueue();

    fireEvent.click(screen.getByTestId("merge-reject-7"));
    fireEvent.click(screen.getByTestId("merge-reject-cancel-7"));

    expect(screen.queryByTestId("merge-reject-armed-7")).toBeNull();
    expect(screen.getByTestId("merge-confirm-7")).toBeTruthy();
    expect(calls).toEqual([]);
  });
});

describe("a refusal from the store", () => {
  it("prints the reason it was given, word for word", async () => {
    installFetch(() => ({
      status: 409,
      body: { detail: "queue row 7 was already decided" },
    }));
    renderQueue();

    fireEvent.click(screen.getByTestId("merge-confirm-7"));

    const error = await screen.findByTestId("merge-error-7");
    expect(error.textContent).toContain("queue row 7 was already decided");
  });
});
