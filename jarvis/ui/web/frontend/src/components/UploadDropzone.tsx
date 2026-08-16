import { useRef } from "react";
import { FileArchive, FolderOpen, Upload } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { filesFromInput, type PickedFile } from "@/lib/filePicking";

/**
 * The "drop a folder here" surface, shared by every upload flow.
 *
 * Skills and plugins want the same three ways in — drag a folder, pick a
 * folder, hand over an archive — and only differ in the words on the label.
 * Keeping one surface means a fix to any of the three fiddly parts (the
 * `webkitdirectory` attribute React refuses to render, the archive accept
 * list, the drag highlight) lands everywhere at once.
 */

/**
 * The two hidden file inputs plus the handles to open them.
 *
 * They live in a hook rather than inside the dropzone because the caller needs
 * to reopen the folder picker from elsewhere too — a "Replace" button sits
 * next to the file list, long after the dropzone is gone.
 */
export function useUploadInputs(
  onPick: (files: PickedFile[]) => void,
  testIdPrefix: string,
) {
  const folderRef = useRef<HTMLInputElement | null>(null);
  const archiveRef = useRef<HTMLInputElement | null>(null);

  // React does not know `webkitdirectory` and drops it from JSX without a
  // word, so it has to be set on the node itself.
  const setFolderRef = (node: HTMLInputElement | null) => {
    folderRef.current = node;
    if (node) {
      node.setAttribute("webkitdirectory", "");
      node.setAttribute("directory", "");
    }
  };

  const handleChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    onPick(filesFromInput(event.target.files));
    // Cleared so picking the same folder twice in a row still fires a change.
    event.target.value = "";
  };

  const inputs = (
    <>
      <input
        ref={setFolderRef}
        type="file"
        multiple
        className="sr-only"
        data-testid={`${testIdPrefix}-folder-input`}
        onChange={handleChange}
      />
      <input
        ref={archiveRef}
        type="file"
        accept=".zip,.tgz,.tar.gz"
        className="sr-only"
        data-testid={`${testIdPrefix}-archive-input`}
        onChange={handleChange}
      />
    </>
  );

  return {
    inputs,
    openFolder: () => folderRef.current?.click(),
    openArchive: () => archiveRef.current?.click(),
  };
}

export function UploadDropzone({
  dragging,
  title,
  hint,
  folderLabel,
  archiveLabel,
  onChooseFolder,
  onChooseArchive,
}: {
  dragging: boolean;
  title: string;
  hint: string;
  folderLabel: string;
  archiveLabel: string;
  onChooseFolder: () => void;
  onChooseArchive: () => void;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-3 rounded-lg border-2 border-dashed px-6 py-10 text-center transition-colors",
        dragging ? "border-primary bg-primary/5" : "border-border bg-muted/20",
      )}
    >
      <Upload className="h-5 w-5 text-muted-foreground" />
      <div>
        <p className="text-sm font-medium">{title}</p>
        <p className="mt-1 text-xs text-muted-foreground">{hint}</p>
      </div>
      <div className="mt-1 flex items-center gap-2">
        <Button size="sm" variant="outline" onClick={onChooseFolder} className="gap-1.5">
          <FolderOpen className="h-3.5 w-3.5" />
          {folderLabel}
        </Button>
        <Button size="sm" variant="ghost" onClick={onChooseArchive} className="gap-1.5">
          <FileArchive className="h-3.5 w-3.5" />
          {archiveLabel}
        </Button>
      </div>
    </div>
  );
}
