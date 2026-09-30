"""
The DISPLAY window — this is the window that gets moved onto the projector /
concert screen and shown full-screen. It never contains any controls; it is
purely a stack of visual layers:

    [ background layer ]  <- png / jpg / gif / mp4 (looped)
    [ element layer    ]  <- optional drag & drop image, shown on top
    [ flash / white    ]  <- solid color overlay used for flash / strobe / white

All backgrounds that have been added are kept alive (preloaded) so switching
between them during a show is instant, with no re-decoding / re-loading lag.
"""

import os

from PySide6.QtCore import Qt, QUrl, QTimer, Signal
from PySide6.QtGui import QPixmap, QMovie, QPainter, QColor
from PySide6.QtWidgets import QWidget, QLabel
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QVideoSink, QVideoFrame

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp", ".webp")
GIF_EXTENSIONS = (".gif",)
VIDEO_EXTENSIONS = (".mp4", ".mov", ".m4v", ".avi")
ALL_BACKGROUND_EXTENSIONS = IMAGE_EXTENSIONS + GIF_EXTENSIONS + VIDEO_EXTENSIONS
ELEMENT_EXTENSIONS = IMAGE_EXTENSIONS + GIF_EXTENSIONS


# --------------------------------------------------------------------------- #
# Background layer implementations
# --------------------------------------------------------------------------- #

class CoverImageLayer(QWidget):
    """Static image (png/jpg), scaled to cover (crop-fill) the widget."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap(path)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self._pixmap.isNull():
            return
        scaled = self._pixmap.scaled(
            self.size(), Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawPixmap(x, y, scaled)

    def pause(self):
        pass

    def resume(self):
        pass

    def set_muted(self, muted):
        pass


class CoverGifLayer(QLabel):
    """Animated GIF, looping, scaled to cover the widget."""

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background-color:#000000;")
        self._movie = QMovie(path)
        self._movie.setCacheMode(QMovie.CacheAll)
        self.setMovie(self._movie)
        self._movie.start()

    def resizeEvent(self, event):
        if self._movie.isValid():
            self._movie.setScaledSize(self.size())
        super().resizeEvent(event)

    def pause(self):
        self._movie.setPaused(True)

    def resume(self):
        self._movie.setPaused(False)

    def set_muted(self, muted):
        pass


class CoverVideoLayer(QWidget):
    """
    MP4/looping video, scaled to cover the widget.

    IMPORTANT: this deliberately does NOT use QVideoWidget. QVideoWidget
    renders through a separate native/overlay window on most platforms
    (Windows in particular), which always paints on top of ordinary Qt
    widgets regardless of raise_()/lower() order — that's what made the
    flash / strobe / white-screen overlay invisible whenever a video
    background was active. Instead, video frames are pulled via QVideoSink
    and painted manually with QPainter, so this layer behaves like any
    other regular widget and respects normal Z-order / raise_() calls.
    """

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self._frame_image = None

        self._sink = QVideoSink(self)
        self._sink.videoFrameChanged.connect(self._on_frame)

        self._audio_output = QAudioOutput(self)
        self._audio_output.setMuted(True)  # muted by default for a visuals screen

        self._player = QMediaPlayer(self)
        self._player.setVideoSink(self._sink)
        self._player.setAudioOutput(self._audio_output)
        self._player.setSource(QUrl.fromLocalFile(path))

        # Loop forever. setLoops() is the clean way (Qt 6.4+); fall back to
        # manually restarting on EndOfMedia for older bindings.
        try:
            self._player.setLoops(QMediaPlayer.Loops.Infinite)
        except AttributeError:
            self._player.mediaStatusChanged.connect(self._restart_on_end)

        self._player.play()

    def _on_frame(self, frame: QVideoFrame):
        if not frame.isValid():
            return
        image = frame.toImage()
        if not image.isNull():
            self._frame_image = image
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#000000"))
        if self._frame_image is None:
            return
        scaled = self._frame_image.scaled(
            self.size(), Qt.KeepAspectRatioByExpanding, Qt.FastTransformation
        )
        x = (self.width() - scaled.width()) // 2
        y = (self.height() - scaled.height()) // 2
        painter.drawImage(x, y, scaled)

    def _restart_on_end(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._player.setPosition(0)
            self._player.play()

    def pause(self):
        self._player.pause()

    def resume(self):
        self._player.play()

    def set_muted(self, muted):
        self._audio_output.setMuted(muted)


def _build_layer(path, parent):
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTENSIONS:
        return CoverImageLayer(path, parent)
    if ext in GIF_EXTENSIONS:
        return CoverGifLayer(path, parent)
    if ext in VIDEO_EXTENSIONS:
        return CoverVideoLayer(path, parent)
    raise ValueError(f"Unsupported background format: {ext}")


class BackgroundStack(QWidget):
    """Holds every loaded background, all preloaded, switching instantly."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layers = {}
        self._current_key = None
        self._muted = True

    def add_background(self, key, path):
        if key in self._layers:
            self.remove_background(key)
        widget = _build_layer(path, self)
        widget.setGeometry(self.rect())
        widget.lower()
        widget.hide()
        if hasattr(widget, "set_muted"):
            widget.set_muted(self._muted)
        self._layers[key] = widget
        return widget

    def remove_background(self, key):
        widget = self._layers.pop(key, None)
        if widget is not None:
            widget.deleteLater()
        if self._current_key == key:
            self._current_key = None

    def set_current(self, key):
        target = self._layers.get(key)
        if target is None:
            return
        for k, w in self._layers.items():
            if k != key:
                w.hide()
                w.pause()
        target.setGeometry(self.rect())
        target.show()
        target.raise_()
        target.resume()
        self._current_key = key

    def set_muted_all(self, muted):
        self._muted = muted
        for w in self._layers.values():
            w.set_muted(muted)

    def clear(self):
        for key in list(self._layers.keys()):
            self.remove_background(key)

    def resizeEvent(self, event):
        for w in self._layers.values():
            w.setGeometry(self.rect())
        super().resizeEvent(event)


