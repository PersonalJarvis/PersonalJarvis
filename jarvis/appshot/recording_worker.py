"""Visible capture UI, loaded only in the user-started recorder process."""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from pathlib import Path


def emit(phase: str, **values: object) -> None:
    print(json.dumps({"phase": phase, **values}), flush=True)


def run(output: Path) -> int:
    # Keep all Qt imports inside the desktop sidecar (headless/base boot is safe).
    from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
    from PySide6.QtGui import QImage, QPainter, QPen
    from PySide6.QtWidgets import (
        QApplication,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )

    from jarvis.appshot.picker.renderer import Picker
    from jarvis.appshot.video_encoder import FPS, VideoEncoder
    from jarvis.platform.probes import is_wayland

    app = QApplication(["Personal Jarvis — Screen recording"])
    app.setQuitOnLastWindowClosed(False)

    class ParentPipe(QObject):
        stop = Signal()

        def read(self) -> None:
            try:
                for _line in sys.stdin:
                    self.stop.emit()
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

    class RecordingPicker(Picker):
        def start(self) -> None:
            super().start()
            for window in self._windows:
                panel = QWidget(window)
                panel.setAutoFillBackground(True)
                layout = QHBoxLayout(panel)
                layout.addWidget(QLabel("Drag an area to record · Esc cancels"))
                button = QPushButton("Record entire screen")
                button.clicked.connect(
                    lambda _checked=False, w=window: self.finish(w, (0, 0, 1, 1))
                )
                layout.addWidget(button)
                panel.adjustSize()
                panel.move(max(0, (window.width() - panel.width()) // 2), 24)
                panel.show()

        def finish(self, window, frac) -> None:
            if self._done:
                return
            self._done = True
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
            self.setWindowTitle("AppShots — Screen recording")
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
            self.layout_box = QVBoxLayout(self)
            self.label = QLabel("Choose a screen in the system sharing dialog.")
            self.layout_box.addWidget(self.label)
            self.stop_button = QPushButton("Cancel")
            self.stop_button.clicked.connect(self.stop)
            self.layout_box.addWidget(self.stop_button)
            self.encoder = None
            self.screen = None
            self.frac = None
            self.started = None
            self.reported = False
            self.finishing = False
            self.error = ""
            self.last_image = None
            self.preview = None
            self.capture = None
            self.capture_session = None
            self.sink = None
            self.picker = None
            self.last_frame = 0.0
            self.dimensions = None
            self.timer = QTimer(self)
            self.timer.timeout.connect(self.tick)
            self.timer.start(round(1000 / FPS))
            self.selection_deadline = time.monotonic() + 180

        def begin(self) -> None:
            emit("selecting")
            if is_wayland():
                from PySide6.QtMultimedia import QMediaCaptureSession, QScreenCapture, QVideoSink

                self.show()
                self.capture_session = QMediaCaptureSession(self)
                self.capture = QScreenCapture(self)
                self.sink = QVideoSink(self)
                self.capture_session.setScreenCapture(self.capture)
                self.capture_session.setVideoSink(self.sink)
                self.sink.videoFrameChanged.connect(self.portal_frame)
                self.capture.errorOccurred.connect(
                    lambda _code, _text: self.fail(
                        "Screen sharing was denied or unavailable. "
                        "Check PipeWire and the ScreenCast portal."
                    )
                )
                self.capture.start()
            else:
                self.picker = RecordingPicker(app)
                self.picker.start()

        def portal_frame(self, frame) -> None:
            now = time.monotonic()
            if now - self.last_frame < 1 / FPS or self.finishing:
                return
            self.last_frame = now
            image = frame.toImage()
            if image.isNull():
                return
            self.last_image = image
            if self.preview is None and self.frac is None:
                self.label.setText(
                    "Drag an area in the preview, or record the entire shared screen."
                )
                self.preview = Preview(image, self)
                self.layout_box.insertWidget(1, self.preview)
                full = QPushButton("Record entire screen")
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
            self.label.setText("Starting recording…")
            self.stop_button.setText("Stop and save")
            self.resize(290, 90)
            self.show()
            # Let the selection overlays disappear before capturing pixels.
            QTimer.singleShot(250, self.start_encoder)

        def start_encoder(self) -> None:
            if self.finishing:
                return
            self.encoder = VideoEncoder(output)
            self.encoder.start()
            self.started = time.monotonic()

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
                if time.monotonic() > self.selection_deadline:
                    self.stop()
                return
            if self.encoder.error:
                self.fail(self.encoder.error)
                return
            elapsed = time.monotonic() - self.started
            if not self.reported and elapsed > 15:
                self.fail(
                    "The recorder did not receive usable frames. Check screen recording permission."
                )
                return
            if self.screen is not None:
                image = self.screen.grabWindow(0).toImage()
            else:
                image = self.last_image
            if image is None or image.isNull():
                self.fail("Screen capture returned no image. Check screen recording permission.")
                return
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
            crop = image.copy(rect.intersected(image.rect())).convertToFormat(
                QImage.Format.Format_RGBA8888
            )
            self.encoder.submit(crop, elapsed)
            if self.encoder.ready.is_set() and not self.reported:
                self.reported = True
                emit("recording", width=self.encoder.size[0], height=self.encoder.size[1])
            if self.reported:
                seconds = int(elapsed)
                self.label.setText(f"Recording · {seconds // 60:02d}:{seconds % 60:02d}")

        def fail(self, message: str) -> None:
            self.error = message
            self.stop()

        def stop(self) -> None:
            if self.finishing:
                return
            self.finishing = True
            if self.picker:
                for window in self.picker._windows:
                    window.hide()
            if self.capture:
                self.capture.stop()
            self.label.setText("Saving recording…")
            self.stop_button.setEnabled(False)
            if self.encoder:
                self.encoder.stop(max(0, time.monotonic() - self.started))
            # A stuck native encoder cannot keep recording or leave an orphan.
            QTimer.singleShot(15000, self.force_finish)

        def force_finish(self) -> None:
            if self.encoder and not self.encoder.done.is_set():
                self.error = "The recording could not finish writing."
            self.finish()

        def finish(self) -> None:
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
                )
            else:
                emit("cancelled")
            app.quit()

        def closeEvent(self, event) -> None:  # noqa: N802
            event.ignore()
            self.stop()

    session = Session()
    pipe = ParentPipe()
    pipe.stop.connect(session.stop)
    threading.Thread(target=pipe.read, name="appshot-recording-parent", daemon=True).start()
    def begin() -> None:
        try:
            if not app.screens():
                session.fail("No screen is available for recording.")
                return
            session.begin()
        except Exception:
            import logging

            logging.getLogger(__name__).exception("appshot: capture initialization failed")
            session.fail("Screen capture could not start. Check desktop support and permissions.")

    QTimer.singleShot(0, begin)
    return app.exec()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        return run(args.output)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("appshot: recorder unavailable")
        emit("error", message="The desktop recorder is unavailable on this computer.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
