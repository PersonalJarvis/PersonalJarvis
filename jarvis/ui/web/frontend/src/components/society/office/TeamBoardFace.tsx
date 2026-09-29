/**
 * The team room's whiteboard shows the real teams: up to four names with their
 * member counts, and the one meeting at the table right now marked "live".
 * Without any team the board keeps its drawn org chart.
 */
import { useEffect, useMemo } from "react";
import { CanvasTexture, SRGBColorSpace } from "three";
import { useT } from "@/i18n";
import { useSocietyChatGroups, type SocietyChatGroup } from "@/lib/societyChatGroups";
import type { Furniture } from "./officeLayout";
import { useOfficeStore } from "./officeStore";

const W = 1024, H = 478;
/** Board face in the teamBoard prop's local space (OfficeProps `TeamBoard`), a hair in front of the drawn panel. */
const FACE = { y: 1.25, z: -0.011, w: 2.4, h: 1.12 };
const MAX_ROWS = 4;
const NOTE_COLOURS = ["#fde68a", "#bbf7d0", "#bfdbfe", "#fbcfe8"];

function clipped(ctx: CanvasRenderingContext2D, text: string, maxWidth: number): string {
  if (ctx.measureText(text).width <= maxWidth) return text;
  let cut = text;
  while (cut.length > 1 && ctx.measureText(`${cut}…`).width > maxWidth) cut = cut.slice(0, -1);
  return `${cut}…`;
}

function drawBoard(ctx: CanvasRenderingContext2D, teams: SocietyChatGroup[], liveId: string | null, labels: { title: string; members: string; live: string }) {
  ctx.fillStyle = "#f4f4f0";
  ctx.fillRect(0, 0, W, H);
  ctx.fillStyle = "#2f3645";
  ctx.font = "700 52px system-ui, sans-serif";
  ctx.textBaseline = "middle";
  ctx.fillText(clipped(ctx, labels.title, W - 100), 50, 58);
  ctx.strokeStyle = "#3b4250";
  ctx.lineWidth = 5;
  ctx.lineCap = "round";
  ctx.beginPath(); ctx.moveTo(50, 100); ctx.lineTo(W - 50, 100); ctx.stroke();
  const rows = teams.slice(0, MAX_ROWS);
  const rowH = (H - 130) / MAX_ROWS;
  rows.forEach((team, i) => {
    const y = 125 + i * rowH + rowH / 2 - 6;
    const live = team.group_id === liveId;
    ctx.fillStyle = NOTE_COLOURS[i % NOTE_COLOURS.length];
    ctx.fillRect(50, y - rowH / 2 + 10, 26, rowH - 20);
    const count = labels.members.replace("{0}", String(team.members.length));
    ctx.font = "500 32px system-ui, sans-serif";
    const countW = ctx.measureText(count).width;
    const liveW = live ? 150 : 0;
    ctx.fillStyle = "#1f2430";
    ctx.font = "600 40px system-ui, sans-serif";
    ctx.fillText(clipped(ctx, team.name, W - 190 - countW - liveW), 100, y);
    ctx.font = "500 32px system-ui, sans-serif";
    ctx.fillStyle = "#5b6475";
    ctx.fillText(count, W - 50 - countW - liveW, y);
    if (live) {
      ctx.fillStyle = "#2f9e5b";
      ctx.beginPath(); ctx.roundRect(W - 180, y - 24, 130, 48, 24); ctx.fill();
      ctx.fillStyle = "#ffffff";
      ctx.font = "700 28px system-ui, sans-serif";
      ctx.fillText(clipped(ctx, labels.live, 100), W - 162, y + 1);
    }
  });
}

export function TeamBoardFace({ board, enabled }: { board: Furniture; enabled: boolean }) {
  const t = useT();
  const groups = useSocietyChatGroups(enabled);
  const liveId = useOfficeStore((s) => (s.meeting && s.meeting.untilMs > Date.now() ? s.meeting.groupId : null));
  const canvas = useMemo(() => {
    const el = typeof document === "undefined" ? null : document.createElement("canvas");
    if (el) { el.width = W; el.height = H; }
    return el;
  }, []);
  const texture = useMemo(() => {
    if (!canvas) return null;
    const tex = new CanvasTexture(canvas);
    tex.colorSpace = SRGBColorSpace;
    tex.anisotropy = 4;
    return tex;
  }, [canvas]);
  useEffect(() => () => texture?.dispose(), [texture]);
  const teams = useMemo(() => [...(groups.data ?? [])].sort((a, b) => b.updated_ms - a.updated_ms), [groups.data]);
  const labels = { title: t("society.office.team_yours"), members: t("society.office.team_members"), live: t("society.office.team_live") };
  const signature = JSON.stringify([teams.map((g) => [g.group_id, g.name, g.members.length]), liveId, labels]);
  useEffect(() => {
    const ctx = canvas?.getContext("2d");
    if (!ctx || !texture) return;
    drawBoard(ctx, teams, liveId, labels);
    texture.needsUpdate = true;
    // `signature` covers teams, liveId and labels.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [signature, canvas, texture]);
  if (!texture || teams.length === 0) return null;
  return (
    <group position={[board.x, 0, board.z]} rotation={[0, board.rotationY, 0]}>
      <mesh position={[0, FACE.y, FACE.z]}>
        <planeGeometry args={[FACE.w, FACE.h]} />
        <meshStandardMaterial map={texture} roughness={0.7} />
      </mesh>
    </group>
  );
}
