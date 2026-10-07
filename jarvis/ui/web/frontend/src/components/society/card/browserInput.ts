/** Map the displayed object-fit image, excluding letterboxing, into captured pixels. */
export function browserPoint(canvas: HTMLCanvasElement, clientX: number, clientY: number) {
  const box = canvas.getBoundingClientRect();
  const { width, height } = canvas;
  if (![box.width, box.height, width, height].every((v) => Number.isFinite(v) && v > 0)) return null;
  const scale = Math.min(box.width / width, box.height / height);
  const x = (clientX - box.left - (box.width - width * scale) / 2) / scale;
  const y = (clientY - box.top - (box.height - height * scale) / 2) / scale;
  if (![x, y].every(Number.isFinite) || x < 0 || y < 0 || x >= width || y >= height) return null;
  const geometry = canvas.dataset.browserGeometryId;
  return { x, y, ...(geometry ? { geometry_id: geometry } : {}) };
}
