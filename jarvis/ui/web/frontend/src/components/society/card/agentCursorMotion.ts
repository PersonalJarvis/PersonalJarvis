/**
 * Travel time for the agent cursor.
 *
 * A short hop stays on screen long enough to see (never a one-frame jump)
 * and a move across the whole picture still lands within about a third of
 * a second. The ease settles onto the button instead of stopping dead.
 */
export const GLIDE_MIN_MS = 130;
export const GLIDE_MAX_MS = 340;

export interface CursorClick {
  id: number;
  x: number;
  y: number;
}

export interface CursorMotion {
  x: number;
  y: number;
  fromX: number;
  fromY: number;
  toX: number;
  toY: number;
  start: number;
  duration: number;
  clickId: number;
  pendingClick: CursorClick | null;
}

export interface CursorTarget {
  x: number;
  y: number;
  clickId: number;
  clickX: number;
  clickY: number;
}

export interface AimResult {
  motion: CursorMotion;
  changed: boolean;
  releaseClick: CursorClick | null;
}

export function glideDurationMs(distancePx: number): number {
  if (!Number.isFinite(distancePx) || distancePx < 1) return 0;
  return Math.min(GLIDE_MAX_MS, Math.max(GLIDE_MIN_MS, 110 + distancePx * 0.28));
}

export function easeOutCubic(t: number): number {
  const clamped = Math.min(1, Math.max(0, t));
  const remaining = 1 - clamped;
  return 1 - remaining * remaining * remaining;
}

export function projectFramePoint(
  frameX: number,
  frameY: number,
  frameW: number,
  frameH: number,
  hostW: number,
  hostH: number,
): { x: number; y: number; scale: number } {
  if (frameW <= 0 || frameH <= 0 || hostW <= 0 || hostH <= 0) {
    return { x: frameX, y: frameY, scale: 1 };
  }
  const scale = Math.min(hostW / frameW, hostH / frameH);
  return {
    x: (hostW - frameW * scale) / 2 + frameX * scale,
    y: (hostH - frameH * scale) / 2 + frameY * scale,
    scale,
  };
}

export function beginCursor(x: number, y: number, now: number): CursorMotion {
  return {
    x,
    y,
    fromX: x,
    fromY: y,
    toX: x,
    toY: y,
    start: now,
    duration: 0,
    clickId: 0,
    pendingClick: null,
  };
}

function close(a: number, b: number): boolean {
  return Math.abs(a - b) < 0.5;
}

export function aimCursor(
  motion: CursorMotion,
  target: CursorTarget,
  distancePx: number,
  now: number,
  reducedMotion: boolean,
): AimResult {
  const samePoint = close(motion.toX, target.x) && close(motion.toY, target.y);
  const sameClick = motion.clickId === target.clickId;
  if (samePoint && sameClick) return { motion, changed: false, releaseClick: null };

  let releaseClick: CursorClick | null = null;
  let pending = motion.pendingClick;
  if (!samePoint && pending) {
    const headingToClick = close(target.x, pending.x) && close(target.y, pending.y);
    if (!headingToClick) {
      releaseClick = pending;
      pending = null;
    }
  }

  let next: CursorMotion = { ...motion, clickId: target.clickId, pendingClick: pending };
  if (!samePoint) {
    const duration = reducedMotion ? 0 : glideDurationMs(distancePx);
    const snap = reducedMotion || duration === 0;
    next = {
      ...next,
      fromX: motion.x,
      fromY: motion.y,
      toX: target.x,
      toY: target.y,
      start: now,
      duration,
      x: snap ? target.x : motion.x,
      y: snap ? target.y : motion.y,
    };
  }
  if (!sameClick && target.clickId > motion.clickId && target.clickId > 0) {
    next = {
      ...next,
      pendingClick: { id: target.clickId, x: target.clickX, y: target.clickY },
    };
  }
  return { motion: next, changed: true, releaseClick };
}

export function hasArrived(x: number, y: number, targetX: number, targetY: number): boolean {
  const dx = x - targetX;
  const dy = y - targetY;
  return dx * dx + dy * dy <= 2.5 * 2.5;
}

export function stepCursor(motion: CursorMotion, now: number): {
  motion: CursorMotion;
  arrivedClick: CursorClick | null;
  moving: boolean;
} {
  const t = motion.duration <= 0
    ? 1
    : Math.min(1, Math.max(0, (now - motion.start) / motion.duration));
  const eased = easeOutCubic(t);
  const x = motion.fromX + (motion.toX - motion.fromX) * eased;
  const y = motion.fromY + (motion.toY - motion.fromY) * eased;
  let pending = motion.pendingClick;
  let arrivedClick: CursorClick | null = null;
  if (pending && hasArrived(x, y, pending.x, pending.y)) {
    arrivedClick = pending;
    pending = null;
  }
  return {
    motion: { ...motion, x, y, pendingClick: pending },
    arrivedClick,
    moving: t < 1,
  };
}
