import "./app.css";
import "./render.css";
import { Composition, registerRoot } from "remotion";
import { Orchestrator } from "./Orchestrator";
import { Agents } from "./Agents";
import { UltraSwarm } from "./UltraSwarm";
import settings from "../settings.json";

const scenes = { JarvisOrchestrator: Orchestrator, JarvisAgents: Agents, UltraSwarm };
const Root = () => <>{settings.compositions.map(scene =>
  <Composition key={scene.id} id={scene.id} component={scenes[scene.id as keyof typeof scenes]}
    width={settings.width} height={settings.height} fps={settings.fps} durationInFrames={scene.durationInFrames}/>
)}</>;
registerRoot(Root);
