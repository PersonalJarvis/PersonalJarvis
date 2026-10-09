"""Warm desktop runtime; capture and visible UI require an explicit start command."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path


def emit(phase: str, **values: object) -> None:
    try:
        print(json.dumps({"phase": phase, **values}), flush=True)
    except OSError:
        # Parent exited: finishing the local file and closing the UI still matter.
        pass


def run(
    output: Path | None, language: str = "en", *, standby: bool = False,
    options: dict | None = None,
) -> int:
    import importlib

    # Load native DLLs on the sidecar main thread before Qt's event loop.
    # Importing NumPy on the encoder thread can stall Windows DLL loading
    # while the GUI thread is inside a screen grab. The main app stays lazy.
    for module in ("av", "numpy"):
        importlib.import_module(module)
    # Keep all Qt imports inside the desktop sidecar (headless/base boot is safe).
    from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
    from PySide6.QtGui import QImage, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QLabel,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    from jarvis.appshot.picker.renderer import Picker, _SelectWindow
    from jarvis.appshot.recording_frame import owned_capture_image
    from jarvis.appshot.recording_hud import RecordingHint, RecordingHud
    from jarvis.appshot.recording_labels import LABELS
    from jarvis.appshot.recording_options import RecordingOptions, effective_fps, output_size
    from jarvis.appshot.region import selection_fractions
    from jarvis.appshot.video_encoder import FirstFramePoster, VideoEncoder
    from jarvis.platform.probes import is_wayland

    # Importing the backend in standby opens no capture device or portal.
    importlib.import_module("PySide6.QtMultimedia")

    app = QApplication(["Personal Jarvis — Screen recording"])
    from jarvis.platform.qt_sidecar import hide_from_dock

    hide_from_dock()  # macOS: no "Python" Dock icon for the standby recorder
    app.setQuitOnLastWindowClosed(False)
    labels = LABELS.get(language, LABELS["en"])
    preferences = RecordingOptions.model_validate(options or {})

    class ParentPipe(QObject):
        stop = Signal()
        start = Signal(dict)

        def read(self) -> None:
            try:
                for line in sys.stdin:
                    if line.strip() == "stop":
                        self.stop.emit()
                        continue
                    try:
                        command = json.loads(line)
                    except ValueError:
                        continue  # Ignore non-protocol input; EOF still stops the worker.
                    if isinstance(command, dict) and command.get("cmd") == "start":
                        self.start.emit(command)
            except OSError:
                pass  # A closed pipe means the parent exited: always stop below.
            self.stop.emit()

    class Preview(QWidget):
        """Wayland crops the portal-approved source in a visible preview."""

        def __init__(self, image: QImage, owner: Session) -> None:
            super().__init__()
            self.image = image
            self.owner = owner
            self.start = None
            self.end = None
            self.setMinimumSize(480, 270)
            self.resize(900, max(270, round(900 * image.height() / image.width())))

        def image_rect(self) -> QRect:
            size = self.image.size().scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatio)
            return QRect(
                (self.width() - size.width()) // 2,
                (self.height() - size.height()) // 2,
                size.width(),
                size.height(),
            )

        def paintEvent(self, _event) -> None:  # noqa: N802
            painter = QPainter(self)
            painter.fillRect(self.rect(), self.palette().window())
            painter.drawImage(self.image_rect(), self.image)
            if self.start is not None and self.end is not None:
                painter.setPen(QPen(self.palette().highlight().color(), 2))
                painter.drawRect(QRect(self.start, self.end).normalized())

        def mousePressEvent(self, event) -> None:  # noqa: N802
            if event.button() == Qt.MouseButton.LeftButton:
                self.start = event.position().toPoint()
                self.end = self.start

        def mouseMoveEvent(self, event) -> None:  # noqa: N802
            if self.start is not None:
                self.end = event.position().toPoint()
                self.update()

        def mouseReleaseEvent(self, event) -> None:  # noqa: N802
            if self.start is None or event.button() != Qt.MouseButton.LeftButton:
                return
            bounds = self.image_rect()
            rect = QRect(self.start, event.position().toPoint()).normalized().intersected(bounds)
            if rect.width() >= 8 and rect.height() >= 8:
                self.owner.select(
                    None,
                    (
                        (rect.x() - bounds.x()) / bounds.width(),
                        (rect.y() - bounds.y()) / bounds.height(),
                        rect.width() / bounds.width(),
                        rect.height() / bounds.height(),
                    ),
                )
            self.start = None
            self.update()

    class RecordingSelectWindow(_SelectWindow):
        """Finish a video selection immediately, without screenshot annotation tools."""

        def mouseReleaseEvent(self, event) -> None:  # noqa: N802
            if event.button() != Qt.MouseButton.LeftButton or self._start is None:
                return
            end = event.position()
            frac = selection_fractions(
                self._start.x(), self._start.y(), end.x(), end.y(), self.width(), self.height()
            )
            self._start = None
            self._end = None
            if frac is not None:
                self._owner.finish(self, frac)
            else:
                self.update()

    class RecordingPicker(Picker):
        def make_window(self, screen, frozen):
            return RecordingSelectWindow(screen, frozen, self)

        def start(self) -> None:
            super().start()
            for window in self._windows:
                screen = window.screen_ref
                dpr = screen.devicePixelRatio()
                size = output_size(
                    round(screen.size().width() * dpr), round(screen.size().height() * dpr),
                    preferences.resolution,
                )
                screen_labels = dict(labels)
                screen_labels["select"] += (
                    f"\n{screen.name()} · {size[0]} × {size[1]} · "
                    f"{effective_fps(preferences.fps, screen.refreshRate())} FPS · "
                    f"{preferences.bitrate_mbps} Mbps"
                )
                panel = RecordingHint(
                    window,
                    screen_labels,
                    lambda w=window: self.finish(w, (0, 0, 1, 1)),
                    window.devicePixelRatioF(),
                )
                panel.move(max(0, (window.width() - panel.width()) // 2), 24)
                panel.show()

        def finish(self, window, frac) -> None:
            if self._done:
                return
            self._done = True
            for timer in self.findChildren(QTimer):
                timer.stop()
            for win in self._windows:
                win.hide()
            app.processEvents()
            if window is None or frac is None:
                session.stop()
            else:
                session.select(window.screen_ref, frac)

    class Session(QWidget):
        def __init__(self) -> None:
            super().__init__()
            self.setWindowTitle(labels["title"])
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
            self.layout_box = QVBoxLayout(self)
            self.label = QLabel(labels["portal"])
            self.layout_box.addWidget(self.label)
            self.stop_button = QPushButton(labels["cancel"])
            self.stop_button.clicked.connect(self.stop)
            self.layout_box.addWidget(self.stop_button)
            self.encoder = None
            self.screen = None
            self.frac = None
            self.started = None
            self.reported = False
            self.finishing = False
            self.completed = False
            self.error = ""
            self.last_image = None
            self.poster = FirstFramePoster()
            self.hud = None
            #: Full-depth capture when the monitor runs HDR / wide gamut (Windows).
            self.hdr = None
            self.preview = None
            self.capture = None
            self.capture_session = None
            self.sink = None
            self.picker = None
            self.last_frame = 0.0
            self.dimensions = None
            self.permission_checked = 0.0
            self.fps = effective_fps(preferences.fps, None)
            self.refresh_hz = None
            self.monitor_name = ""
            self.timer = QTimer(self)
            self.timer.setTimerType(Qt.TimerType.PreciseTimer)
            self.timer.timeout.connect(self.tick)
            self.timer.start(max(1, 1000 // self.fps))
            self.selection_deadline = time.perf_counter() + 180

        def begin(self) -> None:
            emit("selecting")
            if is_wayland():
                self.show()
                self.start_capture()
            else:
                self.picker = RecordingPicker(app)
                self.picker.start()
                emit("selection_ready")

        def start_capture(self) -> None:
            from PySide6.QtMultimedia import QMediaCaptureSession, QScreenCapture, QVideoSink

            self.capture_session = QMediaCaptureSession(self)
            self.capture = QScreenCapture(self)
            self.sink = QVideoSink(self)
            if self.screen is not None:
                self.capture.setScreen(self.screen)
            self.capture_session.setScreenCapture(self.capture)
            self.capture_session.setVideoSink(self.sink)
            self.sink.videoFrameChanged.connect(self.portal_frame)
            self.capture.activeChanged.connect(
                lambda active: None if active or self.finishing else self.stop()
            )
            self.capture.errorOccurred.connect(
                lambda _code, _text: self.fail(
                    "Screen capture was denied or unavailable. Check screen sharing permissions."
                )
            )
            self.capture.start()

        def portal_frame(self, frame) -> None:
            now = time.perf_counter()
            if self.finishing:
                return
            self.last_frame = now
            image = owned_capture_image(frame, opaque_desktop=sys.platform == "win32")
            if image.isNull():
                return
            self.last_image = image
            if is_wayland() and self.preview is None and self.frac is None:
                self.label.setText(labels["preview"])
                self.preview = Preview(image, self)
                self.layout_box.insertWidget(1, self.preview)
                full = QPushButton(labels["full"])
                full.clicked.connect(lambda: self.select(None, (0, 0, 1, 1)))
                self.layout_box.insertWidget(2, full)
                self.full_button = full
                self.adjustSize()

        def select(self, screen, frac) -> None:
            if self.finishing or self.frac is not None:
                return
            self.screen = screen
            self.frac = frac
            if self.preview is not None:
                self.preview.hide()
                self.full_button.hide()
            self.label.setText(labels["starting"])
            self.stop_button.setText(labels["stop"])
            if screen is not None:
                self.hud = RecordingHud(screen, frac, labels, self.stop)
                self.hide()
                self.hud.show()
            else:
                # The portal does not expose the source's global screen position.
                self.resize(290, 90)
                self.show()
            # Let the selection overlays disappear before capturing pixels.
            QTimer.singleShot(250, self.start_encoder)

        def start_encoder(self) -> None:
            if self.finishing:
                return
            self.refresh_hz = self.screen.refreshRate() if self.screen is not None else None
            self.monitor_name = self.screen.name() if self.screen is not None else ""
            self.fps = effective_fps(preferences.fps, self.refresh_hz)
            self.timer.setInterval(max(1, 1000 // self.fps))
            if self.hud is not None:
                from jarvis.appshot.hdr_recording import choose_hdr_capture

                # An HDR or wide-gamut monitor is read at full depth instead.
                self.hdr = choose_hdr_capture(
                    int(self.hud.frame.winId()), tuple(self.frac),
                    preferences.resolution, self.fps,
                )
            # Python 3.11's Windows monotonic clock can tick only every 15.6 ms.
            # QPC keeps 60/120 FPS frames and the audio epoch distinguishable.
            self.started = time.perf_counter()
            self.encoder = VideoEncoder(
                output, options=preferences, fps=self.fps, epoch=self.started,
                hdr=self.hdr is not None,
            )
            self.encoder.start()
            if self.hdr is not None:
                self.hdr.start(self.encoder, self.started)
            elif self.capture is None:
                self.start_capture()

        def tick(self) -> None:
            try:
                self._tick()
            except Exception:
                import logging

                logging.getLogger(__name__).exception("appshot: screen capture failed")
                self.fail("The selected screen is no longer available. The recording stopped.")

        def _tick(self) -> None:
            if self.finishing:
                if self.encoder is None or self.encoder.done.is_set():
                    self.finish()
                return
            if self.started is None or self.encoder is None:
                if time.perf_counter() > self.selection_deadline:
                    self.stop()
                return
            if self.encoder.error:
                self.fail(self.encoder.error)
                return
            elapsed = time.perf_counter() - self.started
            if sys.platform == "darwin" and elapsed - self.permission_checked >= 1.0:
                from jarvis.platform import screen_access

                self.permission_checked = elapsed
                if not screen_access.state_allows_capture(screen_access.screen_recording_state()):
                    self.fail("Screen recording permission was revoked. The recording stopped.")
                    return
            if not self.reported and elapsed > 15:
                self.fail(
                    "The recorder did not receive usable frames. Check screen recording permission."
                )
                return
            if self.screen is not None:
                if effective_fps(preferences.fps, self.screen.refreshRate()) != self.fps:
                    self.fail("The display refresh rate changed. Start a new recording.")
                    return
            if self.hdr is not None:
                if self.hdr.error:
                    self.fail(self.hdr.error)
                    return
                if self.poster.image is None and self.hdr.poster:
                    self.poster.image = self.hdr.poster
                self.report(elapsed)
                return
            image = self.last_image
            if image is None or image.isNull():
                return  # Startup above has a bounded first-frame deadline.
            size = (image.width(), image.height())
            if self.dimensions is not None and size != self.dimensions:
                self.fail("The screen resolution changed. Start a new recording for the new size.")
                return
            self.dimensions = size
            x, y, width, height = self.frac
            rect = QRect(
                round(x * size[0]),
                round(y * size[1]),
                max(1, round(width * size[0])),
                max(1, round(height * size[1])),
            )
            rect = rect.intersected(image.rect())
            # QImage is implicitly shared and the encoder only reads it. Avoid
            # copying a full 4K frame on every GUI tick when no crop is needed.
            crop = image if rect == image.rect() else image.copy(rect)
            self.encoder.submit(crop, elapsed)
            self.poster.observe(crop)
            self.report(elapsed)

        def report(self, elapsed: float) -> None:
            if self.encoder.ready.is_set() and not self.reported:
                self.reported = True
                emit("recording", width=self.encoder.size[0], height=self.encoder.size[1],
                     fps=self.fps, bitrate_mbps=preferences.bitrate_mbps,
                     system_audio=preferences.system_audio, display_name=self.monitor_name,
                     refresh_hz=self.refresh_hz)
            if self.reported:
                seconds = int(elapsed)
                self.label.setText(f"Recording · {seconds // 60:02d}:{seconds % 60:02d}")
                if self.hud is not None:
                    self.hud.update_elapsed(elapsed)

        def fail(self, message: str) -> None:
            self.error = message
            self.stop()

        def stop(self) -> None:
            if self.finishing:
                return
            self.finishing = True
            if self.hud is not None:
                self.hud.hide()
            if self.picker:
                self.picker.finish(None, None)
            if self.capture:
                self.capture.stop()
            if self.hdr is not None:
                self.hdr.stop()
            self.label.setText(labels["saving"])
            self.stop_button.setEnabled(False)
            if self.encoder:
                self.encoder.stop(max(0, time.perf_counter() - self.started))
            # A stuck native encoder cannot keep recording or leave an orphan.
            QTimer.singleShot(15000, self.force_finish)

        def force_finish(self) -> None:
            if self.encoder and not self.encoder.done.is_set():
                self.error = "The recording could not finish writing."
            self.finish()

        def finish(self) -> None:
            if self.completed:
                return
            self.completed = True
            self.timer.stop()
            error = self.error or (self.encoder.error if self.encoder else "")
            if error:
                emit("error", message=error)
            elif self.encoder and output.is_file():
                emit(
                    "saved",
                    duration_s=round(self.encoder.duration, 1),
                    width=self.encoder.size[0],
                    height=self.encoder.size[1],
                    fps=self.fps,
                    bitrate_mbps=preferences.bitrate_mbps,
                    system_audio=preferences.system_audio,
                    display_name=self.monitor_name,
                    refresh_hz=self.refresh_hz,
                    dropped_frames=self.encoder.dropped_frames,
                    **self.save_preview(),
                )
            else:
                emit("cancelled")
            if self.hud is not None:
                self.hud.close()
            app.quit()

        def save_preview(self) -> dict:
            try:
                return self._save_preview()
            except Exception:
                import logging

                logging.getLogger(__name__).warning(
                    "appshot: saved video preview is unavailable", exc_info=True
                )
                return {}

        def _save_preview(self) -> dict:
            if self.poster.image is None:
                return {}
            thumbnail = self.poster.image.scaled(
                560, 560, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            poster = output.with_suffix(".jpg")
            if not thumbnail.save(str(poster), "JPG", 82):
                return {}  # An optional poster failure does not invalidate the video.
            screen = self.screen or app.primaryScreen()
            if screen is None:
                return {}
            return {
                "preview": True, "screen_name": screen.name(),
                "monitor": list(screen.geometry().getRect()),
                "rect": list(self.frac or (0, 0, 1, 1)),
            }

        def closeEvent(self, event) -> None:  # noqa: N802
            if self.completed:
                event.accept()
                return
            event.ignore()
            self.stop()

    session = None
    closing = False
    pipe = ParentPipe()

    def stop() -> None:
        nonlocal closing
        closing = True
        if session is None:
            app.quit()
        else:
            session.stop()

    def begin(command=None) -> None:
        nonlocal session, output, labels, preferences
        if session is not None or closing:
            return
        if command is not None:
            value = command.get("output")
            if not isinstance(value, str) or not value:
                emit("error", message="The recording output is missing.")
                stop()
                return
            output = Path(value)
            labels = LABELS.get(command.get("language", "en"), LABELS["en"])
            try:
                preferences = RecordingOptions.model_validate(command.get("options", {}))
            except ValueError:
                emit("error", message="The recording settings are invalid.")
                stop()
                return
        session = Session()
        try:
            if sys.platform == "darwin":
                from jarvis.platform import screen_access

                if not screen_access.state_allows_capture(screen_access.screen_recording_state()):
                    session.fail("Allow Screen Recording for the app before recording this screen.")
                    return
            if not app.screens():
                session.fail("No screen is available for recording.")
                return
            session.begin()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("appshot: capture initialization failed")
            session.fail("Screen capture could not start. Check desktop support and permissions.")

    pipe.stop.connect(stop)
    pipe.start.connect(begin)
    threading.Thread(target=pipe.read, name="appshot-recording-parent", daemon=True).start()
    if standby:
        emit("ready")  # No Session, windows, timers, pixels or permission prompts in standby.
    else:
        QTimer.singleShot(0, begin)
    return app.exec()


def main() -> int:
    from jarvis.appshot.recording_labels import LABELS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--standby", action="store_true")
    parser.add_argument("--language", choices=tuple(LABELS), default="en")
    args = parser.parse_args()
    if not args.standby and args.output is None:
        parser.error("--output is required unless --standby is used")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        return run(args.output, args.language, standby=args.standby)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("appshot: recorder unavailable")
        emit("error", message="The desktop recorder is unavailable on this computer.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
