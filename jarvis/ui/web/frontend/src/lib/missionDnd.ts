/**
 * Dragging a run toward the Jarvis dock — the payload and the drag start.
 *
 * Lived in `views/OutputsView.tsx` until the Outputs section folded into the
 * Artifacts section (2026-08-23). The dock (`components/JarvisDock.tsx`) reads
 * the same MIME type; both sides import it from here so the two can never
 * drift apart.
 */
import type { DragEvent } from "react";

import { applyMissionDragImage } from "@/lib/missionDragImage";
import { useMissionDrag } from "@/store/missionDrag";
import type { OutputSummary } from "@/hooks/useOutputs";

/** MIME type carrying a mission reference between a rail row and the dock. */
export const MISSION_DND_MIME = "application/x-jarvis-mission";

/** The subset of a run the drag carries to the dock/server. */
export type MissionDragMeta = Pick<
  OutputSummary,
  "slug" | "utterance" | "status" | "summary" | "error" | "mission_id"
>;

/** Serialise the fields the dock/server need from a dragged run. */
export function buildMissionDragPayload(meta: MissionDragMeta): string {
  return JSON.stringify({
    slug: meta.slug,
    utterance: meta.utterance ?? "",
    status: meta.status ?? "unknown",
    summary: meta.summary ?? "",
    error: meta.error ?? "",
    mission_id: meta.mission_id ?? null,
  });
}

/**
 * Begin dragging a run toward the Jarvis dock. Writes the payload, swaps the
 * giant native drag ghost for a compact branded chip, and flags the drag
 * globally so the dock blooms into a big, forgiving target.
 */
export function startMissionDrag(e: DragEvent, meta: MissionDragMeta): void {
  e.dataTransfer.setData(MISSION_DND_MIME, buildMissionDragPayload(meta));
  e.dataTransfer.effectAllowed = "copy";
  applyMissionDragImage(e.dataTransfer, meta.utterance || meta.slug);
  useMissionDrag.getState().begin();
}

/** The drag ended (dropped or cancelled) — let the dock shrink back. */
export function endMissionDrag(): void {
  useMissionDrag.getState().end();
}
