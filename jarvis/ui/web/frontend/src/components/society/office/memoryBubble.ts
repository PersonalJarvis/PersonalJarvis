/**
 * The memory line in the lead pet's thought bubble: "Updating memory ·
 * MEMORY.md" while a write is pending, "Saved to MEMORY.md" only after the
 * backend confirmed the file on disk, "Couldn't save MEMORY.md" when it
 * failed. Pure, tested in `memoryBubble.test.ts`; the receipts themselves
 * live in `store/memoryWrites.ts`.
 */
import type { MemoryNote, MemoryWritePhase } from "@/store/memoryWrites";

export interface MemoryLabels {
  updating: string; updatingFile: string;
  saved: string; savedFile: string;
  failed: string; failedFile: string;
}

export interface MemoryLine { phase: MemoryWritePhase; text: string }

export function memoryLine(note: MemoryNote, labels: MemoryLabels): MemoryLine {
  const files = note.files.join(", ");
  const [plain, named] = note.phase === "pending"
    ? [labels.updating, labels.updatingFile]
    : note.phase === "saved" ? [labels.saved, labels.savedFile] : [labels.failed, labels.failedFile];
  return { phase: note.phase, text: files ? named.replace("{0}", files) : plain };
}
