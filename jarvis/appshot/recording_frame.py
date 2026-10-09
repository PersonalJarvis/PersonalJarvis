"""Copy capture pixels into owned images before the multimedia buffer is reused."""

from __future__ import annotations

from typing import Any


def owned_capture_image(frame: Any, *, opaque_desktop: bool = False) -> Any:
    """Read packed RGB frames without the GPU presentation/conversion path.

    Desktop frames already contain RGB pixels. Sending them through ``toImage``
    can introduce readback/texture-conversion artifacts. Respect the mapped row
    stride, copy while mapped and always release the frame's mapping. YUV portal
    formats still use Qt's conversion, but their result is detached as well.
    """
    from PySide6.QtGui import QImage, QTransform
    from PySide6.QtMultimedia import QVideoFrame, QVideoFrameFormat

    if not frame.isValid():
        return QImage()
    image_format = QVideoFrameFormat.imageFormatFromPixelFormat(frame.pixelFormat())
    if image_format == QImage.Format.Format_Invalid:
        image = frame.toImage().copy()
    else:
        if not frame.map(QVideoFrame.MapMode.ReadOnly):
            return QImage()
        try:
            width, height = frame.width(), frame.height()
            stride = frame.bytesPerLine(0)
            if width <= 0 or height <= 0 or stride <= 0 or frame.mappedBytes(0) < height * stride:
                return QImage()
            image = QImage(frame.bits(0), width, height, stride, image_format).copy()
        finally:
            frame.unmap()
        surface = frame.surfaceFormat()
        viewport = surface.viewport().intersected(image.rect())
        if not viewport.isEmpty() and viewport != image.rect():
            image = image.copy(viewport)
        if surface.scanLineDirection() == QVideoFrameFormat.Direction.BottomToTop:
            image = image.mirrored(False, True)
        rotation = frame.rotation().value
        if rotation:
            image = image.transformed(QTransform().rotate(rotation))
        if frame.mirrored():
            image = image.mirrored(True, False)
    if image.isNull():
        return image
    # A newly opened desktop source can emit zero-filled transparent buffers.
    # Keep opaque black screens: darkness alone is never an invalid-frame signal.
    if image.hasAlphaChannel():
        w, h = image.width() - 1, image.height() - 1
        points = ((0, 0), (w, 0), (0, h), (w, h), (w // 2, h // 2))
        alphas = [image.pixelColor(x, y).alpha() for x, y in points]
        if all(alpha == 0 for alpha in alphas):
            return QImage()
        # Windows desktop duplication is opaque. Before its first completed
        # GPU copy, a staging surface can contain arbitrary bytes, not just
        # zeros. Do not encode that uninitialized buffer as a real desktop.
        # Portal window sources may legitimately have transparency, so this
        # stronger check is explicitly limited to opaque desktop sources.
        if opaque_desktop and any(alpha != 255 for alpha in alphas):
            return QImage()
    return image
