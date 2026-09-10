/** Live city surface: authored districts, physical metro journeys and real work state. */
import {
  Suspense,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { Canvas } from "@react-three/fiber";
import { useCanvasAwake } from "@/hooks/useCanvasAwake";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { useWebglSupported } from "@/lib/graphDimension";
import { useLocaleChunk, useT } from "@/i18n";
import { useEventStore } from "@/store/events";
import { useSocietyRoster } from "../data";
import type { PlaceId } from "../world/islandLayout";
import {
  CAMERA_BOUNDS,
  CITY,
  DISTRICTS,
  checkpointStop,
  district,
  restoreLayout,
  transformPoint,
  type Stop,
} from "./cityModel";
import {
  addCitizen,
  cancelJourney,
  moveBuilding,
  setTarget,
  setTaskActive,
  trainAt,
  type JourneyStage,
} from "./citySimulation";
import { tickCityClock } from "./cityClock";
import { citySessions } from "./citySession";
import { useCityReducedMotion } from "./cityMotion";
import { CityEnvironment } from "./CityEnvironment";
import {
  CityRuntime,
  MovingActor,
  OVERVIEW,
  type CameraFocus,
  type CityActor,
  type CityMetrics,
} from "./CityRuntime";

interface Props {
  topRight?: ReactNode;
  onOpenLedger: () => void;
  onSelectAgent?: (id: string | null) => void;
  onSelectPlace?: (place: PlaceId) => void;
}
const LAYOUT_KEY = "jarvis.city.layout.v2";
function initialLayout() {
  try {
    return restoreLayout(
      JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "null"),
    );
  } catch {
    return restoreLayout(null); /* Private storage uses default city plots. */
  }
}
const DEMO_CHECKPOINT: Record<Stop, string> = {
  west: "meeting",
  east: "hub:cli",
  knowledge: "archive",
  workshop: "hub:skills",
  communications: "hub:comms",
};
const EMPTY_METRICS: CityMetrics = {
  fps: 0,
  calls: 0,
  triangles: 0,
  geometries: 0,
  textures: 0,
};
export function CityStage(props: Props) {
  const ready = useLocaleChunk("society");
  return ready ? (
    <ReadyCity {...props} />
  ) : (
    <div className="h-full animate-pulse bg-secondary" aria-busy="true" />
  );
}
function ReadyCity({
  topRight,
  onOpenLedger,
  onSelectAgent,
  onSelectPlace,
}: Props) {
  const t = useT();
  const setActiveSection = useEventStore((s) => s.setActiveSection);
  const host = useRef<HTMLDivElement>(null);
  const { generation } = useWebglSurface(host);
  const awake = useCanvasAwake(host),
    webgl = useWebglSupported(),
    reduced = useCityReducedMotion();
  const roster = useSocietyRoster();
  const [demo, setDemo] = useState(false);
  const sample = !demo && roster.data?.sample === true;
  const loading = !demo && !roster.data;
  const { sim, clock } = useMemo(() => citySessions.get(demo ? "demo" : sample ? "sample" : "live", () => initialLayout().placements, performance.now()), [demo, sample]);
  const metrics = useRef<CityMetrics>({ ...EMPTY_METRICS }),
    requestFrame = useRef<() => void>(() => undefined);
  const signatures = useRef(new Map<string, string>());
  const [count, setCount] = useState(8),
    [demoStop, setDemoStop] = useState<Stop>("east");
  const [selected, setSelected] = useState<string | null>(null),
    [follow, setFollow] = useState<string | null>(null);
  const [selectedStop, setSelectedStop] = useState<Stop | null>(null),
    [focus, setFocus] = useState<CameraFocus>(OVERVIEW);
  const [interiorStop, setInteriorStop] = useState<Stop | null>(null);
  const [details, setDetails] = useState(false),
    [message, setMessage] = useState<string | null>(null);
  const [version, setVersion] = useState(0),
    [snapshot, setSnapshot] = useState({
      hour: 10,
      time: 0,
      workKey: "",
      debt: 0,
    });
  const demoLabel = t("society.city.reference_agent");
  const actors = useMemo<CityActor[]>(
    () =>
      demo
        ? Array.from({ length: count }, (_, i) => ({
            id: `city-demo-${i}`,
            label: `${demoLabel} ${i + 1}`,
            checkpoint: DEMO_CHECKPOINT[demoStop],
            state: "working",
          }))
        : (roster.data?.agents ?? []).map((a) => ({
            id: a.agentId,
            label: a.name,
            recipe: a.figure,
            checkpoint: a.checkpoint,
            state: a.state,
          })),
    [demo, count, demoStop, roster.data, demoLabel],
  );
  useEffect(() => {
    if (!demo && !roster.data) return; // A pending fetch is not an empty roster.
    const ids = new Set(actors.map((a) => a.id));
    for (const id of sim.citizens.keys())
      if (!ids.has(id)) {
        sim.citizens.delete(id);
        signatures.current.delete(id);
      }
    for (const actor of actors) {
      const created = !sim.citizens.has(actor.id);
      const citizen = addCitizen(sim, actor.id, "west");
      const signature = `${actor.checkpoint}:${actor.state}`;
      if (!created && signatures.current.get(actor.id) === signature) continue;
      signatures.current.set(actor.id, signature);
      const target = checkpointStop(actor.checkpoint);
      if (actor.state === "paused" || !target) {
        setTaskActive(sim, actor.id, false);
        if (citizen.stage !== "arrived")
          cancelJourney(sim, actor.id, citizen.revision + 1);
      } else {
        setTarget(sim, actor.id, target, citizen.revision + 1);
        setTaskActive(sim, actor.id, actor.state === "working");
      }
    }
    setVersion((v) => v + 1);
    requestFrame.current();
  }, [actors, sim, demo, roster.data]);
  useEffect(() => {
    const timer = window.setInterval(() => {
      tickCityClock(clock, sim, performance.now(), reduced);
      setSnapshot({
        hour: clock.hour,
        time: sim.time,
        workKey: [...sim.citizens.values()]
          .filter((c) => c.atWork && c.taskActive)
          .map((c) => c.target)
          .sort()
          .join(","),
        debt: sim.debt,
      });
    }, 250);
    return () => window.clearInterval(timer);
  }, [clock, sim, reduced]);
  const activeStops = useMemo(
    () => new Set(snapshot.workKey.split(",").filter(Boolean) as Stop[]),
    [snapshot.workKey],
  );
  const selectActor = useCallback((id: string) => {
    setSelected(id);
    requestFrame.current();
  }, []);
  const selectStop = useCallback(
    (stop: Stop) => {
      const d = district(stop);
      setSelectedStop(stop);
      setFollow(null);
      setMessage(null);
      setInteriorStop(stop);
      const position = transformPoint(
        sim.placements[stop],
        d.buildingRotation,
        [0, 0, -8],
      );
      setFocus((f) => ({
        position,
        distance: 88,
        yaw: d.buildingRotation + Math.PI + 0.3,
        key: f.key + 1,
      }));
    },
    [sim],
  );
  function overview() {
    setFollow(null);
    setSelectedStop(null);
    setInteriorStop(null);
    setFocus((f) => ({ ...OVERVIEW, key: f.key + 1 }));
  }
  function movePlot(dx: number, dz: number) {
    if (!selectedStop) return;
    const p = sim.placements[selectedStop];
    if (!moveBuilding(sim, selectedStop, [p[0] + dx, p[1], p[2] + dz])) {
      setMessage("invalid_plot");
      return;
    }
    if (demo || sample) { setMessage("demo_plot"); setVersion((v) => v + 1); requestFrame.current(); return; }
    try {
      localStorage.setItem(
        LAYOUT_KEY,
        JSON.stringify({ version: 2, placements: sim.placements }),
      );
      setMessage("saved_plot");
    } catch {
      setMessage("session_plot");
    }
    setVersion((v) => v + 1);
    requestFrame.current();
  }
  const citizen = selected ? sim.citizens.get(selected) : undefined;
  const actor = actors.find((a) => a.id === selected),
    train = trainAt(snapshot.time);
  const button =
    "rounded-md border border-border bg-background px-2.5 py-1.5 text-xs text-foreground transition-colors hover:bg-muted focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring";
  const activeDistrict = selectedStop ? district(selectedStop) : null;
  const stageText = (stage: JourneyStage, workspaceQueue = false) =>
    t(
      workspaceQueue
        ? "society.city.waiting_workspace"
        : `society.city.stage_${stage}`,
    );
  return (
    <div
      className="relative flex h-full min-h-0 flex-col bg-background text-foreground"
      data-testid="city-reference"
      data-version={version}
    >
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-popover p-2">
        <strong className="mr-auto text-sm">{t("society.city.title")}</strong>
        {topRight}
        <button className={button} onClick={overview}>
          {t("society.city.overview")}
        </button>
        <button
          className={button}
          onClick={() => {
            clock.dayPaused = !clock.dayPaused;
            setSnapshot((s) => ({ ...s }));
          }}
        >
          {t(
            clock.dayPaused
              ? "society.city.resume_day"
              : "society.city.pause_day",
          )}
        </button>
        <label className="flex items-center gap-1 text-xs">
          {t("society.city.hour")}
          <input
            className="w-24"
            aria-label={t("society.city.hour")}
            type="range"
            min="0"
            max="23.9"
            step="0.1"
            value={snapshot.hour}
            onChange={(e) => {
              clock.hour = Number(e.target.value);
              clock.dayPaused = true;
              setSnapshot((s) => ({ ...s, hour: clock.hour }));
              requestFrame.current();
            }}
          />
          {String(Math.floor(snapshot.hour)).padStart(2, "0")}:00
        </label>
        <button
          className={button}
          aria-expanded={details}
          onClick={() => setDetails(!details)}
        >
          {t("society.city.options")}
        </button>
      </div>
      {details && (
        <div className="flex flex-wrap items-center gap-2 border-b border-border bg-muted/40 p-2 text-xs">
          <label className="flex items-center gap-1">
            <input
              type="checkbox"
              checked={demo}
              onChange={(e) => {
                setDemo(e.target.checked);
                setSelected(null);
                setFollow(null);
              }}
            />
            {t("society.city.demo")}
          </label>
          {demo && (
            <>
              <select
                className={button}
                aria-label={t("society.city.agent_count")}
                value={count}
                onChange={(e) => setCount(Number(e.target.value))}
              >
                {[1, 8, 30].map((n) => (
                  <option key={n}>{n}</option>
                ))}
              </select>
              <select
                className={button}
                aria-label={t("society.city.demo_target")}
                value={demoStop}
                onChange={(e) => setDemoStop(e.target.value as Stop)}
              >
                {DISTRICTS.map((d) => (
                  <option key={d.stop} value={d.stop}>
                    {t(d.labelKey)}
                  </option>
                ))}
              </select>
            </>
          )}
          <span data-testid="city-metrics">
            {metrics.current.fps} FPS · {metrics.current.calls} draws ·{" "}
            {metrics.current.triangles.toLocaleString()} triangles
          </span>
        </div>
      )}
      <div ref={host} className="relative isolate min-h-0 flex-1">
        {webgl ? (
          <Canvas
            key={generation}
            camera={{
              position: [700, 550, 850],
              fov: 45,
              near: 0.2,
              far: 9000,
            }}
            dpr={1}
            gl={{ antialias: true, alpha: false }}
            frameloop={!awake ? "never" : reduced ? "demand" : "always"}
          >
            <Suspense fallback={null}>
              <CityRuntime
                sim={sim}
                clock={clock}
                paused={reduced}
                focus={focus}
                follow={follow}
                metrics={metrics}
                requestFrame={requestFrame}
              />
              <CityEnvironment
                graph={sim.graph}
                placements={sim.placements}
                activeStops={activeStops}
                onSelect={selectStop}
                interiorStop={interiorStop ?? (follow && sim.citizens.get(follow)?.atWork ? sim.citizens.get(follow)!.target : null)}
              />
              {actors
                .filter((a) => sim.citizens.has(a.id))
                .map((a) => (
                  <MovingActor
                    key={a.id}
                    actor={a}
                    sim={sim}
                    paused={reduced || !awake}
                    selected={selected === a.id}
                    onSelect={selectActor}
                  />
                ))}
            </Suspense>
          </Canvas>
        ) : (
          <div className="p-6">
            <p>{t("society.world.webgl_missing_body")}</p>
            <button className={button} onClick={onOpenLedger}>
              {t("society.world.open_ledger")}
            </button>
          </div>
        )}
        <div
          className="absolute left-3 top-3 z-20 flex max-w-[55%] flex-wrap gap-1 rounded-lg border border-border bg-popover/95 p-1.5 shadow-sm"
          aria-label={t("society.city.districts")}
        >
          {DISTRICTS.map((d) => (
            <button
              key={d.stop}
              className="rounded px-2 py-1 text-xs hover:bg-muted"
              onClick={() => selectStop(d.stop)}
            >
              {t(d.labelKey)}
            </button>
          ))}
        </div>
        <div className="absolute right-3 top-3 z-20 max-h-[40%] w-48 overflow-auto rounded-lg border border-border bg-popover/95 p-2 text-xs shadow-sm">
          <p className="mb-2 font-medium">
            {t(
              demo
                ? "society.city.demo"
                : loading
                  ? "society.city.loading"
                  : sample
                  ? "society.city.sample"
                  : "society.city.live",
            )}
          </p>
          {sample && (
            <p className="mb-2 text-muted-foreground">
              {t("society.city.sample_notice")}
            </p>
          )}
          {roster.isError && !demo && (
            <p role="alert">{t("society.city.feed_error")}</p>
          )}
          {actors.map((a) => (
            <button
              key={a.id}
              className="mb-1 block w-full rounded p-1.5 text-left hover:bg-muted"
              onClick={() => selectActor(a.id)}
            >
              {a.label}
              <span className="block text-muted-foreground">
                {stageText(
                  sim.citizens.get(a.id)?.stage ?? "arrived",
                  sim.citizens.get(a.id)?.waitingForWorkspace,
                )}
              </span>
            </button>
          ))}
        </div>
        <div className="absolute bottom-3 left-3 z-20 rounded-lg border border-border bg-popover/95 p-3 text-xs shadow-sm">
          <svg
            viewBox="-950 -650 1900 1300"
            width="190"
            height="120"
            role="img"
            aria-label={t("society.city.city_map")}
          >
            <rect
              x={CAMERA_BOUNDS.minX}
              y={CAMERA_BOUNDS.minZ}
              width={CAMERA_BOUNDS.maxX - CAMERA_BOUNDS.minX}
              height={CAMERA_BOUNDS.maxZ - CAMERA_BOUNDS.minZ}
              rx="40"
              fill="none"
              stroke="currentColor"
              opacity="0.2"
              strokeWidth="8"
            />
            <ellipse
              cx="0"
              cy="-110"
              rx="500"
              ry="280"
              fill="none"
              stroke="currentColor"
              strokeWidth="10"
              opacity="0.4"
            />
            {DISTRICTS.map((d) => (
              <circle
                key={d.stop}
                cx={d.stationPosition[0]}
                cy={d.stationPosition[2]}
                r="26"
                fill={d.color}
              />
            ))}
            <circle
              cx={train.position[0]}
              cy={train.position[2]}
              r="17"
              fill="currentColor"
            />
          </svg>
          <p>
            {t("society.city.metro_line")} · {actors.length}{" "}
            {t("society.city.agents")}
          </p>
          <p className="mt-1 text-muted-foreground">
            {CITY.width / 1000} × {CITY.depth / 1000} km ·{" "}
            {t("society.city.city_limits")}
          </p>
          {reduced && (
            <p className="mt-2 max-w-52">{t("society.city.reduced")}</p>
          )}
          {snapshot.debt > 1 && <p>{t("society.city.catching_up")}</p>}
        </div>
        {citizen && actor && (
          <div
            className="absolute bottom-3 right-3 z-20 w-64 rounded-lg border border-border bg-popover p-3 text-xs shadow-lg"
            data-testid="city-journey"
            data-stage={citizen.stage}
            data-position={citizen.position.join(",")}
            data-simulation-time={snapshot.time}
          >
            <strong>{actor.label}</strong>
            <p className="mt-1">
              {t(
                demo || sample
                  ? "society.city.example_state"
                  : "society.city.task",
              )}
              : {actor.checkpoint} · {actor.state}
            </p>
            <p>
              {t("society.city.travel")}:{" "}
              {stageText(citizen.stage, citizen.waitingForWorkspace)}
            </p>
            <p>
              {t("society.city.target")}: {t(district(citizen.target).labelKey)}
            </p>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                className={button}
                onClick={() =>
                  setFollow(follow === citizen.id ? null : citizen.id)
                }
              >
                {t(
                  follow === citizen.id
                    ? "society.city.unfollow"
                    : "society.city.follow",
                )}
              </button>
              {!demo && !sample && (
                <button
                  className={button}
                  onClick={() => onSelectAgent?.(citizen.id)}
                >
                  {t("society.city.open_agent")}
                </button>
              )}
            </div>
          </div>
        )}
        {activeDistrict && (
          <div
            className="absolute left-3 top-16 z-20 max-w-72 rounded-lg border border-border bg-popover p-3 text-xs shadow-lg"
            data-testid="city-building-panel"
          >
            <strong>{t(activeDistrict.labelKey)}</strong>
            <p className="my-2 text-muted-foreground">
              {t(`society.city.description_${activeDistrict.id}`)}
            </p>
            <button
              className={button}
              onClick={() =>
                activeDistrict.stop === "east"
                  ? setActiveSection("agentic-ide")
                  : onSelectPlace?.(activeDistrict.placeId)
              }
            >
              {t(
                activeDistrict.stop === "east"
                  ? "society.city.open_terminal"
                  : "society.city.open_building",
              )}
            </button>
            <button
              className={`${button} ml-1`}
              onClick={() => {
                setInteriorStop(
                  interiorStop === activeDistrict.stop
                    ? null
                    : activeDistrict.stop,
                );
                requestFrame.current();
              }}
            >
              {t(
                interiorStop === activeDistrict.stop
                  ? "society.city.exterior"
                  : "society.city.work_floor",
              )}
            </button>
            <details className="mt-3">
              <summary className="cursor-pointer">
                {t("society.city.adjust_plot")}
              </summary>
              <div className="my-2 flex flex-wrap gap-1">
                <button className={button} onClick={() => movePlot(-4, 0)}>
                  ← 4 m
                </button>
                <button className={button} onClick={() => movePlot(4, 0)}>
                  4 m →
                </button>
                <button className={button} onClick={() => movePlot(0, -4)}>
                  ↑ 4 m
                </button>
                <button className={button} onClick={() => movePlot(0, 4)}>
                  4 m ↓
                </button>
              </div>
            </details>
            {message && (
              <p role="status" className="my-2">
                {t(`society.city.${message}`)}
              </p>
            )}
            <button
              className="mt-2 text-muted-foreground underline"
              onClick={() => setSelectedStop(null)}
            >
              {t("society.city.close")}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
