/**
 * Keeps the Verse's level state in step with the server while the map is
 * open, and reports the world actions the server meters.
 *
 * - First read on mount (with the level-ups missed while the map was closed),
 *   then a slow jittered re-read as a safety net (AP-33); live awards arrive
 *   as `ProgressionAwarded` pushes over the existing WebSocket.
 * - Daily visit and each floor discovered, a pet or a treat for the office
 *   dog, a team gathered at the table: reported once each, the server
 *   decides whether they pay.
 */
import { useEffect, useRef } from "react";
import { useEventStore } from "@/store/events";
import { useCompanionPet } from "../companion/companionPetStore";
import { useOfficeDog } from "../office/dogLife";
import { useOfficeStore, type OfficeFloor } from "../office/officeStore";
import { fetchProgression, parseAwardEvent, reportWorldAction } from "./progressionApi";
import { awayCelebrations, readSeenSeq, useProgression, writeSeenSeq } from "./progressionStore";
import { playLevelUp, playXpTick } from "./levelSounds";

const RESYNC_MS = 60_000;
const RESYNC_JITTER_MS = 15_000;
/** Ticks for "+XP" closer together than this are one sound. */
const TICK_GAP_MS = 400;

export function useProgressionSync(awake: boolean, floor: OfficeFloor): void {
  const seen = useRef<number>(0);

  // First read, then the slow safety net while the map is on screen.
  useEffect(() => {
    if (!awake) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async (first: boolean) => {
      try {
        const snapshot = await fetchProgression();
        if (cancelled) return;
        const marker = readSeenSeq();
        useProgression.getState().hydrate(snapshot, first ? awayCelebrations(snapshot, marker) : []);
        seen.current = Math.max(seen.current, snapshot.latestSeq);
        writeSeenSeq(seen.current);
      } catch (error) {
        // The Verse plays on without levels; the next read tries again.
        console.debug("Verse levels unavailable", error);
      }
      if (!cancelled) timer = setTimeout(() => void load(false), RESYNC_MS + Math.random() * RESYNC_JITTER_MS);
    };
    void load(true);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [awake]);

  // Live awards from the bus.
  useEffect(() => {
    let lastId: string | null = useEventStore.getState().events[0]?.id ?? null;
    let lastTick = 0;
    return useEventStore.subscribe((state) => {
      const fresh = [];
      for (const event of state.events) {
        if (event.id === lastId) break;
        fresh.push(event);
      }
      lastId = state.events[0]?.id ?? lastId;
      for (const event of fresh.reverse()) {
        if (event.name !== "ProgressionAwarded") continue;
        const award = parseAwardEvent(event.payload);
        if (!award) continue;
        useProgression.getState().apply(award);
        if (award.seq > seen.current) { seen.current = award.seq; writeSeenSeq(award.seq); }
        if (award.level > award.previousLevel) playLevelUp(award.kind !== "agent");
        else if (award.kind !== "agent" && performance.now() - lastTick > TICK_GAP_MS) { lastTick = performance.now(); playXpTick(); }
      }
    });
  }, []);

  // The pet that earns is the one in My Pets right now.
  const petId = useCompanionPet((s) => s.pet.id);
  useEffect(() => { useProgression.getState().setPetId(petId); }, [petId]);

  // The day's first visit and every floor discovered.
  useEffect(() => { if (awake) void reportWorldAction("daily_visit"); }, [awake]);
  useEffect(() => { if (awake) void reportWorldAction("floor_discovered", floor); }, [awake, floor]);

  // The office dog: a pat, a treat.
  useEffect(() => useOfficeDog.subscribe((state, prev) => {
    if (state.petSeq !== prev.petSeq) void reportWorldAction("dog_petted");
    if (state.treatSeq !== prev.treatSeq) void reportWorldAction("dog_treat");
  }), []);

  // A team gathered at the team-room table.
  useEffect(() => useOfficeStore.subscribe((state, prev) => {
    if (state.meeting && state.meeting.groupId !== prev.meeting?.groupId) void reportWorldAction("team_meeting");
  }), []);
}
