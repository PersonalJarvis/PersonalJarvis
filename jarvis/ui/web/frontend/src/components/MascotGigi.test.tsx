import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, test } from "vitest";

import { MascotGigi } from "@/components/MascotGigi";
import { useEventStore } from "@/store/events";

describe("MascotGigi", () => {
  beforeEach(() => {
    useEventStore.setState({
      voiceState: "idle",
      transcription: "",
      transcriptionFinal: true,
    });
  });

  afterEach(() => {
    cleanup();
  });

  test("shows the current transcription in a listening bubble", () => {
    useEventStore.setState({
      voiceState: "listening",
      transcription: "Hallo, ich bin gross und teste die neue Sprechblase.",  // i18n-allow: simulated German STT transcription is the content under test
      transcriptionFinal: false,
    });

    render(<MascotGigi size={56} />);

    const bubble = screen.getByRole("status");
    expect(bubble.textContent).toContain(
      "Hallo, ich bin gross und teste die neue Sprechblase.",  // i18n-allow: simulated German STT transcription is the content under test
    );
    expect(bubble.className).toContain("gigi-bubble-listening");
  });

  test("does not show the old comment bubble while listening without transcription", () => {
    useEventStore.setState({
      voiceState: "listening",
      transcription: "",
      transcriptionFinal: true,
    });

    render(<MascotGigi size={56} />);

    expect(screen.queryByRole("status")).toBeNull();
  });
});