# --------------------------------------------------------------------------- #
# Element overlay (drag & drop image / logo shown on top of the background)
# --------------------------------------------------------------------------- #

class ElementOverlay(QLabel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background: transparent;")
        self._pixmap = None
        self._scale = 0.5  # fraction of the screen height
        self.hide()

    def set_image(self, path):
        self._pixmap = QPixmap(path)
        self._refresh()

    def set_scale(self, fraction):
        self._scale = max(0.05, min(1.0, fraction))
        self._refresh()

    def _refresh(self):
        if self._pixmap and not self._pixmap.isNull():
            target_h = int(self.height() * self._scale)
            scaled = self._pixmap.scaledToHeight(
                max(target_h, 1), Qt.SmoothTransformation
            )
            self.setPixmap(scaled)

    def resizeEvent(self, event):
        self._refresh()
        super().resizeEvent(event)


# --------------------------------------------------------------------------- #
# Flash / white / strobe overlay
# --------------------------------------------------------------------------- #

class FlashOverlay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._color = QColor("#FFFFFF")
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    def set_color(self, color: QColor):
        self._color = color
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), self._color)


# --------------------------------------------------------------------------- #
# The full display window
# --------------------------------------------------------------------------- #

class DisplayWindow(QWidget):
    closed = Signal()

    def __init__(self):
        super().__init__()
        self.setWindowTitle("ARXIIV.STUDIO — DISPLAY")
        self.setStyleSheet("background-color:#000000;")
        self.setCursor(Qt.BlankCursor)

        self.background = BackgroundStack(self)
        self.element = ElementOverlay(self)
        self.flash = FlashOverlay(self)

        self._white_on = False
        self._strobe_timer = QTimer(self)
        self._strobe_timer.timeout.connect(self._strobe_tick)
        self._strobe_visible = False
        self._reactive_enabled = False
        self._reactive_color = QColor("#FFFFFF")
        self._reactive_flash_threshold = 0.86
        self._reactive_strobe = False
        self._reactive_last_flash = 0

        self._layout_children()

    def _layout_children(self):
        self.background.setGeometry(self.rect())
        self.element.setGeometry(self.rect())
        self.flash.setGeometry(self.rect())

    def resizeEvent(self, event):
        self._layout_children()
        super().resizeEvent(event)

    def closeEvent(self, event):
        self._strobe_timer.stop()
        self.closed.emit()
        super().closeEvent(event)

    def keyPressEvent(self, event):
        # Escape always gets you out of the fullscreen display, just in case.
        if event.key() == Qt.Key_Escape:
            self.close()
        else:
            super().keyPressEvent(event)

    # ----- Audio reactive effects ------------------------------------- #

    def set_audio_reactive(self, enabled, color=None):
        self._reactive_enabled = bool(enabled)
        if color is not None:
            self._reactive_color = color
        if not enabled:
            self.flash.hide()

    def audio_reactive_update(self, energy, bass, peak):
        # Keep this deliberately subtle. Continuous brightness modulation is
        # avoided; actual flashes are driven by the player's beat_signal.
        return

    def audio_reactive_beat(self, strength=1.0):
        if not self._reactive_enabled or self._white_on:
            return
        import time
        now = time.monotonic()
        if now - self._reactive_last_flash < 0.10:
            return
        self._reactive_last_flash = now
        duration = int(28 + 34 * max(0.0, min(1.0, strength)))
        c = QColor(self._reactive_color)
        c.setAlpha(235)
        self.trigger_flash(c, duration)

    # ----- Effects ---------------------------------------------------- #

    def trigger_flash(self, color: QColor, duration_ms: int = 150):
        self._strobe_timer.stop()
        self.flash.set_color(color)
        self.flash.show()
        self.flash.raise_()
        QTimer.singleShot(duration_ms, self._end_flash)

    def _end_flash(self):
        if not self._white_on and not self._strobe_timer.isActive():
            self.flash.hide()

    def toggle_strobe(self, color: QColor, interval_ms: int = 60):
        if self._strobe_timer.isActive():
            self._strobe_timer.stop()
            self.flash.hide()
            return False
        self._white_on = False
        self.flash.set_color(color)
        self._strobe_timer.setInterval(max(10, interval_ms))
        self._strobe_timer.start()
        return True

    def _strobe_tick(self):
        self._strobe_visible = not self._strobe_visible
        self.flash.setVisible(self._strobe_visible)
        if self._strobe_visible:
            self.flash.raise_()

    def toggle_white(self, color: QColor):
        self._strobe_timer.stop()
        self._white_on = not self._white_on
        if self._white_on:
            self.flash.set_color(color)
            self.flash.show()
            self.flash.raise_()
        else:
            self.flash.hide()
        return self._white_on

    def toggle_element(self):
        visible = not self.element.isVisible()
        self.element.setVisible(visible)
        self.element.raise_()
        return visible

    def set_element_image(self, path):
        self.element.set_image(path)

    def set_element_scale(self, fraction):
        self.element.set_scale(fraction)

    # ----- Background --------------------------------------------------- #

    def add_background(self, key, path):
        return self.background.add_background(key, path)

    def set_background(self, key):
        self.background.set_current(key)
        self.element.raise_()
        self.flash.raise_()

    def remove_background(self, key):
        self.background.remove_background(key)

    def set_background_muted(self, muted):
        self.background.set_muted_all(muted)
