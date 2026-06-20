import { SceneWrap } from "../components/SceneWrap";
import { Kicker, Title } from "../components/Text";
import { FeatureBadge } from "../components/FeatureBadge";

const FEATURES = [
  { icon: "book", label: "Memory & Wiki" },
  { icon: "bolt", label: "Custom skills" },
  { icon: "robot", label: "Background missions" },
  { icon: "globe", label: "Web search" },
  { icon: "phone", label: "Phone calls" },
  { icon: "mic", label: "Voice & text" },
] as const;

export const MoreFeatures: React.FC = () => {
  return (
    <SceneWrap>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 22,
          width: "100%",
        }}
      >
        <Kicker>And more</Kicker>
        <Title delay={8} size={62}>
          One assistant. Many skills.
        </Title>

        <div
          style={{
            marginTop: 30,
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "center",
            gap: 22,
            maxWidth: 1040,
          }}
        >
          {FEATURES.map((f, i) => (
            <FeatureBadge key={f.label} icon={f.icon} label={f.label} delay={30 + i * 12} />
          ))}
        </div>
      </div>
    </SceneWrap>
  );
};
