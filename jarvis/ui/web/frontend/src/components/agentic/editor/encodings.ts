import { fill, translate } from "@/i18n";

/**
 * The text encodings the editor offers for "Reopen with" and "Save with".
 *
 * Ids are the Python codec names the backend reports and accepts
 * (`codecs.lookup(name).name`), so a detected encoding finds its label here.
 */
export interface EncodingOption {
  id: string;
  /** The standard name, shown as is ("UTF-8", "Windows 1252"). */
  name: string;
  /**
   * The script or region it covers, as an i18n key under
   * `code_editor.encoding_scripts`; the label then reads "Western (Windows 1252)".
   */
  script?: string;
}

export const ENCODINGS: readonly EncodingOption[] = [
  { id: "utf-8", name: "UTF-8" },
  { id: "utf-8-sig", name: "UTF-8 BOM", script: "with_bom" },
  { id: "utf-16-le", name: "UTF-16 LE" },
  { id: "utf-16-be", name: "UTF-16 BE" },
  { id: "cp1252", name: "Windows 1252", script: "western" },
  { id: "iso8859-1", name: "ISO 8859-1", script: "western" },
  { id: "iso8859-15", name: "ISO 8859-15", script: "western" },
  { id: "cp1250", name: "Windows 1250", script: "central_european" },
  { id: "cp1251", name: "Windows 1251", script: "cyrillic" },
  { id: "koi8-r", name: "KOI8-R", script: "cyrillic" },
  { id: "cp1253", name: "Windows 1253", script: "greek" },
  { id: "cp1254", name: "Windows 1254", script: "turkish" },
  { id: "shift_jis", name: "Shift JIS", script: "japanese" },
  { id: "euc_jp", name: "EUC-JP", script: "japanese" },
  { id: "gb18030", name: "GB 18030", script: "chinese_simplified" },
  { id: "big5", name: "Big5", script: "chinese_traditional" },
  { id: "euc_kr", name: "EUC-KR", script: "korean" },
];

/** The menu label of an encoding, in the UI language; translated when called. */
export function encodingOptionLabel(entry: EncodingOption): string {
  if (!entry.script) return entry.name;
  if (entry.script === "with_bom") return translate("code_editor.encoding_scripts.utf8_bom");
  return fill(translate("code_editor.encoding_scripts.labelled"), {
    script: translate(`code_editor.encoding_scripts.${entry.script}`),
    encoding: entry.name,
  });
}

/** A short status-bar label for an encoding id. */
export function encodingLabel(id: string): string {
  const entry = ENCODINGS.find((option) => option.id === id);
  return entry ? encodingOptionLabel(entry) : id.toUpperCase();
}
