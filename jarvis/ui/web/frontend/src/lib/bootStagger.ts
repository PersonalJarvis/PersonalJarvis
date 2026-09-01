/**
 * First-mount request staggering.
 *
 * Chromium allows ~6 concurrent HTTP/1.1 connections per origin. The shell
 * fires ~15 `/api/*` requests in its first frames, and during a cold boot the
 * serve-first bootstrap HOLDS each of them until the real app registers — so
 * the pool is exhausted, and even the 1 s health poll and lazy-chunk asset
 * fetches queue behind held requests. The window then reads as frozen while it
 * is only waiting (2026-09-01 cold-boot forensics).
 *
 * Non-critical lookups (update badge, marketplace attention, permissions
 * banner, download capabilities) await `bootSettled()` before their first
 * request: the essential burst (config, providers, chats, brain, voice) gets
 * the whole pool, and the long-tail lookups arrive once the app has painted
 * real data. The timer starts on first call — effectively at app mount.
 */

const BOOT_SETTLE_MS = 4_000;

let settled: Promise<void> | null = null;

export function bootSettled(): Promise<void> {
  if (!settled) {
    settled = new Promise((resolve) => {
      window.setTimeout(resolve, BOOT_SETTLE_MS);
    });
  }
  return settled;
}
