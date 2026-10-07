/**
 * The text encodings the editor offers for "Reopen with" and "Save with".
 *
 * Ids are the Python codec names the backend reports and accepts
 * (`codecs.lookup(name).name`), so a detected encoding finds its label here.
 */
export const ENCODINGS: readonly { id: string; label: string }[] = [
  { id: "utf-8", label: "UTF-8" },
  { id: "utf-8-sig", label: "UTF-8 with BOM" },
  { id: "utf-16-le", label: "UTF-16 LE" },
  { id: "utf-16-be", label: "UTF-16 BE" },
  { id: "cp1252", label: "Western (Windows 1252)" },
  { id: "iso8859-1", label: "Western (ISO 8859-1)" },
  { id: "iso8859-15", label: "Western (ISO 8859-15)" },
  { id: "cp1250", label: "Central European (Windows 1250)" },
  { id: "cp1251", label: "Cyrillic (Windows 1251)" },
  { id: "koi8-r", label: "Cyrillic (KOI8-R)" },
  { id: "cp1253", label: "Greek (Windows 1253)" },
  { id: "cp1254", label: "Turkish (Windows 1254)" },
  { id: "shift_jis", label: "Japanese (Shift JIS)" },
  { id: "euc_jp", label: "Japanese (EUC-JP)" },
  { id: "gb18030", label: "Chinese Simplified (GB 18030)" },
  { id: "big5", label: "Chinese Traditional (Big5)" },
  { id: "euc_kr", label: "Korean (EUC-KR)" },
];

/** A short status-bar label for an encoding id. */
export function encodingLabel(id: string): string {
  return ENCODINGS.find((entry) => entry.id === id)?.label ?? id.toUpperCase();
}
