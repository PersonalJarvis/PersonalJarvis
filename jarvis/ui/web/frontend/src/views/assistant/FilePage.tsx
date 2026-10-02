/**
 * One of the assistant's files, opened in place with a way back.
 *
 * SOUL.md and the standing instructions are the person's to write: the file
 * in an editor that scrolls inside itself, and under it one line — what
 * saving does on the left, Revert and Save on the right. MEMORY.md and
 * USER.md are written by the assistant itself: they open as their notes,
 * each of which can be forgotten.
 */
import { ArrowLeft } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  useSaveInstructions,
  useSaveSoul,
  type CharacterFile,
  type InstructionsFile,
  type NotebookFile,
  type SoulFile,
} from "@/views/assistant/api";
import { useFileMeta } from "@/views/assistant/FilesSection";
import { NoteList } from "@/views/assistant/MemorySection";
import { TextAction } from "@/views/assistant/Section";

function Editor({ file }: { file: CharacterFile | InstructionsFile }) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const saveSoul = useSaveSoul();
  const saveInstructions = useSaveInstructions();
  const [draft, setDraft] = useState(file.content);

  // A save (or a write from elsewhere) brings new content: take it.
  useEffect(() => setDraft(file.content), [file.content]);

  const isSoul = file.id === "soul";
  const saving = saveSoul.isPending || saveInstructions.isPending;
  const dirty = draft !== file.content;
  const template = file.id === "instructions" ? file.template : "";
  // SOUL.md can never be saved empty; the instructions file is cleared that way.
  const canSave = dirty && !saving && (!isSoul || draft.trim().length > 0);

  const onSave = () => {
    const done = {
      onSuccess: () =>
        pushToast("success", t(!isSoul && !draft.trim() ? "assistant_view.cleared" : "assistant_view.saved")),
      onError: (e: Error) => pushToast("error", e.message),
    };
    if (isSoul) saveSoul.mutate(draft, done);
    else saveInstructions.mutate(draft, done);
  };

  return (
    <div className="flex flex-col gap-4">
      <label className="sr-only" htmlFor="assistant-file-editor">
        {t("assistant_view.editor_label").replace("{0}", file.filename)}
      </label>
      <textarea
        id="assistant-file-editor"
        data-testid="assistant-file-editor"
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          if ((e.ctrlKey || e.metaKey) && e.key === "s") {
            e.preventDefault();
            if (canSave) onSave();
          }
        }}
        spellCheck={false}
        placeholder={t(isSoul ? "assistant_view.soul_placeholder" : "assistant_view.instructions_placeholder")}
        className="h-[42vh] min-h-[280px] w-full resize-none rounded-xl border border-border bg-card px-6 py-5 font-mono text-sm leading-6 text-foreground placeholder:text-foreground-faint transition-colors focus-visible:border-border-strong focus-visible:outline-none"
      />
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <p className="min-w-0 max-w-[34rem] text-sm text-muted-foreground">
          {dirty ? (
            <span className="text-foreground">{t("assistant_view.unsaved")}</span>
          ) : (
            t(isSoul ? "assistant_view.soul_note" : "assistant_view.instructions_note")
          )}
        </p>
        <div className="flex items-center gap-5">
          {template && !draft.trim() && (
            <TextAction onClick={() => setDraft(template)}>{t("assistant_view.load_template")}</TextAction>
          )}
          {!isSoul && file.exists && draft.trim() && !dirty && (
            <TextAction onClick={() => setDraft("")}>{t("assistant_view.clear")}</TextAction>
          )}
          <TextAction disabled={!dirty || saving} onClick={() => setDraft(file.content)}>
            {t("assistant_view.revert")}
          </TextAction>
          <Button type="button" size="sm" disabled={!canSave} onClick={onSave} data-testid="assistant-file-save">
            {saving ? t("assistant_view.saving") : t("assistant_view.save")}
          </Button>
        </div>
      </div>
    </div>
  );
}

function isNotebook(file: SoulFile): file is NotebookFile {
  return file.id === "memory" || file.id === "user";
}

export function FilePage({
  file,
  name,
  canForget,
  onBack,
}: {
  file: SoulFile;
  name: string;
  canForget: boolean;
  onBack: () => void;
}) {
  const t = useT();
  const meta = useFileMeta();

  let body: ReactNode;
  if (isNotebook(file)) {
    body = (
      <>
        <NoteList
          target={file.id}
          entries={file.entries}
          canForget={canForget}
          emptyText={t(`assistant_view.empty_${file.id}`)}
        />
        <p className="mt-4 text-sm text-muted-foreground">{t("assistant_view.notebook_note")}</p>
      </>
    );
  } else if (file.exists || file.id === "instructions") {
    body = <Editor key={file.id} file={file} />;
  } else {
    body = <p className="text-base text-muted-foreground">{t("assistant_view.character_missing")}</p>;
  }

  return (
    <div data-testid={`assistant-page-${file.id}`} className="flex flex-col">
      <TextAction onClick={onBack} data-testid="assistant-back" className="inline-flex items-center gap-1.5 self-start">
        <ArrowLeft aria-hidden className="h-4 w-4" />
        {name}
      </TextAction>
      <div className="mb-6 mt-5 flex items-end justify-between gap-6 border-b border-border pb-5">
        <div className="min-w-0">
          <h2 className="truncate text-xl font-semibold text-foreground-strong">{file.filename}</h2>
          <p className="mt-1 text-base text-muted-foreground">{t(`assistant_view.file_role_${file.id}`)}</p>
        </div>
        <span className="shrink-0 text-sm tabular-nums text-foreground-faint">{meta(file)}</span>
      </div>
      {body}
    </div>
  );
}
