import {
  forwardRef,
  useEffect,
  useImperativeHandle,
  useRef,
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { cn } from "@/lib/utils";
import { choiceLookup, choiceToken } from "./composerChips";
import type { ToolChoice } from "./toolChoices";
import { toolIdentity, toolIdentityStyle } from "./toolIdentity";
import "./toolIdentity.css";

export interface ComposerDraft {
  text: string;
  choices: ToolChoice[];
  caret: number;
}

export interface ComposerChipFieldHandle {
  insertChip: (row: ToolChoice) => void;
  insertText: (text: string) => void;
  hydrate: (text: string, choices?: ToolChoice[]) => void;
  getDraft: () => ComposerDraft;
  clear: () => void;
  focus: () => void;
  setText: (text: string) => void;
}

function readChoice(node: HTMLElement): ToolChoice | null {
  const raw = node.dataset.choice;
  if (!raw) return null;
  try {
    return JSON.parse(decodeURIComponent(raw)) as ToolChoice;
  } catch {
    return null;
  }
}

function createChipEl(row: ToolChoice): HTMLSpanElement {
  const identity = toolIdentity(row);
  const el = document.createElement("span");
  el.contentEditable = "false";
  el.className = "tool-identity tool-choice-chip";
  el.dataset.inline = "true";
  el.dataset.toolChip = "true";
  el.dataset.toolId = row.id;
  el.dataset.brand = identity.key ?? row.category;
  el.dataset.choice = encodeURIComponent(JSON.stringify(row));
  el.title = row.description || row.label;
  Object.assign(el.style, toolIdentityStyle(row));

  const icon = document.createElement("span");
  icon.className = "tool-choice-icon";
  icon.style.width = "16px";
  icon.style.height = "16px";
  icon.setAttribute("aria-hidden", "true");
  if (identity.logo && identity.mark === "mono") {
    const mask = document.createElement("span");
    mask.className = "tool-choice-mask";
    mask.style.setProperty("--tool-logo", `url("${identity.logo}")`);
    icon.appendChild(mask);
  } else if (identity.logo) {
    const img = document.createElement("img");
    img.src = identity.logo;
    img.alt = "";
    icon.appendChild(img);
  }
  const label = document.createElement("span");
  label.textContent = row.label;
  el.append(icon, label);
  return el;
}

function serialize(root: HTMLElement): ComposerDraft {
  const choices: ToolChoice[] = [];
  let text = "";
  const sel = root.ownerDocument.getSelection();
  let caret = 0;
  let seenCaret = false;

  const walk = (node: Node) => {
    if (!seenCaret && sel && sel.anchorNode === node) {
      caret = text.length + (node.nodeType === Node.TEXT_NODE ? sel.anchorOffset : 0);
      seenCaret = true;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      text += node.textContent ?? "";
      return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE) return;
    const el = node as HTMLElement;
    if (el.dataset.toolChip) {
      const row = readChoice(el);
      if (row) {
        const token = `${choiceToken(row)}`;
        if (!seenCaret && sel && el.contains(sel.anchorNode)) {
          caret = text.length + token.length;
          seenCaret = true;
        }
        text += token;
        choices.push(row);
      }
      return;
    }
    if (el.tagName === "BR") {
      text += "\n";
      return;
    }
    if (el.tagName === "DIV" || el.tagName === "P") {
      if (text && !text.endsWith("\n")) text += "\n";
    }
    for (const child of Array.from(node.childNodes)) walk(child);
  };
  for (const child of Array.from(root.childNodes)) walk(child);
  if (!seenCaret) caret = text.length;
  return { text, choices, caret };
}

function caretRange(root: HTMLElement): Range {
  const sel = root.ownerDocument.getSelection();
  if (sel && sel.rangeCount && root.contains(sel.anchorNode)) {
    return sel.getRangeAt(0);
  }
  const range = root.ownerDocument.createRange();
  range.selectNodeContents(root);
  range.collapse(false);
  return range;
}

function placeAfter(node: Node) {
  const range = node.ownerDocument!.createRange();
  range.setStartAfter(node);
  range.collapse(true);
  const sel = node.ownerDocument!.getSelection();
  sel?.removeAllRanges();
  sel?.addRange(range);
}

function hydrateRoot(root: HTMLElement, text: string, choices: ToolChoice[] = []) {
  root.innerHTML = "";
  const tags = choiceLookup(choices);
  const chunks = text.split(/(@[^\s@]+)/g);
  const doc = root.ownerDocument;
  for (const chunk of chunks) {
    if (!chunk) continue;
    const name = chunk.startsWith("@") ? chunk.slice(1).toLowerCase() : "";
    const row = name ? tags.get(name) : undefined;
    if (row) root.appendChild(createChipEl(row));
    else root.appendChild(doc.createTextNode(chunk));
  }
}

export const ComposerChipField = forwardRef<
  ComposerChipFieldHandle,
  {
    placeholder: string;
    disabled?: boolean;
    autoFocus?: boolean;
    onSubmit: () => void;
    onDraftChange: (draft: ComposerDraft) => void;
    onKeyDown?: (event: KeyboardEvent<HTMLDivElement>) => boolean | void;
    onPasteFiles?: (event: ClipboardEvent<HTMLElement>) => void;
    className?: string;
  }
>(function ComposerChipField(
  { placeholder, disabled, autoFocus, onSubmit, onDraftChange, onKeyDown: onKeyDownProp, onPasteFiles, className },
  ref,
) {
  const elRef = useRef<HTMLDivElement>(null);

  const emit = () => {
    const root = elRef.current;
    if (!root) return;
    onDraftChange(serialize(root));
  };

  const insertNode = (node: Node, afterSpace = true) => {
    const root = elRef.current;
    if (!root) return;
    root.focus();
    const range = caretRange(root);
    range.deleteContents();
    range.insertNode(node);
    if (afterSpace) {
      const space = root.ownerDocument.createTextNode(" ");
      node.parentNode?.insertBefore(space, node.nextSibling);
      placeAfter(space);
    } else {
      placeAfter(node);
    }
    emit();
  };

  useImperativeHandle(ref, () => ({
    insertChip(row) {
      insertNode(createChipEl(row));
    },
    insertText(text) {
      insertNode(elRef.current!.ownerDocument.createTextNode(text), false);
    },
    hydrate(text, choices = []) {
      const root = elRef.current;
      if (!root) return;
      hydrateRoot(root, text, choices);
      emit();
      root.focus();
    },
    getDraft() {
      return elRef.current ? serialize(elRef.current) : { text: "", choices: [], caret: 0 };
    },
    clear() {
      const root = elRef.current;
      if (!root) return;
      root.innerHTML = "";
      emit();
    },
    focus() {
      elRef.current?.focus();
    },
    setText(text) {
      const root = elRef.current;
      if (!root) return;
      root.textContent = text;
      emit();
    },
  }));

  useEffect(() => {
    if (autoFocus) elRef.current?.focus();
  }, [autoFocus]);

  useEffect(() => {
    const root = elRef.current;
    if (!root) return;
    const onSel = () => {
      const sel = root.ownerDocument.getSelection();
      if (sel?.anchorNode && root.contains(sel.anchorNode)) emit();
    };
    root.ownerDocument.addEventListener("selectionchange", onSel);
    return () => root.ownerDocument.removeEventListener("selectionchange", onSel);
  }, []);

  function onInput(_ev: FormEvent<HTMLDivElement>) {
    emit();
  }

  function onKeyDown(ev: KeyboardEvent<HTMLDivElement>) {
    if (onKeyDownProp?.(ev)) return;
    if (ev.defaultPrevented) return;
    if (ev.key === "Enter" && !ev.shiftKey) {
      ev.preventDefault();
      onSubmit();
    }
  }

  function onPaste(ev: ClipboardEvent<HTMLElement>) {
    onPasteFiles?.(ev);
    if (ev.defaultPrevented) return;
    const text = ev.clipboardData.getData("text/plain");
    if (!text) return;
    ev.preventDefault();
    insertNode(ev.currentTarget.ownerDocument.createTextNode(text), false);
  }

  return (
    <div
      ref={elRef}
      role="textbox"
      aria-multiline="true"
      aria-placeholder={placeholder}
      aria-disabled={disabled || undefined}
      contentEditable={!disabled}
      data-placeholder={placeholder}
      data-testid="composer-chip-field"
      data-jarvis-chat-input=""
      suppressContentEditableWarning
      onInput={onInput}
      onKeyDown={onKeyDown}
      onPaste={onPaste}
      className={cn(
        "composer-chip-field min-h-[32px] flex-1 bg-transparent px-1 py-1.5 text-sm leading-relaxed text-foreground outline-none",
        className,
      )}
    />
  );
});
