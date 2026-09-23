import "./app.css";
import "./render.css";
import { Composition, registerRoot } from "remotion";
import { Orchestrator } from "./Orchestrator";
import { Agents } from "./Agents";
import { UltraSwarm } from "./UltraSwarm";

const Root = () => <>
  <Composition id="JarvisOrchestrator" component={Orchestrator} width={1600} height={900} fps={30} durationInFrames={450}/>
  <Composition id="JarvisAgents" component={Agents} width={1600} height={900} fps={30} durationInFrames={450}/>
  <Composition id="UltraSwarm" component={UltraSwarm} width={1600} height={900} fps={30} durationInFrames={450}/>
</>;
registerRoot(Root);
