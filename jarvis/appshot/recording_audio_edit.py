"""Stream a recording's trimmed audio through a pitch-preserving speed filter."""

from __future__ import annotations

from fractions import Fraction
from typing import Any


def write_audio(
    source: Any, writer: Any, output: Any, start: float, end: float, speed: float
) -> None:
    """Mux the selected audio range with sample-accurate cuts and a fresh timeline."""
    import av
    import numpy as np

    rate = 48000
    graph = av.filter.Graph()
    entry = graph.add_abuffer(sample_rate=rate, format="fltp", layout="stereo",
                             time_base=Fraction(1, rate))
    tempo = graph.add("atempo", str(speed))
    sink = graph.add("abuffersink")
    graph.link_nodes(entry, tempo, sink)
    graph.configure()
    pts = 0

    def drain() -> None:
        while True:
            try:
                frame = graph.pull()
            except (av.error.BlockingIOError, av.error.EOFError):
                break  # The filter needs input, or its final samples were drained.
            for packet in output.encode(frame):
                writer.mux(packet)

    def push_samples(samples: Any) -> None:
        nonlocal pts
        sliced = av.AudioFrame.from_ndarray(
            np.ascontiguousarray(samples), format="fltp", layout="stereo"
        )
        sliced.sample_rate = rate
        sliced.pts, sliced.time_base = pts, Fraction(1, rate)
        pts += sliced.samples
        graph.push(sliced)
        drain()

    with av.open(str(source)) as reader:
        stream = reader.streams.audio[0]
        resampler = av.AudioResampler(format="fltp", layout="stereo", rate=rate)
        reader.seek(max(0, int(start / stream.time_base)), stream=stream, backward=True)

        def submit(frame: Any) -> bool:
            at = float(frame.pts * frame.time_base) if frame.pts is not None else 0
            left = max(0, round((start - at) * rate))
            right = min(frame.samples, round((end - at) * rate))
            if right > left:
                desired_pts = max(0, round((at - start) * rate) + left)
                # Preserve delayed audio startup and any timestamp gaps relative to video.
                while pts < desired_pts:
                    push_samples(np.zeros((2, min(rate, desired_pts - pts)), dtype=np.float32))
                left += max(0, pts - desired_pts)
                if right > left:
                    push_samples(frame.to_ndarray()[:, left:right])
            return at >= end

        finished = False
        for frame in reader.decode(stream):
            for converted in resampler.resample(frame):
                finished = submit(converted)
            if finished:
                break
        if not finished:
            for converted in resampler.resample(None):
                submit(converted)
    graph.push(None)
    drain()
    for packet in output.encode(None):
        writer.mux(packet)
