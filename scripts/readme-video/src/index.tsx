import "./app.css";
import "./render.css";
import { Composition, registerRoot } from "remotion";
import { Orchestrator } from "./Orchestrator";
import { Agents } from "./Agents";
import { UltraSwarm } from "./UltraSwarm";
import settings from "../settings.json";

const Root = () => <>
  <Composition id="JarvisOrchestrator" component={Orchestrator} width={settings.width} height={settings.height} fps={settings.fps} durationInFrames={settings.durationInFrames}/>
  <Composition id="JarvisAgents" component={Agents} width={settings.width} height={settings.height} fps={settings.fps} durationInFrames={settings.durationInFrames}/>
  <Composition id="UltraSwarm" component={UltraSwarm} width={settings.width} height={settings.height} fps={settings.fps} durationInFrames={settings.durationInFrames}/>
</>;
registerRoot(Root);
