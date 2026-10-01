/** Shared in-flight history reads, never a completed-data or credential cache. */
export function createHistoryRequests<T>() {
  const pending = new Map<string, { revision: number; promise: Promise<T> }>();

  return {
    read(key: string, load: (signal: AbortSignal) => Promise<T>): Promise<T> {
      const existing = pending.get(key);
      if (existing) return existing.promise;
      const entry = { revision: 0, promise: null as unknown as Promise<T> };
      entry.promise = (async () => {
        // A mutation that finishes during a read invalidates that response.
        // Allow one serial catch-up read. Continuous mutations must hand the
        // next attempt back to the budgeted poll instead of creating a loop
        // of immediate requests, and known-stale data must never be published.
        for (let attempt = 0; attempt < 2; attempt += 1) {
          const revision = entry.revision;
          const controller = new AbortController();
          let timeout: ReturnType<typeof setTimeout> | undefined;
          const deadline = new Promise<never>((_resolve, reject) => {
            timeout = setTimeout(() => {
              // Settle even if an auth/fetch wrapper ignores AbortSignal.
              // Native fetch is also cancelled, so its connection can close.
              reject(new DOMException("History request timed out.", "TimeoutError"));
              controller.abort();
            }, 10_000);
          });
          try {
            const value = await Promise.race([load(controller.signal), deadline]);
            if (revision === entry.revision) return value;
          } finally {
            clearTimeout(timeout);
          }
        }
        throw new Error("History changed during refresh; retry on the next poll.");
      })().finally(() => {
        if (pending.get(key) === entry) pending.delete(key);
      });
      pending.set(key, entry);
      return entry.promise;
    },
    invalidate(): void {
      for (const entry of pending.values()) entry.revision += 1;
    },
  };
}

function sameFields(a: object, b: object): boolean {
  const left = a as Record<string, unknown>;
  const right = b as Record<string, unknown>;
  const keys = Object.keys(left);
  return keys.length === Object.keys(right).length && keys.every((key) => {
    if (!Object.prototype.hasOwnProperty.call(right, key)) return false;
    const before = left[key];
    const after = right[key];
    return Object.is(before, after) || (
      Array.isArray(before) && Array.isArray(after)
      && before.length === after.length && before.every((value, index) => Object.is(value, after[index]))
    );
  });
}

/** History rows contain scalar fields and, for sessions, approval-id arrays. */
export function reuseHistoryRows<T extends object>(
  previous: T[], next: T[], key: (row: T) => string,
): T[] {
  const old = new Map(previous.map((row) => [key(row), row]));
  const rows = next.map((row) => {
    const before = old.get(key(row));
    return before && sameFields(before, row) ? before : row;
  });
  return rows.length === previous.length && rows.every((row, index) => row === previous[index])
    ? previous : rows;
}
