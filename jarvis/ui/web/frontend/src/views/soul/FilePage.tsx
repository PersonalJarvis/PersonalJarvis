/**
 * One of the assistant's files, opened in place with a back link.
 *
 * SOUL.md and the standing instructions are the person's to write: one card
 * with the editor (it scrolls inside itself) and the action row under it. MEMORY.md and
 * USER.md are written by the assistant itself: they open as their notes, each
 * of which can be forgotten, with one line saying how to change them.
 */
import { ArrowLeft, Eraser, FilePlus2, Info, RotateCcw, Save } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useT, useUiLanguage } from "@/i18n";
import { useEventStore } from "@/store/events";
import {
  useSaveInstructions,
  useSaveSoul,
  type CharacterFile,
  type InstructionsFile,
  type NotebookFile,
  type SoulFile,
} from "@/views/soul/api";
import { FileIcon, useFileMeta } from "@/views/soul/FileList";
import { NoteList } from "@/views/soul/NotesGroup";

function Header({ file }: { file: SoulFile }) {
  const t = useT();
  const meta = useFileMeta();
  return (
    <div className="flex items-center gap-4 px-1">
      <FileIcon id={file.id} size="lg" />
      <div className="min-w-0 flex-1">
        <h2 className="truncate font-mono text-lg font-semibold text-foreground-strong">{file.filename}</h2>
        <p className="truncate text-base text-muted-foreground">{t(`soul_view.file_role_${file.id}`)}</p>
      </div>
      <span className="hidden shrink-0 text-sm tabular-nums text-foreground-faint sm:block">{meta(file)}</span>
    </div>
  );
}

function Note({ children }: { children: string }) {
  return (
    <p className="flex items-start gap-2 px-1 text-sm text-muted-foreground">
      <Info aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

function Editor({ file }: { file: CharacterFile | InstructionsFile }) {
  const t = useT();
  const lang = useUiLanguage();
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
        pushToast(
          "success",
          t(!isSoul && !draft.trim() ? "soul_view.cleared" : "soul_view.saved"),
        ),
      onError: (e: Error) => pushToast("error", e.message),
    };
    if (isSoul) saveSoul.mutate(draft, done);
    else saveInstructions.mutate(draft, done);
  };

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-col rounded-xl border border-border bg-card">
        <label className="sr-only" htmlFor="soul-file-editor">
          {t("soul_view.editor_label").replace("{0}", file.filename)}
        </label>
        <Textarea
          id="soul-file-editor"
          data-testid="soul-file-editor"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === "s") {
              e.preventDefault();
              if (canSave) onSave();
            }
          }}
          spellCheck={false}
          placeholder={t(isSoul ? "soul_view.soul_placeholder" : "soul_view.instructions_placeholder")}
          className="h-[46vh] min-h-[300px] resize-none rounded-none rounded-t-xl border-0 bg-card p-5 font-mono text-sm leading-6 focus-visible:ring-0 focus-visible:ring-offset-0"
        />
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-b-xl border-t border-border bg-card px-5 py-3">
          <span className="text-sm tabular-nums text-foreground-faint">
            {dirty ? t("soul_view.unsaved") : t("soul_view.chars").replace("{0}", draft.length.toLocaleString(lang))}
          </span>
          <div className="flex items-center gap-2">
            {template && !draft.trim() && (
              <Button type="button" variant="ghost" onClick={() => setDraft(template)}>
                <FilePlus2 aria-hidden />
                {t("soul_view.load_template")}
              </Button>
            )}
            {!isSoul && file.exists && draft.trim() && !dirty && (
              <Button type="button" variant="ghost" onClick={() => setDraft("")}>
                <Eraser aria-hidden />
                {t("soul_view.clear")}
              </Button>
            )}
            <Button type="button" variant="ghost" disabled={!dirty || saving} onClick={() => setDraft(file.content)}>
              <RotateCcw aria-hidden />
              {t("soul_view.revert")}
            </Button>
            <Button type="button" disabled={!canSave} onClick={onSave} data-testid="soul-file-save">
              <Save aria-hidden />
              {saving ? t("soul_view.saving") : t("soul_view.save")}
            </Button>
          </div>
        </div>
      </div>
      <Note>{t(isSoul ? "soul_view.soul_note" : "soul_view.instructions_note")}</Note>
    </div>
  );
}

function Notebook({ file, canForget }: { file: NotebookFile; canForget: boolean }) {
  const t = useT();
  return (
    <div className="flex flex-col gap-3">
      <div className="overflow-hidden rounded-xl border border-border bg-card">
        <NoteList
          target={file.id}
          entries={file.entries}
          canForget={canForget}
          emptyText={t(`soul_view.empty_${file.id}`)}
        />
      </div>
      <Note>{t("soul_view.notebook_note")}</Note>
    </div>
  );
}

function isNotebook(file: SoulFile): file is NotebookFile {
  return file.id === "memory" || file.id === "user";
}

export function FilePage({
  file,
  canForget,
  onBack,
}: {
  file: SoulFile;
  canForget: boolean;
  onBack: () => void;
}) {
  const t = useT();
  let body: ReactNode;
  if (isNotebook(file)) {
    body = <Notebook file={file} canForget={canForget} />;
  } else if (file.exists || file.id === "instructions") {
    body = <Editor key={file.id} file={file} />;
  } else {
    body = <p className="px-1 text-base text-muted-foreground">{t("soul_view.character_missing")}</p>;
  }
  return (
    <div data-testid={`soul-page-${file.id}`} className="flex flex-col gap-5">
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="self-start text-muted-foreground"
        onClick={onBack}
        data-testid="soul-back"
      >
        <ArrowLeft aria-hidden />
        {t("soul_view.back")}
      </Button>
      <Header file={file} />
      {body}
    </div>
  );
}
