"""
The CONTROL window — this stays on the operator's own monitor (like the
"presenter view" in PowerPoint) while DisplayWindow is pushed full-screen
onto the chosen output / projector screen. It shows a live thumbnail of the
output, and every control needed to run a show: effects, the drag & drop
element, backgrounds, and rebindable keyboard shortcuts.
"""

import os
import uuid
import json
import tempfile
import time
from collections import OrderedDict
import threading
import math

from PySide6.QtCore import Qt, QTimer, Signal, QObject, QThread, QPoint, QRect, QPropertyAnimation, QEasingCurve, QEvent, QRunnable, QThreadPool, QStandardPaths, QSettings
from PySide6.QtGui import QColor, QPixmap, QShortcut, QKeySequence, QPainter, QPen, QImage, QFontMetrics, QLinearGradient, QPainterPath
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QFrame, QPushButton, QComboBox,
    QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea, QListWidget,
    QListWidgetItem, QSlider, QColorDialog, QFileDialog, QKeySequenceEdit,
    QCheckBox, QSizePolicy, QLineEdit, QAbstractItemView, QGraphicsOpacityEffect, QMenu, QInputDialog, QSplitter,
)

from display_window import (
    DisplayWindow, ALL_BACKGROUND_EXTENSIONS, ELEMENT_EXTENSIONS,
)
from audio_player import AudioReactivePlayer


AUDIO_EXTENSIONS = (".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".opus", ".wma")
_METADATA_CACHE = OrderedDict()
_METADATA_CACHE_LOCK = threading.Lock()
_METADATA_CACHE_LIMIT = 512



def _clean_tag_value(value):
    """Return a clean, human-readable tag value or an empty string."""
    if value is None:
        return ""
    try:
        if hasattr(value, "text"):
            value = value.text
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ""
        value = str(value).replace("\x00", "").strip()
        return value
    except Exception:
        return ""


def _read_audio_metadata(path):
    """Read metadata robustly without decoding the audio stream.

    The filename is always used as a title fallback.  Missing tags/artwork are
    normal for WAV/old files, so the UI never receives an empty title.
    """
    path = os.path.abspath(path)
    try:
        st = os.stat(path)
        cache_key = (path, st.st_mtime_ns, st.st_size)
        with _METADATA_CACHE_LOCK:
            cached = _METADATA_CACHE.get(cache_key)
            if cached is not None:
                _METADATA_CACHE.move_to_end(cache_key)
                return cached
    except OSError:
        cache_key = None
    filename = os.path.basename(path)
    stem = os.path.splitext(filename)[0].strip()
    title = stem or filename or "UNTITLED"
    artist = ""
    cover = None
    duration_ms = 0

    try:
        from mutagen import File as MutagenFile
        audio = MutagenFile(path, easy=False)
        if audio is not None:
            try:
                duration_ms = max(0, int(float(getattr(audio.info, "length", 0.0)) * 1000.0))
            except Exception:
                duration_ms = 0
        tags = getattr(audio, "tags", None) if audio is not None else None

        # Generic/easy tags first: this handles many MP3/FLAC/OGG variants.
        if audio is not None:
            try:
                easy = getattr(audio, "tags", None)
                if easy:
                    for key in ("title", "TIT2", "©nam"):
                        value = _clean_tag_value(easy.get(key)) if hasattr(easy, "get") else ""
                        if value:
                            title = value
                            break
                    for key in ("artist", "TPE1", "©ART"):
                        value = _clean_tag_value(easy.get(key)) if hasattr(easy, "get") else ""
                        if value:
                            artist = value
                            break
            except Exception:
                pass

        if tags:
            for key in ("TIT2", "title", "©nam", "TITLE", "INAM", "WM/Title", "Title"):
                try:
                    value = _clean_tag_value(tags.get(key))
                except Exception:
                    value = ""
                if value:
                    title = value
                    break
            for key in ("TPE1", "artist", "©ART", "ARTIST", "IART", "WM/Author", "Author"):
                try:
                    value = _clean_tag_value(tags.get(key))
                except Exception:
                    value = ""
                if value:
                    artist = value
                    break

            # MP3 ID3 artwork.
            for key, value in getattr(tags, "items", lambda: [])():
                if str(key).upper().startswith("APIC") and hasattr(value, "data"):
                    cover = value.data
                    break

            # MP4/M4A artwork.
            if cover is None:
                try:
                    covr = tags.get("covr")
                    if covr:
                        cover = bytes(covr[0])
                except Exception:
                    pass

            # OGG/FLAC embedded FLAC picture block.
            if cover is None:
                try:
                    pictures = getattr(audio, "pictures", None)
                    if pictures:
                        cover = pictures[0].data
                except Exception:
                    pass
                if cover is None:
                    try:
                        import base64
                        from mutagen.flac import Picture
                        encoded = tags.get("metadata_block_picture")
                        if encoded:
                            raw = encoded[0] if isinstance(encoded, (list, tuple)) else encoded
                            cover = Picture(base64.b64decode(raw)).data
                    except Exception:
                        pass
    except Exception:
        # Unsupported/broken tags must never prevent a track from appearing.
        pass

    # Sidecar/folder artwork is a cheap and useful fallback.
    if cover is None:
        folder = os.path.dirname(path)
        for name in ("cover.jpg", "cover.jpeg", "cover.png", "folder.jpg", "folder.jpeg", "folder.png", "front.jpg", "front.png"):
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                try:
                    with open(candidate, "rb") as fh:
                        cover = fh.read()
                    break
                except OSError:
                    pass

    result = (title, artist, cover, duration_ms)
    if cache_key is not None:
        with _METADATA_CACHE_LOCK:
            _METADATA_CACHE[cache_key] = result
            _METADATA_CACHE.move_to_end(cache_key)
            while len(_METADATA_CACHE) > _METADATA_CACHE_LIMIT:
                _METADATA_CACHE.popitem(last=False)
    return result


class CoverSignals(QObject):
    loaded = Signal(object, str, str, object, int)


class CoverWorker(QRunnable):
    def __init__(self, tile, path):
        super().__init__()
        self.tile = tile
        self.path = path
        self.signals = CoverSignals()
        self.signals.loaded.connect(tile._apply_metadata)

    def run(self):
        title, artist, cover, duration_ms = _read_audio_metadata(self.path)
        self.signals.loaded.emit(self.tile, title, artist, cover, duration_ms)


class FolderScanner(QObject):
    batch = Signal(list)
    finished = Signal()
    error = Signal(str)

    def __init__(self, folders):
        super().__init__()
        self.folders = list(dict.fromkeys(folders))
        self._stop_requested = False

    def stop(self):
        self._stop_requested = True

    def run(self):
        try:
            seen = set()
            batch = []
            for root in self.folders:
                if self._stop_requested:
                    break
                if os.path.isfile(root):
                    if os.path.splitext(root)[1].lower() in AUDIO_EXTENSIONS:
                        batch.append(os.path.abspath(root))
                    continue
                if not os.path.isdir(root):
                    continue
                for current, dirs, files in os.walk(root):
                    dirs[:] = [d for d in dirs if not d.startswith(".") and d not in {"__MACOSX", "System Volume Information", "$RECYCLE.BIN"}]
                    dirs.sort(key=str.casefold)
                    for name in sorted(files, key=str.casefold):
                        if self._stop_requested:
                            break
                        path = os.path.normcase(os.path.abspath(os.path.join(current, name)))
                        if os.path.splitext(name)[1].lower() not in AUDIO_EXTENSIONS or path in seen:
                            continue
                        seen.add(path)
                        batch.append(path)
                        if len(batch) >= 128:
                            self.batch.emit(batch)
                            batch = []
            if batch:
                self.batch.emit(batch)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            self.finished.emit()



class TrackTile(QWidget):
    duration_changed = Signal()
    """Compact queue tile with optional lightweight artwork and clean typography."""

    _pool = QThreadPool.globalInstance()
    _pool.setMaxThreadCount(max(2, min(4, _pool.maxThreadCount())))

    def __init__(self, path, parent_queue=None):
        super().__init__(parent_queue)
        self.path = os.path.abspath(path)
        self.queue = parent_queue
        self._press_pos = None
        self._full_title = os.path.splitext(os.path.basename(self.path))[0]
        self._artist = ""
        self._duration_ms = 0
        self._gradient_phase = 0.0
        self._gradient_opacity = 0.0
        self._gradient_target = 0.0
        self._marker_color = ""
        self._gradient_timer = QTimer(self)
        self._gradient_timer.setInterval(40)
        self._gradient_timer.timeout.connect(self._advance_gradient)
        self.setObjectName("trackTile")
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.setCursor(Qt.OpenHandCursor)
        self.setMinimumHeight(68)
        self.setMaximumHeight(68)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setToolTip(self._full_title)

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(9)

        self.select_box = QCheckBox()
        self.select_box.setObjectName("trackSelect")
        self.select_box.setToolTip("Select this track for batch removal")
        self.select_box.setFixedWidth(20)
        self.select_box.setFocusPolicy(Qt.NoFocus)
        self.select_box.toggled.connect(self._selection_toggled)
        row.addWidget(self.select_box)

        self.cover = QLabel()
        self.cover.setObjectName("trackCover")
        self.cover.setFixedSize(50, 50)
        self.cover.setAlignment(Qt.AlignCenter)
        self.cover.setText("♪")
        row.addWidget(self.cover)

        self.number = QLabel("00")
        self.number.setObjectName("trackNumber")
        self.number.setFixedWidth(25)
        self.number.setAlignment(Qt.AlignCenter)
        row.addWidget(self.number)

        self.marker = QFrame()
        self.marker.setObjectName("trackMarker")
        self.marker.setFixedSize(10, 10)
        self.marker.setToolTip("Track marker")
        self.marker.setFrameShape(QFrame.NoFrame)
        self.marker.hide()
        row.addWidget(self.marker)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)
        self.title = QLabel()
        self.title.setObjectName("trackTitle")
        self.title.setTextInteractionFlags(Qt.NoTextInteraction)
        self.title.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.meta = QLabel()
        self.meta.setObjectName("trackMeta")
        self.meta.setTextInteractionFlags(Qt.NoTextInteraction)
        self.meta.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        text_col.addWidget(self.title)
        text_col.addWidget(self.meta)
        row.addLayout(text_col, 1)

        self.play_hint = QLabel("▶")
        self.play_hint.setObjectName("trackHint")
        self.play_hint.setAlignment(Qt.AlignCenter)
        self.play_hint.setFixedWidth(22)
        row.addWidget(self.play_hint)

        self._refresh_text()
        # Metadata/artwork is loaded off the UI thread so large playlists stay responsive.
        self._pool.start(CoverWorker(self, self.path))

    def _elide(self, text, width):
        metrics = QFontMetrics(self.font())
        return metrics.elidedText(text, Qt.ElideRight, max(40, width))

    def _refresh_text(self):
        width = self.width() - 46 - 25 - 8 - 22 - 45 - 20
        self.title.setText(self._elide(self._full_title, width))
        ext = os.path.splitext(self.path)[1].upper().lstrip(".") or "AUDIO"
        meta = self._artist or f"NO TAG • {ext}"
        if self._duration_ms > 0:
            meta = f"{meta}  •  {self._fmt_ms(self._duration_ms)}"
        self.meta.setText(self._elide(meta, width))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refresh_text()

    def _apply_metadata(self, tile, title, artist, cover_data, duration_ms):
        if tile is not self or self.path != tile.path:
            return
        self._full_title = title or self._full_title
        self._artist = artist or ""
        self._duration_ms = max(0, int(duration_ms or 0))
        if self.queue is not None:
            self.queue.duration_changed.emit()
        self.setToolTip(self._full_title + (f" — {self._artist}" if self._artist else ""))
        self._refresh_text()
        if cover_data:
            image = QImage()
            if image.loadFromData(cover_data):
                pix = QPixmap.fromImage(image).scaled(46, 46, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
                self.cover.setPixmap(pix)
                self.cover.setText("")

    @staticmethod
    def _fmt_ms(ms):
        total = max(0, int(ms)) // 1000
        return f"{total // 60:02d}:{total % 60:02d}"

    def set_marker(self, color):
        # Marker is stored on the tile and painted directly in paintEvent.
        # This avoids stylesheet/layout conflicts and guarantees visibility.
        self._marker_color = str(color or "")
        self.marker.setVisible(bool(self._marker_color))
        self.marker.setToolTip(f"Marker: {self._marker_color}" if self._marker_color else "No track marker")
        self.update()

    def _selection_toggled(self, checked):
        if self.queue is not None:
            self.queue.set_tile_selected(self, checked)

    def _advance_gradient(self):
        # One lightweight timer drives only the currently playing tile.
        # Opacity eases in/out so the texture never pops when playback changes.
        if self._gradient_opacity < self._gradient_target:
            self._gradient_opacity = min(self._gradient_target, self._gradient_opacity + 0.085)
        elif self._gradient_opacity > self._gradient_target:
            self._gradient_opacity = max(self._gradient_target, self._gradient_opacity - 0.085)
        if self._gradient_opacity > 0.0:
            self._gradient_phase = (self._gradient_phase + 0.008) % 1.0
        if self._gradient_opacity <= 0.001 and self._gradient_target <= 0.0:
            self._gradient_opacity = 0.0
            self._gradient_timer.stop()
        self.update()

    def _set_gradient_active(self, active):
        self._gradient_target = 1.0 if active else 0.0
        if not self._gradient_timer.isActive():
            self._gradient_timer.start()
        self.update()

    def paintEvent(self, event):
        # Active-track texture inspired by the supplied halftone reference:
        # a monochrome dither field whose dot density moves across the tile.
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)
        radius = 6.0
        current = bool(self.property("current"))
        selected = bool(self.property("selected"))

        painter.setBrush(QColor("#0A0A0A"))
        painter.setPen(QPen(QColor("#1F1F1F"), 1))
        painter.drawRoundedRect(rect, radius, radius)

        opacity = self._gradient_opacity
        if opacity > 0.001:
            painter.save()
            path = QPainterPath()
            path.addRoundedRect(rect, radius, radius)
            painter.setClipPath(path)

            # Subtle monochrome halftone: white dot density rises/falls along X.
            # The field is deliberately low-contrast so the typography stays clean.
            cell = 7
            dot_r = 1.05
            phase_px = self._gradient_phase * (cell * 8)
            for y in range(rect.top() + 3, rect.bottom() - 2, cell):
                for x in range(rect.left() - cell, rect.right() + cell, cell):
                    nx = ((x - rect.left() + phase_px) / max(1.0, rect.width()))
                    wave = 0.5 + 0.5 * math.cos((nx * math.pi * 2.0) - math.pi)
                    density = max(0.0, min(1.0, 1.0 - nx * 1.05))
                    strength = max(0.0, min(1.0, density * 0.62 + wave * 0.12))
                    alpha = int(118 * strength * opacity)
                    if alpha <= 4:
                        continue
                    painter.setPen(Qt.NoPen)
                    painter.setBrush(QColor(255, 255, 255, alpha))
                    painter.drawEllipse(QPoint(x, y), dot_r, dot_r)
            painter.restore()

        border = QColor("#FFFFFF") if selected else QColor("#1F1F1F")
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(border, 2 if selected else 1))
        painter.drawRoundedRect(rect, radius, radius)

        if self._marker_color:
            marker_rect = QRect(rect.right() - 15, rect.top() + 8, 8, 8)
            painter.setPen(QPen(QColor(255, 255, 255, 190), 1))
            painter.setBrush(QColor(self._marker_color))
            painter.drawRoundedRect(marker_rect, 2, 2)

        painter.end()

    def set_selected(self, selected, emit=True):
        selected = bool(selected)
        if self.select_box.isChecked() == selected:
            self.queue and self.queue._apply_tile_selection(self, selected)
            return
        self.select_box.blockSignals(True)
        self.select_box.setChecked(selected)
        self.select_box.blockSignals(False)
        if self.queue is not None:
            self.queue._apply_tile_selection(self, selected)

    def set_number(self, number):
        self.number.setText(f"{number:02d}")

    def set_filtered(self, hidden):
        self.setVisible(not hidden)

    def mousePressEvent(self, event):
        if event.button() == Qt.RightButton:
            if self.queue is not None:
                self.queue.show_track_context_menu(self, event.globalPosition().toPoint())
            event.accept()
            return
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            if self.queue is not None:
                self.queue.setFocus()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and self.queue is not None:
            self.queue.track_activated.emit(self.path)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event):
        if (
            self._press_pos is not None
            and event.buttons() & Qt.LeftButton
            and self.queue is not None
            and not self.queue.is_dragging
        ):
            distance = (event.position().toPoint() - self._press_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self.queue.start_animated_drag(self, event.globalPosition().toPoint(), self._press_pos)
                self._press_pos = None
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.queue is not None and self.queue.is_dragging:
            return
        if event.button() == Qt.LeftButton:
            self.setCursor(Qt.OpenHandCursor)
        super().mouseReleaseEvent(event)


class TrackQueueWidget(QScrollArea):
    """Scrollable playlist with animated drag/reorder behavior.

    A placeholder is moved under the cursor while a slightly enlarged ghost
    tile follows the mouse. Neighbor tiles animate to make room instead of
    snapping instantly.
    """

    track_activated = Signal(str)
    track_removed = Signal(str)
    tracks_removed = Signal(list)
    marker_changed = Signal(str, str)
    queue_start_time_requested = Signal(str, bool)
    duration_changed = Signal()
    selection_changed = Signal(int)
    order_changed = Signal()
    drag_state_changed = Signal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("trackQueue")
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMinimumHeight(420)
        self.setMinimumWidth(500)
        self.setMouseTracking(True)

        # IMPORTANT: eventFilter can be triggered by Qt during setWidget().
        # Initialize all state used by eventFilter before installing the widget.
        self._tiles = []
        self._placeholder = None
        self._drag_tile = None
        self._ghost = None
        self._drag_offset = QPoint()
        self._drag_animations = []
        self.is_dragging = False
        self._last_placeholder_index = -1
        self._filter_active = False
        self._selected_tiles = []

        self._content = QWidget()
        self._content.setObjectName("trackQueueContent")
        self._layout = QVBoxLayout(self._content)
        self._layout.setContentsMargins(6, 6, 6, 6)
        self._layout.setSpacing(5)
        self._layout.addStretch(1)
        self.setWidget(self._content)


        self.viewport().setMouseTracking(True)
        self.viewport().installEventFilter(self)
        QApplication.instance().installEventFilter(self)

    def count(self):
        return len(self._tiles)

    def paths(self):
        return [tile.path for tile in self._tiles]

    def add_track(self, path):
        tile = TrackTile(path, self)
        tile.installEventFilter(self)
        # Insert before the permanent stretch item.
        self._tiles.append(tile)
        self._layout.insertWidget(self._layout.count() - 1, tile)
        self._renumber()
        return tile

    def remove_all(self):
        for tile in self._tiles:
            tile.deleteLater()
        self._tiles.clear()
        self.clear_selection()
        self._renumber()

    def selected_tiles(self):
        return [tile for tile in self._selected_tiles if tile in self._tiles and tile.select_box.isChecked()]

    def _apply_tile_selection(self, tile, selected):
        tile.setProperty("selected", bool(selected))
        tile.select_box.setProperty("selected", bool(selected))
        tile.style().unpolish(tile)
        tile.style().polish(tile)
        tile.update()

    def set_tile_selected(self, tile, selected):
        if tile not in self._tiles:
            return
        selected = bool(selected)
        if selected and tile not in self._selected_tiles:
            self._selected_tiles.append(tile)
        elif not selected and tile in self._selected_tiles:
            self._selected_tiles.remove(tile)
        self._apply_tile_selection(tile, selected)
        self.selection_changed.emit(len(self.selected_tiles()))

    def clear_selection(self):
        for tile in list(self._selected_tiles):
            tile.set_selected(False)
        self._selected_tiles = []
        self.selection_changed.emit(0)

    def select_all(self):
        for tile in self._tiles:
            tile.set_selected(True)
        self._selected_tiles = list(self._tiles)
        self.selection_changed.emit(len(self._selected_tiles))

    def set_current_path(self, path):
        for tile in self._tiles:
            is_current = tile.path == path
            tile.setProperty("current", is_current)
            tile._set_gradient_active(is_current)
            tile.style().unpolish(tile)
            tile.style().polish(tile)
            tile.update()
        for tile in self._tiles:
            if tile.path == path:
                self.ensureWidgetVisible(tile)
                break

    def filter(self, query):
        q = query.strip().casefold()
        self._filter_active = bool(q)
        visible = 0
        for tile in self._tiles:
            hidden = bool(q) and q not in os.path.basename(tile.path).casefold() and q not in tile.path.casefold()
            tile.set_filtered(hidden)
            if not hidden:
                visible += 1
        self._renumber()
        return visible

    def visible_count(self):
        return sum(tile.isVisible() for tile in self._tiles)

    def _renumber(self):
        for index, tile in enumerate(self._tiles, 1):
            tile.set_number(index)

    def _cleanup_drag_visuals(self):
        if self._ghost is not None:
            self._ghost.deleteLater()
            self._ghost = None
        if self._placeholder is not None:
            self._layout.removeWidget(self._placeholder)
            self._placeholder.deleteLater()
            self._placeholder = None

    def eventFilter(self, obj, event):
        if self.is_dragging:
            if event.type() == QEvent.ApplicationDeactivate:
                self.finish_animated_drag()
                return True
            if event.type() == QEvent.MouseMove:
                self._update_drag(self.viewport().mapFromGlobal(event.globalPosition().toPoint()))
                return True
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                self.finish_animated_drag()
                return True
        return super().eventFilter(obj, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._ghost is not None:
            self._update_ghost_position(QPoint(self.viewport().width() // 2, self._ghost.y()))

    def _cancel_animations(self):
        for anim in self._drag_animations:
            anim.stop()
            anim.deleteLater()
        self._drag_animations.clear()

    def _animate_layout_transition(self, old_geometries):
        self._cancel_animations()
        self._layout.activate()
        for tile, old_rect in old_geometries.items():
            if tile.isHidden():
                continue
            new_rect = tile.geometry()
            if new_rect == old_rect:
                continue
            tile.setGeometry(old_rect)
            anim = QPropertyAnimation(tile, b"geometry", self)
            anim.setDuration(155)
            anim.setStartValue(old_rect)
            anim.setEndValue(new_rect)
            anim.setEasingCurve(QEasingCurve.OutCubic)
            anim.finished.connect(lambda a=anim: self._drag_animations.remove(a) if a in self._drag_animations else None)
            self._drag_animations.append(anim)
            anim.start()

    def start_animated_drag(self, tile, global_pos, local_offset):
        if self.is_dragging or self._filter_active:
            return
        self.is_dragging = True
        self.drag_state_changed.emit(True)
        self._drag_tile = tile
        self._drag_offset = local_offset
        self._cancel_animations()

        old_geometries = {t: t.geometry() for t in self._tiles if t is not tile and t.isVisible()}
        source_index = [t for t in self._tiles if t.isVisible()].index(tile)
        self._layout.removeWidget(tile)
        tile.hide()
        self._placeholder = QFrame(self._content)
        self._placeholder.setObjectName("dragPlaceholder")
        self._placeholder.setFixedHeight(tile.height())
        self._placeholder.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._layout.insertWidget(source_index, self._placeholder)
        self._layout.activate()
        self._animate_layout_transition(old_geometries)
        self._last_placeholder_index = source_index

        ghost = QFrame(self.viewport())
        ghost.setObjectName("dragGhost")
        ghost.setAttribute(Qt.WA_TransparentForMouseEvents)
        ghost.setFixedHeight(tile.height())
        ghost.setFixedWidth(max(250, int(self.viewport().width() * 0.82)))
        ghost.setStyleSheet(
            "QFrame#dragGhost { background: #111111; border: 1px dashed #777777; border-radius: 6px; }"
        )
        ghost_label = QLabel("DRAGGING  •  " + os.path.basename(tile.path), ghost)
        ghost_label.setStyleSheet("color: #777777; font-size: 11px; font-weight: bold; padding-left: 12px;")
        ghost_label.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        ghost_label.setGeometry(0, 0, ghost.width(), ghost.height())
        ghost.show()
        start_local = self.viewport().mapFromGlobal(global_pos - local_offset)
        start_rect = QRect(start_local, ghost.sizeHint())
        ghost.setGeometry(start_rect)
        ghost.raise_()
        self._ghost = ghost
        self._update_ghost_position(self.viewport().mapFromGlobal(global_pos))

        target = self._scaled_ghost_rect(self._ghost.geometry().center())
        anim = QPropertyAnimation(self._ghost, b"geometry", self)
        anim.setDuration(150)
        anim.setStartValue(self._ghost.geometry())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.OutBack)
        anim.start()
        self._drag_animations.append(anim)

        self.viewport().setCursor(Qt.ClosedHandCursor)
        self.setFocus()

    def _scaled_ghost_rect(self, center):
        if self._ghost is None:
            return QRect()
        base_w = max(250, int(self.viewport().width() * 0.82))
        base_h = max(54, self._ghost.height())
        w = int(base_w * 1.06)
        h = int(base_h * 1.06)
        return QRect(center.x() - w // 2, center.y() - h // 2, w, h)

    def _update_ghost_position(self, cursor_pos):
        if self._ghost is None:
            return
        target = self._scaled_ghost_rect(cursor_pos - self._drag_offset + QPoint(self._ghost.width() // 2, self._ghost.height() // 2))
        self._ghost.setGeometry(target)

    def _update_drag(self, viewport_pos):
        if not self.is_dragging or self._drag_tile is None:
            return
        # Auto-scroll near the queue edges.
        edge = 52
        if viewport_pos.y() < edge:
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - 22)
        elif viewport_pos.y() > self.viewport().height() - edge:
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() + 22)

        self._update_ghost_position(viewport_pos)
        content_pos = self._content.mapFrom(self.viewport(), viewport_pos)
        candidates = [t for t in self._tiles if t is not self._drag_tile and t.isVisible()]
        target = len(candidates)
        for idx, tile in enumerate(candidates):
            center_y = tile.geometry().center().y()
            if content_pos.y() < center_y:
                target = idx
                break
        self._move_placeholder(target)

    def _move_placeholder(self, target_index):
        if self._placeholder is None or target_index == self._last_placeholder_index:
            return
        old_geometries = {t: t.geometry() for t in self._tiles if t is not self._drag_tile and t.isVisible()}
        self._layout.removeWidget(self._placeholder)
        self._layout.insertWidget(target_index, self._placeholder)
        self._last_placeholder_index = target_index
        self._animate_layout_transition(old_geometries)

    def finish_animated_drag(self):
        if not self.is_dragging or self._drag_tile is None:
            return
        old_geometries = {t: t.geometry() for t in self._tiles if t is not self._drag_tile and t.isVisible()}
        target_index = self._last_placeholder_index
        self._cancel_animations()
        if self._placeholder is not None:
            self._layout.removeWidget(self._placeholder)
            self._placeholder.deleteLater()
            self._placeholder = None
        self._layout.insertWidget(target_index, self._drag_tile)
        self._drag_tile.show()
        self._tiles.remove(self._drag_tile)
        self._tiles.insert(target_index, self._drag_tile)
        self._layout.activate()
        if self._ghost is not None:
            self._ghost.deleteLater()
            self._ghost = None
        self._animate_layout_transition(old_geometries)
        # Small settling pulse via opacity for a polished drop.
        dropped_tile = self._drag_tile
        effect = QGraphicsOpacityEffect(dropped_tile)
        effect.setOpacity(0.55)
        dropped_tile.setGraphicsEffect(effect)
        fade = QPropertyAnimation(effect, b"opacity", self)
        fade.setDuration(180)
        fade.setStartValue(0.55)
        fade.setEndValue(1.0)
        fade.setEasingCurve(QEasingCurve.OutCubic)
        fade.finished.connect(lambda: dropped_tile.setGraphicsEffect(None))
        fade.start()
        self._drag_animations.append(fade)

        self._drag_tile = None
        self.is_dragging = False
        self._last_placeholder_index = -1
        self._renumber()
        self.viewport().setCursor(Qt.ArrowCursor)
        self.drag_state_changed.emit(False)
        self.order_changed.emit()

    def show_track_context_menu(self, tile, global_pos):
        # Right-click is a context action, never a selection action.
        # Selection remains exclusively controlled by the checkbox.
        selected = [tile]
        count = 1
        menu = QMenu(self)
        marker_menu = menu.addMenu("TRACK MARKER")
        marker_colors = [
            ("RED", "#FF4D4D"), ("ORANGE", "#FF9F43"), ("YELLOW", "#FFD93D"),
            ("GREEN", "#4DFF88"), ("BLUE", "#4DA6FF"), ("VIOLET", "#B56CFF"),
        ]
        marker_actions = {}
        for label, color in marker_colors:
            act = marker_menu.addAction(f"■  {label}")
            marker_actions[act] = color
        marker_actions[marker_menu.addAction("□  CLEAR MARKER")] = ""
        menu.addSeparator()
        start_action = menu.addAction("SET START TIME…")
        reset_start = menu.addAction("RESET START TIME")
        menu.addSeparator()
        remove = menu.addAction("REMOVE THIS TRACK")
        chosen = menu.exec(global_pos)
        if chosen in marker_actions:
            color = marker_actions[chosen]
            for item in selected:
                self.marker_changed.emit(item.path, color)
                item.set_marker(color)
        elif chosen is start_action:
            self.queue_start_time_requested.emit(tile.path, False)
        elif chosen is reset_start:
            self.queue_start_time_requested.emit(tile.path, True)
        elif chosen is remove:
            self.remove_tile(tile)

    def remove_tile(self, tile):
        if tile not in self._tiles or self.is_dragging:
            return
        self.remove_tiles([tile])

    def remove_selected(self):
        self.remove_tiles(self.selected_tiles())

    def remove_tiles(self, tiles):
        if self.is_dragging:
            return
        targets = [tile for tile in tiles if tile in self._tiles]
        if not targets:
            return
        paths = [tile.path for tile in targets]
        for tile in targets:
            self._layout.removeWidget(tile)
            self._tiles.remove(tile)
            tile.deleteLater()
        self.clear_selection()
        self._renumber()
        self.order_changed.emit()
        if len(paths) == 1:
            self.track_removed.emit(paths[0])
        self.tracks_removed.emit(paths)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            if self.selected_tiles():
                self.remove_selected()
            event.accept()
            return
        if event.key() == Qt.Key_Escape:
            self.clear_selection()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            current = next((t for t in self._tiles if t.property("current")), None)
            if current:
                self.track_activated.emit(current.path)
                event.accept()
                return
        super().keyPressEvent(event)

# --------------------------------------------------------------------------- #
# Small reusable UI pieces
# --------------------------------------------------------------------------- #

def hairline():
    line = QFrame()
    line.setObjectName("hairline")
    line.setFrameShape(QFrame.HLine)
    return line


def section_title(text):
    label = QLabel(f"[ {text} ]")
    label.setObjectName("sectionTitle")
    return label


class DropZone(QFrame):
    """A bordered panel that accepts drag & drop files of given extensions."""

    files_dropped = Signal(list)

    def __init__(self, text, extensions, parent=None):
        super().__init__(parent)
        self.extensions = extensions
        self.setObjectName("panel")
        self.setAcceptDrops(True)
        self.setMinimumHeight(64)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        self.label = QLabel(text)
        self.label.setObjectName("dim")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setWordWrap(True)
        layout.addWidget(self.label)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths = []
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path and (os.path.isdir(path) or os.path.splitext(path)[1].lower() in self.extensions):
                paths.append(path)
        if paths:
            self.files_dropped.emit(paths)
        event.acceptProposedAction()


class ColorSwatchButton(QPushButton):
    """A button that shows / picks a color."""

    color_changed = Signal(QColor)

    def __init__(self, initial=QColor("#FFFFFF"), parent=None):
        super().__init__(parent)
        self._color = initial
        self.setFixedHeight(32)
        self.clicked.connect(self._pick)
        self._refresh()

    def color(self):
        return self._color

    def _pick(self):
        chosen = QColorDialog.getColor(self._color, self, "SELECT EFFECT COLOR")
        if chosen.isValid():
            self._color = chosen
            self._refresh()
            self.color_changed.emit(self._color)

    def _refresh(self):
        self.setText(self._color.name().upper())
        self.setStyleSheet(
            f"background-color:{self._color.name()};"
            f"color:{'#000000' if self._color.lightness() > 128 else '#FFFFFF'};"
            f"border:1px solid #333333;"
        )



class SpectrumWidget(QWidget):
    """Compact live spectrum visualization for the operator monitor."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.bars = [0.0] * 32
        self.energy = 0.0
        self.bass = 0.0
        self.peak = 0.0
        self.setMinimumHeight(100)
        self.setObjectName("panel")

    def set_analysis(self, energy, bass, peak, bars):
        self.energy = energy
        self.bass = bass
        self.peak = peak
        self.bars = list(bars)
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, False)
        r = self.rect().adjusted(8, 8, -8, -8)
        p.fillRect(self.rect(), QColor("#050505"))

        # Energy/bass meters.
        p.setPen(QPen(QColor("#333333"), 1))
        p.drawRect(r)
        p.setPen(QPen(QColor("#FFFFFF"), 1))
        p.drawText(r.left() + 8, r.top() + 14,
                   f"ENERGY {self.energy:0.2f}   BASS {self.bass:0.2f}   PEAK {self.peak:0.2f}")

        graph = r.adjusted(8, 22, -8, -8)
        gap = 2
        bw = max(1, int((graph.width() - gap * 31) / 32))
        for i, value in enumerate(self.bars):
            h = int((graph.height()) * max(0.0, min(1.0, value)))
            x = graph.left() + i * (bw + gap)
            y = graph.bottom() - h
            p.fillRect(x, y, bw, max(1, h), QColor("#FFFFFF"))

# --------------------------------------------------------------------------- #
# Main control window
# --------------------------------------------------------------------------- #

class ControlWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ARXIIV.STUDIO — CONTROL")
        self.resize(1280, 760)
        self.setMinimumSize(1080, 680)

        self.display_window = None
        self.background_items = {}   # key -> {"path", "name"}
        self.element_path = None

        self.audio = AudioReactivePlayer(self)
        self.audio.analysis.connect(self._on_audio_analysis)
        self.audio.beat_signal.connect(self._on_audio_beat)
        self.audio.position_changed.connect(self._on_audio_position)
        self.audio.state_changed.connect(self._on_audio_state)
        self.audio.track_changed.connect(self._on_audio_track)
        self.audio.crossfade_finished.connect(self._on_crossfade_finished)
        self.audio.error.connect(lambda msg: self.status_label.setText("AUDIO ERROR: " + msg) if hasattr(self, "status_label") else None)

        self._audio_tracks = []
        self._audio_index = -1
        self._scanner_thread = None
        self._scanner = None
        self._loading_audio = False
        self._close_after_scan = False
        self._audio_reactive = True
        self._reactive_sensitivity = 55
        self._last_analysis = (0.0, 0.0, 0.0)
        self._crossfade_triggered = False
        self._track_markers = {}
        self._track_start_ms = {}
        self._queue_path = self._queue_state_path()
        self._queue_loaded = False

        self._build_ui()
        self._build_shortcuts()
        self._load_queue_state()

        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(250)
        self.preview_timer.timeout.connect(self._refresh_preview)

    # ------------------------------------------------------------------ #
    # UI construction
    # ------------------------------------------------------------------ #

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 14)
        root.setSpacing(10)

        root.addLayout(self._build_header())
        root.addWidget(hairline())

        # Three independently resizable work areas, like dock panels in a video editor.
        # QSplitter handles resizing natively; no polling/timer is needed for layout.
        self.workspace_splitter = QSplitter(Qt.Horizontal)
        self.workspace_splitter.setObjectName("workspaceSplitter")
        self.workspace_splitter.setChildrenCollapsible(False)
        self.workspace_splitter.setHandleWidth(7)

        left_panel = QWidget()
        left_panel.setMinimumWidth(300)
        left_layout = self._build_left_column()
        left_panel.setLayout(left_layout)

        middle_panel = self._build_right_column()
        middle_panel.setMinimumWidth(230)

        queue_panel = self._build_playlist_sidebar()
        queue_panel.setMinimumWidth(280)
        queue_panel.setMaximumWidth(16777215)

        self.workspace_splitter.addWidget(left_panel)
        self.workspace_splitter.addWidget(middle_panel)
        self.workspace_splitter.addWidget(queue_panel)
        self.workspace_splitter.setStretchFactor(0, 5)
        self.workspace_splitter.setStretchFactor(1, 2)
        self.workspace_splitter.setStretchFactor(2, 3)
        self.workspace_splitter.setSizes([850, 300, 440])
        self.workspace_splitter.splitterMoved.connect(self._save_workspace_sizes)
        root.addWidget(self.workspace_splitter, 1)
        self._restore_workspace_sizes()

    def _workspace_settings(self):
        return QSettings("ARXIIV.STUDIO", "ControlWindow")

    def _save_workspace_sizes(self, *_args):
        splitter = getattr(self, "workspace_splitter", None)
        if splitter is None:
            return
        try:
            self._workspace_settings().setValue("workspace/splitter_sizes", splitter.sizes())
        except Exception:
            pass

    def _restore_workspace_sizes(self):
        try:
            sizes = self._workspace_settings().value("workspace/splitter_sizes", None)
            if isinstance(sizes, (list, tuple)) and len(sizes) == 3:
                parsed = [max(1, int(value)) for value in sizes]
                self.workspace_splitter.setSizes(parsed)
        except (TypeError, ValueError, RuntimeError):
            # Invalid old settings must never prevent the control window from opening.
            self.workspace_splitter.setSizes([850, 300, 440])

    def _build_header(self):
        header = QHBoxLayout()
        brand = QLabel("ARXIIV.STUDIO")
        brand.setObjectName("brand")
        header.addWidget(brand)
        header.addStretch(1)

        header.addWidget(QLabel("SCREEN:"))
        self.screen_combo = QComboBox()
        self.screen_combo.setMinimumWidth(220)
        self._populate_screens()
        header.addWidget(self.screen_combo)

        self.start_button = QPushButton("OPEN DISPLAY")
        self.start_button.clicked.connect(self._toggle_display)
        header.addWidget(self.start_button)
        return header

    def _build_left_column(self):
        col = QVBoxLayout()
        col.setSpacing(8)
        col.setContentsMargins(0, 0, 0, 0)
        col.addWidget(section_title("PREVIEW"))

        self.preview_frame = QFrame()
        self.preview_frame.setObjectName("panel")
        self.preview_frame.setMinimumSize(180, 100)
        self.preview_frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.preview_frame.setMaximumHeight(240)
        pv_layout = QVBoxLayout(self.preview_frame)
        pv_layout.setContentsMargins(0, 0, 0, 0)
        self.preview_label = QLabel("NO SIGNAL")
        self.preview_label.setObjectName("dim")
        self.preview_label.setAlignment(Qt.AlignCenter)
        pv_layout.addWidget(self.preview_label)
        col.addWidget(self.preview_frame)

        self.status_label = QLabel("DISPLAY: OFFLINE")
        self.status_label.setObjectName("dim")
        col.addWidget(self.status_label)
        col.addWidget(section_title("PLAYER"))

        player_drop = DropZone(
            "DROP AUDIO FILES OR A FOLDER HERE\nMP3 / WAV / FLAC / OGG / M4A / AAC / OPUS",
            AUDIO_EXTENSIONS,
        )
        player_drop.files_dropped.connect(self._add_audio_tracks)
        col.addWidget(player_drop)

        browse_row = QHBoxLayout()
        add_audio_btn = QPushButton("＋ FILES")
        add_audio_btn.clicked.connect(self._browse_audio)
        add_folder_btn = QPushButton("▣ LOAD FOLDER")
        add_folder_btn.setToolTip("Load every supported audio file from this folder and its subfolders")
        add_folder_btn.clicked.connect(self._browse_audio_folder)
        browse_row.addWidget(add_audio_btn)
        browse_row.addWidget(add_folder_btn)
        col.addLayout(browse_row)

        self.track_label = QLabel("NO TRACK SELECTED")
        self.track_label.setObjectName("dim")
        self.track_label.setWordWrap(True)
        col.addWidget(self.track_label)

        prow = QHBoxLayout()
        self.prev_button = QPushButton("◀")
        self.prev_button.setToolTip("Previous track • crossfade when enabled")
        self.play_button = QPushButton("▶  PLAY")
        self.play_button.setMinimumWidth(105)
        self.next_button = QPushButton("▶")
        self.next_button.setToolTip("Next track • crossfade when enabled")
        self.prev_button.clicked.connect(self._prev_track)
        self.play_button.clicked.connect(self._toggle_play)
        self.next_button.clicked.connect(self._next_track)
        prow.addWidget(self.prev_button)
        prow.addWidget(self.play_button, 1)
        prow.addWidget(self.next_button)
        col.addLayout(prow)

        self.position_slider = QSlider(Qt.Horizontal)
        self.position_slider.setRange(0, 0)
        self.position_slider.sliderMoved.connect(self.audio.set_position)
        col.addWidget(self.position_slider)

        self.time_label = QLabel("00:00  /  00:00")
        self.time_label.setObjectName("dim")
        col.addWidget(self.time_label)

        vrow = QHBoxLayout()
        vrow.addWidget(QLabel("VOLUME"))
        self.volume_slider = QSlider(Qt.Horizontal)
        self.volume_slider.setRange(0, 100)
        self.volume_slider.setValue(85)
        self.volume_value = QLabel("85%")
        self.volume_slider.valueChanged.connect(self.audio.set_volume)
        self.volume_slider.valueChanged.connect(lambda v: self.volume_value.setText(f"{v}%"))
        vrow.addWidget(self.volume_slider)
        vrow.addWidget(self.volume_value)
        col.addLayout(vrow)

        crossfade_row = QHBoxLayout()
        self.crossfade_checkbox = QCheckBox("CROSSFADE")
        self.crossfade_checkbox.setToolTip("Automatically crossfade into the next track. Turn off for hard track changes.")
        self.crossfade_checkbox.setChecked(False)
        self.crossfade_checkbox.stateChanged.connect(self._on_crossfade_toggle)
        crossfade_row.addWidget(self.crossfade_checkbox)
        crossfade_row.addWidget(QLabel("TIME"))
        self.crossfade_slider = QSlider(Qt.Horizontal)
        self.crossfade_slider.setRange(2, 8)
        self.crossfade_slider.setValue(4)
        self.crossfade_value = QLabel("4s")
        self.crossfade_slider.valueChanged.connect(lambda v: self.crossfade_value.setText(f"{v}s"))
        crossfade_row.addWidget(self.crossfade_slider, 1)
        crossfade_row.addWidget(self.crossfade_value)
        col.addLayout(crossfade_row)

        orow = QHBoxLayout()
        orow.addWidget(QLabel("AUDIO OUT"))
        self.audio_output_combo = QComboBox()
        self.audio_output_combo.setMinimumWidth(150)
        for device in self.audio.audio_outputs():
            self.audio_output_combo.addItem(device.description())
        self.audio_output_combo.currentIndexChanged.connect(self.audio.set_audio_output)
        orow.addWidget(self.audio_output_combo, 1)
        col.addLayout(orow)

        self.loop_checkbox = QCheckBox("LOOP TRACK")
        self.loop_checkbox.stateChanged.connect(self._set_loop)
        col.addWidget(self.loop_checkbox)

        self.reactive_checkbox = QCheckBox("AUDIO REACTIVE")
        self.reactive_checkbox.setChecked(True)
        self.reactive_checkbox.stateChanged.connect(self._toggle_reactive)
        col.addWidget(self.reactive_checkbox)

        self.visualizer = SpectrumWidget()
        col.addWidget(self.visualizer)
        col.addStretch(1)
        return col

    def _build_playlist_sidebar(self):
        panel = QFrame()
        panel.setObjectName("playlistPanel")
        panel.setMinimumWidth(280)
        panel.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        head = QHBoxLayout()
        title = QLabel("[ QUEUE ]")
        title.setObjectName("sectionTitle")
        head.addWidget(title)
        self.track_count_label = QLabel("0 TRACKS • 00:00")
        self.track_count_label.setObjectName("dim")
        head.addStretch(1)
        head.addWidget(self.track_count_label)
        layout.addLayout(head)

        self.track_search = QLineEdit()
        self.track_search.setPlaceholderText("SEARCH / FILTER TRACKS…")
        self.track_search.textChanged.connect(self._filter_tracks)
        layout.addWidget(self.track_search)

        self.track_hint_label = QLabel("☐ CHECK TRACKS TO SELECT • DOUBLE-CLICK TO PLAY • DRAG TO REORDER")
        self.track_hint_label.setObjectName("dim")
        self.track_hint_label.setWordWrap(True)
        layout.addWidget(self.track_hint_label)

        select_row = QHBoxLayout()
        select_all = QPushButton("SELECT ALL")
        select_all.setToolTip("Select every track in the queue")
        select_all.clicked.connect(lambda: self.track_list.select_all())
        select_row.addWidget(select_all)
        clear_sel = QPushButton("CLEAR")
        clear_sel.setToolTip("Clear track selection")
        clear_sel.clicked.connect(lambda: self.track_list.clear_selection())
        select_row.addWidget(clear_sel)
        self.selected_count_label = QLabel("0 SELECTED")
        self.selected_count_label.setObjectName("selectionCount")
        select_row.addWidget(self.selected_count_label)
        select_row.addStretch(1)
        self.remove_selected_button = QPushButton("REMOVE SELECTED")
        self.remove_selected_button.setEnabled(False)
        self.remove_selected_button.setObjectName("removeSelected")
        self.remove_selected_button.clicked.connect(lambda: self.track_list.remove_selected())
        select_row.addWidget(self.remove_selected_button)
        layout.addLayout(select_row)

        self.track_list = TrackQueueWidget()
        self.track_list.track_activated.connect(self._select_audio_path)
        self.track_list.order_changed.connect(self._sync_audio_order)
        self.track_list.tracks_removed.connect(self._remove_audio_paths)
        self.track_list.drag_state_changed.connect(self._on_queue_drag_state)
        self.track_list.selection_changed.connect(self._on_queue_selection_changed)
        self.track_list.duration_changed.connect(self._refresh_queue_duration)
        self.track_list.marker_changed.connect(self._set_track_marker)
        self.track_list.queue_start_time_requested.connect(self._set_track_start_time)
        layout.addWidget(self.track_list, 1)

        return panel


    def _build_right_column(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        scroll.setWidget(content)

        layout = QVBoxLayout(content)
        layout.setSpacing(4)

        layout.addLayout(self._build_effects_panel())
        layout.addWidget(hairline())
        layout.addLayout(self._build_element_panel())
        layout.addWidget(hairline())
        layout.addLayout(self._build_background_panel())
        layout.addWidget(hairline())
        layout.addLayout(self._build_keybinds_panel())
        layout.addStretch(1)
        return scroll

    # -- Effects -------------------------------------------------------- #

    def _build_effects_panel(self):
        col = QVBoxLayout()
        col.addWidget(section_title("EFFECTS"))

        row = QHBoxLayout()
        row.addWidget(QLabel("COLOR:"))
        self.effect_color = ColorSwatchButton(QColor("#FFFFFF"))
        self.effect_color.setFixedWidth(120)
        row.addWidget(self.effect_color)
        row.addStretch(1)
        col.addLayout(row)

        grid = QGridLayout()
        self.flash_button = QPushButton("FLASH")
        self.flash_button.clicked.connect(self._on_flash)
        grid.addWidget(self.flash_button, 0, 0)

        self.strobe_button = QPushButton("STROBE")
        self.strobe_button.setCheckable(True)
        self.strobe_button.setObjectName("danger")
        self.strobe_button.clicked.connect(self._on_strobe)
        grid.addWidget(self.strobe_button, 0, 1)

        self.white_button = QPushButton("WHITE SCREEN")
        self.white_button.setCheckable(True)
        self.white_button.clicked.connect(self._on_white)
        grid.addWidget(self.white_button, 0, 2)

        self.smooth_strobe_button = QPushButton("SMOOTH STROBE")
        self.smooth_strobe_button.setCheckable(True)
        self.smooth_strobe_button.clicked.connect(self._on_smooth_strobe)
        grid.addWidget(self.smooth_strobe_button, 1, 0, 1, 3)
        col.addLayout(grid)

        slider_row = QHBoxLayout()
        slider_row.addWidget(QLabel("FLASH DURATION"))
        self.flash_duration_slider = QSlider(Qt.Horizontal)
        self.flash_duration_slider.setRange(50, 1000)
        self.flash_duration_slider.setValue(150)
        slider_row.addWidget(self.flash_duration_slider)
        col.addLayout(slider_row)

        slider_row2 = QHBoxLayout()
        slider_row2.addWidget(QLabel("STROBE SPEED"))
        self.strobe_speed_slider = QSlider(Qt.Horizontal)
        self.strobe_speed_slider.setRange(300, 2000)
        self.strobe_speed_slider.setValue(800)
        self.strobe_speed_slider.valueChanged.connect(self._on_strobe_speed_changed)
        slider_row2.addWidget(self.strobe_speed_slider)
        col.addLayout(slider_row2)

        slider_row3 = QHBoxLayout()
        slider_row3.addWidget(QLabel("SMOOTH STROBE SPEED"))
        self.smooth_strobe_speed_slider = QSlider(Qt.Horizontal)
        self.smooth_strobe_speed_slider.setRange(300, 3000)
        self.smooth_strobe_speed_slider.setValue(1200)
        self.smooth_strobe_speed_slider.valueChanged.connect(self._on_smooth_strobe_speed_changed)
        slider_row3.addWidget(self.smooth_strobe_speed_slider)
        col.addLayout(slider_row3)

        self.auto_flash_checkbox = QCheckBox("AUTO FLASH ON PEAKS")
        self.auto_flash_checkbox.setChecked(True)
        col.addWidget(self.auto_flash_checkbox)

        self.auto_strobe_checkbox = QCheckBox("AUTO STROBE FROM ENERGY")
        self.auto_strobe_checkbox.setChecked(True)
        col.addWidget(self.auto_strobe_checkbox)

        sens_row = QHBoxLayout()
        sens_row.addWidget(QLabel("FLASH SENSITIVITY"))
        self.sensitivity_slider = QSlider(Qt.Horizontal)
        self.sensitivity_slider.setRange(0, 100)
        self.sensitivity_slider.setValue(55)
        self.sensitivity_value = QLabel("55%")
        self.sensitivity_slider.valueChanged.connect(self._on_sensitivity_changed)
        sens_row.addWidget(self.sensitivity_slider)
        sens_row.addWidget(self.sensitivity_value)
        col.addLayout(sens_row)

        hint = QLabel("LOW = only strong beats   •   HIGH = more flashes")
        hint.setObjectName("dim")
        col.addWidget(hint)

        return col

    # -- Element ---------------------------------------------------------#

    def _build_element_panel(self):
        col = QVBoxLayout()
        col.addWidget(section_title("ELEMENT"))

        self.element_drop = DropZone(
            "DRAG & DROP AN IMAGE / GIF HERE", ELEMENT_EXTENSIONS
        )
        self.element_drop.files_dropped.connect(
            lambda paths: self._set_element(paths[0])
        )
        col.addWidget(self.element_drop)

        row = QHBoxLayout()
        browse = QPushButton("BROWSE")
        browse.clicked.connect(self._browse_element)
        row.addWidget(browse)

        self.element_toggle = QPushButton("SHOW ELEMENT")
        self.element_toggle.setCheckable(True)
        self.element_toggle.clicked.connect(self._on_element_toggle)
        row.addWidget(self.element_toggle)
        col.addLayout(row)

        size_row = QHBoxLayout()
        size_row.addWidget(QLabel("SIZE"))
        self.element_size_slider = QSlider(Qt.Horizontal)
        self.element_size_slider.setRange(5, 100)
        self.element_size_slider.setValue(50)
        self.element_size_slider.valueChanged.connect(self._on_element_size_changed)
        size_row.addWidget(self.element_size_slider)
        col.addLayout(size_row)

        return col

    # -- Background --------------------------------------------------- #

    def _build_background_panel(self):
        col = QVBoxLayout()
        col.addWidget(section_title("BACKGROUND"))

        self.background_drop = DropZone(
            "DRAG & DROP PNG / JPG / GIF / MP4 HERE", ALL_BACKGROUND_EXTENSIONS
        )
        self.background_drop.files_dropped.connect(self._add_backgrounds)
        col.addWidget(self.background_drop)

        row = QHBoxLayout()
        browse = QPushButton("ADD BACKGROUND")
        browse.clicked.connect(self._browse_backgrounds)
        row.addWidget(browse)

        remove = QPushButton("REMOVE SELECTED")
        remove.clicked.connect(self._remove_selected_background)
        row.addWidget(remove)
        col.addLayout(row)

        self.background_list = QListWidget()
        self.background_list.setFixedHeight(120)
        self.background_list.itemClicked.connect(self._on_background_selected)
        col.addWidget(self.background_list)

        self.mute_checkbox = QCheckBox("MUTE VIDEO BACKGROUNDS")
        self.mute_checkbox.setChecked(True)
        self.mute_checkbox.stateChanged.connect(self._on_mute_changed)
        col.addWidget(self.mute_checkbox)

        return col

    # -- Keybinds --------------------------------------------------------- #

    def _build_keybinds_panel(self):
        col = QVBoxLayout()
        col.addWidget(section_title("KEYBINDS"))

        self.keybind_actions = {
            "flash": ("FLASH", "F1"),
            "strobe": ("STROBE TOGGLE", "F2"),
            "white": ("WHITE SCREEN TOGGLE", "F3"),
            "element": ("ELEMENT TOGGLE", "F4"),
        }
        self.keybind_edits = {}

        grid = QGridLayout()
        for row_i, (key, (label, default)) in enumerate(self.keybind_actions.items()):
            grid.addWidget(QLabel(label), row_i, 0)
            edit = QKeySequenceEdit(QKeySequence(default))
            edit.editingFinished.connect(
                lambda k=key, e=edit: self._rebind(k, e.keySequence())
            )
            grid.addWidget(edit, row_i, 1)
            self.keybind_edits[key] = edit
        col.addLayout(grid)
        return col

    # ------------------------------------------------------------------ #
    # Shortcuts
    # ------------------------------------------------------------------ #

    def _build_shortcuts(self):
        self.shortcuts = {}
        handlers = {
            "flash": self._on_flash,
            "strobe": lambda: self.strobe_button.click(),
            "white": lambda: self.white_button.click(),
            "element": lambda: self.element_toggle.click(),
        }
        for key, (label, default) in self.keybind_actions.items():
            shortcut = QShortcut(QKeySequence(default), self)
            shortcut.activated.connect(handlers[key])
            self.shortcuts[key] = shortcut

        # Transport shortcuts are deliberately simple for live use.
        self.player_shortcuts = []
        for sequence, handler in (
            ("Space", self._toggle_play),
            ("Ctrl+Right", self._next_track),
            ("Ctrl+Left", self._prev_track),
            ("Ctrl+F", lambda: (self.track_search.setFocus(), self.track_search.selectAll())),
            ("Escape", lambda: (self.track_search.clear(), self.track_list.setFocus())),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(handler)
            self.player_shortcuts.append(shortcut)

    def _rebind(self, key, sequence):
        if sequence.isEmpty():
            return
        self.shortcuts[key].setKey(sequence)

    # ------------------------------------------------------------------ #
    # Screen / display management
    # ------------------------------------------------------------------ #

    def _populate_screens(self):
        from PySide6.QtGui import QGuiApplication
        self.screen_combo.clear()
        for i, screen in enumerate(QGuiApplication.screens()):
            geo = screen.geometry()
            self.screen_combo.addItem(
                f"SCREEN {i + 1} — {geo.width()}x{geo.height()}", screen
            )

    def _toggle_display(self):
        if self.display_window is not None:
            self.display_window.close()
            return
        from PySide6.QtGui import QGuiApplication
        screen = self.screen_combo.currentData()
        if screen is None:
            screens = QGuiApplication.screens()
            screen = screens[0] if screens else None
        if screen is None:
            return

        self.display_window = DisplayWindow()
        self.display_window.closed.connect(self._on_display_closed)
        self.display_window.setGeometry(screen.geometry())

        # Rebuild any already-loaded backgrounds / element onto the new window
        for key, item in self.background_items.items():
            self.display_window.add_background(key, item["path"])
        if self.background_list.currentItem() is not None:
            key = self.background_list.currentItem().data(Qt.UserRole)
            self.display_window.set_background(key)
        if self.element_path:
            self.display_window.set_element_image(self.element_path)
            self.display_window.set_element_scale(
                self.element_size_slider.value() / 100.0
            )
        self.display_window.set_background_muted(self.mute_checkbox.isChecked())

        self.display_window.set_audio_reactive(self._audio_reactive, self.effect_color.color())
        self.display_window.showFullScreen()
        self.start_button.setText("CLOSE DISPLAY")
        self.status_label.setText("DISPLAY: LIVE")
        self.preview_timer.start()

    def _on_display_closed(self):
        self.display_window = None
        self.start_button.setText("OPEN DISPLAY")
        self.status_label.setText("DISPLAY: OFFLINE")
        self.preview_timer.stop()
        self.preview_label.setText("NO SIGNAL")
        self.preview_label.setPixmap(QPixmap())

    def _refresh_preview(self):
        window = self.display_window
        if window is None:
            return
        try:
            if not window.isVisible():
                return
            pixmap = window.grab()
            if pixmap.isNull():
                return
            target_size = self.preview_label.contentsRect().size()
            if target_size.width() <= 0 or target_size.height() <= 0:
                return
            scaled = pixmap.scaled(
                target_size, Qt.KeepAspectRatio, Qt.FastTransformation
            )
            self.preview_label.setPixmap(scaled)
        except RuntimeError:
            # Qt can invalidate a native window during display changes/close.
            self.preview_timer.stop()
            self.display_window = None

    # ------------------------------------------------------------------ #
    # Effects handlers
    # ------------------------------------------------------------------ #

    def _on_flash(self):
        if self.display_window is None:
            return
        self.display_window.trigger_flash(
            self.effect_color.color(), self.flash_duration_slider.value()
        )

    def _on_strobe(self):
        if self.display_window is None:
            self.strobe_button.setChecked(False)
            return
        active = self.display_window.toggle_strobe(
            self.effect_color.color(), self.strobe_speed_slider.value()
        )
        self.strobe_button.setChecked(active)
        if active:
            self.white_button.setChecked(False)
            self.smooth_strobe_button.setChecked(False)

    def _on_strobe_speed_changed(self, value):
        if self.display_window is not None and self.strobe_button.isChecked():
            # Change legacy STROBE interval without toggling its state.
            self.display_window._strobe_timer.setInterval(max(10, int(value)))

    def _on_smooth_strobe(self):
        if self.display_window is None:
            self.smooth_strobe_button.setChecked(False)
            return
        active = self.display_window.toggle_smooth_strobe(
            self.effect_color.color(), self.smooth_strobe_speed_slider.value()
        )
        self.smooth_strobe_button.setChecked(active)
        if active:
            self.strobe_button.setChecked(False)
            self.white_button.setChecked(False)

    def _on_smooth_strobe_speed_changed(self, value):
        if self.display_window is not None and self.smooth_strobe_button.isChecked():
            self.display_window.set_smooth_strobe_period(value)

    def _on_white(self):
        if self.display_window is None:
            self.white_button.setChecked(False)
            return
        active = self.display_window.toggle_white(self.effect_color.color())
        self.white_button.setChecked(active)
        if active:
            self.strobe_button.setChecked(False)
            self.smooth_strobe_button.setChecked(False)

    # ------------------------------------------------------------------ #
    # Player / audio reactive
    # ------------------------------------------------------------------ #

    def _browse_audio(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "ADD MUSIC", "",
            "Audio (*.mp3 *.wav *.flac *.ogg *.m4a *.aac *.opus *.wma)"
        )
        if paths:
            self._add_audio_tracks(paths)

    def _browse_audio_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "LOAD AUDIO FOLDER")
        if folder:
            self._scan_audio_folders([folder])

    def _scan_audio_folders(self, paths):
        folders = [p for p in paths if os.path.isdir(p)]
        files = [p for p in paths if os.path.isfile(p)]
        if files:
            self._add_audio_tracks(files)
        if not folders or (self._scanner_thread is not None and self._scanner_thread.isRunning()):
            return
        self._loading_audio = True
        self.track_hint_label.setText("SCANNING FOLDER…")
        self._scanner_thread = QThread(self)
        self._scanner = FolderScanner(folders)
        self._scanner.moveToThread(self._scanner_thread)
        self._scanner_thread.started.connect(self._scanner.run)
        self._scanner.batch.connect(self._add_audio_tracks)
        self._scanner.error.connect(lambda msg: self.status_label.setText("AUDIO SCAN ERROR: " + msg))
        self._scanner.finished.connect(self._audio_scan_finished)
        self._scanner.finished.connect(self._scanner.deleteLater)
        self._scanner.finished.connect(self._scanner_thread.quit)
        self._scanner_thread.finished.connect(self._scanner_thread.deleteLater)
        self._scanner_thread.start()

    def _audio_scan_finished(self):
        self._loading_audio = False
        self.track_hint_label.setText("☐ CHECK TRACKS TO SELECT • DOUBLE-CLICK TO PLAY • DRAG TO REORDER")
        self._scanner = None
        self._scanner_thread = None

    def _select_audio_path(self, path):
        if not path:
            return
        try:
            self._audio_index = self._audio_tracks.index(path)
        except ValueError:
            return
        self._load_audio_track()
        self.audio.play()

    def _on_queue_drag_state(self, dragging):
        self.track_hint_label.setText(
            "RELEASE TO DROP • ORDER UPDATES LIVE" if dragging
            else "DRAG A TILE • MOVE UP/DOWN • DOUBLE-CLICK TO PLAY"
        )

    def _on_queue_selection_changed(self, count):
        if hasattr(self, "remove_selected_button"):
            self.remove_selected_button.setEnabled(count > 0)
            self.remove_selected_button.setText(
                f"REMOVE {count} SELECTED" if count else "REMOVE SELECTED"
            )
        if hasattr(self, "selected_count_label"):
            self.selected_count_label.setText(f"{count} SELECTED")

    def _set_loop(self, state):
        self.audio.set_loop(bool(state))

    def _on_sensitivity_changed(self, value):
        self._reactive_sensitivity = int(value)
        self.sensitivity_value.setText(f"{value}%")
        self.audio.set_reactive_sensitivity(value)

    def _on_audio_beat(self, strength):
        if self.display_window is not None and self._audio_reactive and self.auto_flash_checkbox.isChecked():
            self.display_window.audio_reactive_beat(strength)

    def _add_audio_tracks(self, paths, save=True):
        normalized = []
        existing = {os.path.normcase(os.path.abspath(p)) for p in self._audio_tracks}
        for path in paths:
            if os.path.isdir(path):
                self._scan_audio_folders([path])
                continue
            absolute = os.path.abspath(path)
            key = os.path.normcase(absolute)
            if os.path.splitext(absolute)[1].lower() not in AUDIO_EXTENSIONS or key in existing:
                continue
            if not os.path.isfile(absolute):
                continue
            existing.add(key)
            normalized.append(absolute)

        if not normalized:
            self._update_track_count()
            return

        was_empty = not self._audio_tracks
        self._audio_tracks.extend(normalized)
        self.track_list.setUpdatesEnabled(False)
        try:
            for path in normalized:
                tile = self.track_list.add_track(path)
                tile.set_marker(self._track_markers.get(path, ""))
        finally:
            self.track_list.setUpdatesEnabled(True)

        self._renumber_tracks()
        self._update_track_count()
        if save:
            self._save_queue_state()
        if was_empty and self._audio_tracks:
            self._audio_index = 0
            self.track_list.set_current_path(self._audio_tracks[0])
            self._load_audio_track()

    def _sync_audio_order(self):
        current_path = (
            self._audio_tracks[self._audio_index]
            if 0 <= self._audio_index < len(self._audio_tracks) else None
        )
        self._audio_tracks = self.track_list.paths()
        valid = set(self._audio_tracks)
        self._track_markers = {p: c for p, c in self._track_markers.items() if p in valid}
        self._track_start_ms = {p: m for p, m in self._track_start_ms.items() if p in valid}
        if current_path in self._audio_tracks:
            self._audio_index = self._audio_tracks.index(current_path)
        elif self._audio_tracks:
            self._audio_index = max(0, min(self._audio_index, len(self._audio_tracks) - 1))
        else:
            self._audio_index = -1
        self._renumber_tracks()
        self._update_track_count()
        self._save_queue_state()

    def _queue_state_path(self):
        root = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
        root = root or os.path.join(os.path.expanduser("~"), ".arxiiv_studio")
        os.makedirs(root, exist_ok=True)
        return os.path.join(root, "playlist_queue.json")

    def _save_queue_state(self):
        try:
            payload = {
                "version": 1,
                "tracks": self.track_list.paths(),
                "current_index": self._audio_index,
                "track_markers": self._track_markers,
                "track_start_ms": self._track_start_ms,
            }
            # Write to a sibling temporary file and replace atomically. This
            # prevents a crash/power loss from truncating the only queue file.
            queue_dir = os.path.dirname(self._queue_path)
            os.makedirs(queue_dir, exist_ok=True)
            fd, temp_path = tempfile.mkstemp(prefix=".playlist_queue_", suffix=".tmp", dir=queue_dir)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, ensure_ascii=False, indent=2)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(temp_path, self._queue_path)
            finally:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except OSError:
                    pass
        except Exception as exc:
            try:
                if hasattr(self, "status_label"):
                    self.status_label.setText(f"QUEUE SAVE ERROR: {exc}")
            except Exception:
                pass

    def _load_queue_state(self):
        try:
            with open(self._queue_path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            if not isinstance(payload, dict):
                raise ValueError("queue root must be a JSON object")
            self._track_markers = {os.path.abspath(k): str(v) for k, v in payload.get("track_markers", {}).items() if v}
            self._track_start_ms = {os.path.abspath(k): max(0, int(v)) for k, v in payload.get("track_start_ms", {}).items()}
            tracks = [
                os.path.abspath(p) for p in payload.get("tracks", [])
                if os.path.isfile(p) and os.path.splitext(p)[1].lower() in AUDIO_EXTENSIONS
            ]
            self._add_audio_tracks(tracks, save=False)
            if tracks != payload.get("tracks", []):
                self._save_queue_state()
            if tracks:
                for tile in self.track_list._tiles:
                    tile.set_marker(self._track_markers.get(tile.path, ""))
                wanted = int(payload.get("current_index", 0))
                self._audio_index = max(0, min(wanted, len(tracks) - 1))
                self._load_audio_track()
        except (OSError, ValueError, TypeError, AttributeError, json.JSONDecodeError) as exc:
            # Preserve a malformed queue for diagnosis instead of silently
            # pretending the saved playlist never existed.
            try:
                if os.path.isfile(self._queue_path):
                    damaged = self._queue_path + f".corrupt-{int(time.time())}"
                    os.replace(self._queue_path, damaged)
                if hasattr(self, "status_label"):
                    self.status_label.setText(f"QUEUE RECOVERY: saved queue was invalid ({exc})")
            except OSError:
                pass
        finally:
            self._queue_loaded = True

    def _remove_audio_path(self, path):
        self._remove_audio_paths([path])

    def _remove_audio_paths(self, paths):
        removed = {os.path.abspath(p) for p in paths}
        if not removed:
            return
        current_path = (
            self._audio_tracks[self._audio_index]
            if 0 <= self._audio_index < len(self._audio_tracks) else None
        )
        current_old_index = self._audio_index
        self._audio_tracks = [p for p in self._audio_tracks if p not in removed]
        self._track_markers = {p: c for p, c in self._track_markers.items() if p not in removed}
        self._track_start_ms = {p: m for p, m in self._track_start_ms.items() if p not in removed}
        if not self._audio_tracks:
            self._audio_index = -1
            self.audio.stop()
            self.track_label.setText("NO TRACK SELECTED")
        elif current_path in self._audio_tracks:
            self._audio_index = self._audio_tracks.index(current_path)
        else:
            self._audio_index = max(0, min(current_old_index, len(self._audio_tracks) - 1))
            self._load_audio_track()
        self._update_track_count()
        self._save_queue_state()

    def _renumber_tracks(self):
        self.track_list._renumber()

    def _refresh_queue_duration(self):
        self._update_track_count()

    def _update_track_count(self, visible=None):
        total = len(self._audio_tracks)
        if visible is None:
            visible = self.track_list.visible_count()
        total_duration = sum(getattr(tile, "_duration_ms", 0) for tile in self.track_list._tiles)
        duration_text = self._fmt_ms(total_duration)
        self.track_count_label.setText(
            f"{total} TRACKS • {duration_text}" if total == visible else f"{visible}/{total} TRACKS • {duration_text}"
        )

    def _filter_tracks(self, query):
        self.track_list.setUpdatesEnabled(False)
        try:
            visible = self.track_list.filter(query)
        finally:
            self.track_list.setUpdatesEnabled(True)
        self._update_track_count(visible=visible)

    def _set_track_marker(self, path, color):
        path = os.path.abspath(path)
        if color:
            self._track_markers[path] = color
        else:
            self._track_markers.pop(path, None)
        for tile in self.track_list._tiles:
            if tile.path == path:
                tile.set_marker(color)
                break
        self._save_queue_state()

    def _set_track_start_time(self, path, reset=False):
        path = os.path.abspath(path)
        if reset:
            self._track_start_ms.pop(path, None)
            self._save_queue_state()
            return
        current = self._track_start_ms.get(path, 0)
        initial = self._fmt_ms(current)
        value, ok = QInputDialog.getText(
            self, "SET TRACK START",
            "Start position (MM:SS or HH:MM:SS):",
            QLineEdit.Normal, initial
        )
        if not ok:
            return
        parsed = self._parse_time(value)
        if parsed is None:
            self.status_label.setText("INVALID START TIME • USE MM:SS")
            return
        self._track_start_ms[path] = parsed
        self._save_queue_state()
        if 0 <= self._audio_index < len(self._audio_tracks) and self._audio_tracks[self._audio_index] == path:
            self.audio.set_position(parsed)

    @staticmethod
    def _parse_time(value):
        try:
            parts = [int(x) for x in str(value).strip().split(":")]
            if len(parts) == 2:
                m, s = parts
                if not (0 <= s < 60 and m >= 0): return None
                return (m * 60 + s) * 1000
            if len(parts) == 3:
                h, m, s = parts
                if not (h >= 0 and 0 <= m < 60 and 0 <= s < 60): return None
                return (h * 3600 + m * 60 + s) * 1000
        except (TypeError, ValueError):
            return None
        return None

    def _load_audio_track(self, preserve_position=False):
        if not (0 <= self._audio_index < len(self._audio_tracks)):
            return
        path = self._audio_tracks[self._audio_index]
        old_position = self.audio.position() if preserve_position else 0
        self._crossfade_triggered = False
        self.audio.cancel_crossfade()
        self.audio.set_source(path, start_ms=self._track_start_ms.get(path, 0))
        if old_position > 0:
            QTimer.singleShot(120, lambda p=old_position: self.audio.set_position(p))
        self.track_label.setText(
            f"{self._audio_index + 1:02d}  /  {len(self._audio_tracks):02d}    {os.path.basename(path)}"
        )
        self.track_list.set_current_path(path)

    def _toggle_play(self):
        if not self._audio_tracks:
            return
        self.audio.toggle()

    def _transition_to_index(self, target_index):
        """Move to another queue item, using the same crossfade as auto-next.

        Manual NEXT/PREV uses crossfade only when it is enabled and a track is
        currently playing. Paused/stopped playback remains a normal selection
        change, so pressing NEXT/PREV does not unexpectedly start music.
        """
        if not self._audio_tracks:
            return

        target_index %= len(self._audio_tracks)
        if target_index == self._audio_index and self.audio.is_playing():
            return

        target_path = self._audio_tracks[target_index]
        crossfade_enabled = bool(
            getattr(self, "crossfade_checkbox", None)
            and self.crossfade_checkbox.isChecked()
        )

        # A manual command takes priority over an automatic transition that
        # may already be preparing the next deck.
        self._crossfade_triggered = False
        self.audio.cancel_crossfade()

        if crossfade_enabled and self.audio.is_playing() and len(self._audio_tracks) > 1:
            fade_ms = int(self.crossfade_slider.value()) * 1000
            self._crossfade_triggered = self.audio.start_crossfade(target_path, fade_ms, self._track_start_ms.get(target_path, 0))
            if self._crossfade_triggered:
                # Keep the logical index on the currently playing track until
                # the fade completes; _on_crossfade_finished commits the target.
                return

        # Crossfade disabled, unavailable, or not playing: normal selection.
        self._audio_index = target_index
        self._load_audio_track()
        self.audio.play()

    def _next_track(self):
        if not self._audio_tracks:
            return
        self._transition_to_index(self._audio_index + 1)

    def _prev_track(self):
        if not self._audio_tracks:
            return
        self._transition_to_index(self._audio_index - 1)

    def _on_crossfade_toggle(self, state):
        """Enable/disable lightweight dual-deck crossfade."""
        enabled = bool(state)
        self.crossfade_slider.setEnabled(enabled)
        if not enabled:
            self._crossfade_triggered = False
            self.audio.cancel_crossfade()
        self.track_hint_label.setText(
            "CROSSFADE ON • automatic transition enabled" if enabled
            else "CROSSFADE OFF • hard track changes"
        )

    def _maybe_crossfade(self, position, duration):
        """Start a lightweight crossfade near the end of the current track."""
        if not getattr(self, "crossfade_checkbox", None) or not self.crossfade_checkbox.isChecked():
            return
        if self._crossfade_triggered or len(self._audio_tracks) < 2:
            return
        if not (0 <= self._audio_index < len(self._audio_tracks)) or duration <= 0:
            return

        fade_ms = int(self.crossfade_slider.value()) * 1000
        # Give the transition enough time to start, but avoid trying to crossfade
        # on very short tracks where the entire file is shorter than the fade.
        if duration <= fade_ms + 500:
            return
        remaining = int(duration) - int(position)
        if remaining > fade_ms:
            return

        next_index = (self._audio_index + 1) % len(self._audio_tracks)
        if next_index == self._audio_index:
            return
        next_path = self._audio_tracks[next_index]
        self._crossfade_triggered = self.audio.start_crossfade(next_path, fade_ms, self._track_start_ms.get(next_path, 0))

    def _on_crossfade_finished(self, path):
        """Keep the logical playlist index synchronized with the active deck."""
        try:
            self._audio_index = self._audio_tracks.index(path)
        except ValueError:
            return
        self._crossfade_triggered = False
        self.track_list.set_current_path(path)
        self._save_queue_state()

    def _on_audio_track(self, path):
        self.track_label.setText(os.path.basename(path))
        self.position_slider.setValue(0)
        self.position_slider.setRange(0, self.audio.duration())

    def _on_audio_position(self, position, duration):
        if not self.position_slider.isSliderDown():
            self.position_slider.setRange(0, max(0, duration))
            self.position_slider.setValue(max(0, position))
        self.time_label.setText(f"{self._fmt_ms(position)}  /  {self._fmt_ms(duration)}")
        self._maybe_crossfade(position, duration)

    @staticmethod
    def _fmt_ms(ms):
        total = max(0, int(ms)) // 1000
        return f"{total // 60:02d}:{total % 60:02d}"

    def _on_audio_state(self, state):
        self.play_button.setText("Ⅱ  PAUSE" if state == "PLAYING" else "▶  PLAY")
        if state == "ENDED" and self._audio_tracks:
            if self.loop_checkbox.isChecked():
                return
            self._audio_index = (self._audio_index + 1) % len(self._audio_tracks)
            self._load_audio_track()
            self.audio.play()

    def _toggle_reactive(self, state):
        self._audio_reactive = bool(state)
        if self.display_window is not None:
            self.display_window.set_audio_reactive(
                self._audio_reactive, self.effect_color.color()
            )

    def _on_audio_analysis(self, energy, bass, peak, bars):
        self._last_analysis = (energy, bass, peak)
        self.visualizer.set_analysis(energy, bass, peak, bars)
        if self.display_window is None or not self._audio_reactive:
            return
        self.display_window.set_audio_reactive(True, self.effect_color.color())
        self.display_window.audio_reactive_update(energy, bass, peak)

    # ------------------------------------------------------------------ #
    # Element handlers
    # ------------------------------------------------------------------ #

    def _browse_element(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "SELECT ELEMENT IMAGE", "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp *.gif)"
        )
        if path:
            self._set_element(path)

    def _set_element(self, path):
        self.element_path = path
        self.element_drop.label.setText(os.path.basename(path))
        if self.display_window is not None:
            self.display_window.set_element_image(path)
            self.display_window.set_element_scale(
                self.element_size_slider.value() / 100.0
            )

    def _on_element_toggle(self):
        if self.display_window is None:
            self.element_toggle.setChecked(False)
            return
        visible = self.display_window.toggle_element()
        self.element_toggle.setChecked(visible)

    def _on_element_size_changed(self, value):
        if self.display_window is not None:
            self.display_window.set_element_scale(value / 100.0)

    # ------------------------------------------------------------------ #
    # Background handlers
    # ------------------------------------------------------------------ #

    def _browse_backgrounds(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "SELECT BACKGROUNDS", "",
            "Media (*.png *.jpg *.jpeg *.bmp *.webp *.gif *.mp4 *.mov *.m4v *.avi)"
        )
        if paths:
            self._add_backgrounds(paths)

    def _add_backgrounds(self, paths):
        for path in paths:
            key = str(uuid.uuid4())
            name = os.path.basename(path)
            self.background_items[key] = {"path": path, "name": name}

            item = QListWidgetItem(name)
            item.setData(Qt.UserRole, key)
            self.background_list.addItem(item)

            if self.display_window is not None:
                self.display_window.add_background(key, path)

    def _remove_selected_background(self):
        item = self.background_list.currentItem()
        if item is None:
            return
        key = item.data(Qt.UserRole)
        self.background_items.pop(key, None)
        self.background_list.takeItem(self.background_list.row(item))
        if self.display_window is not None:
            self.display_window.remove_background(key)

    def _on_background_selected(self, item):
        key = item.data(Qt.UserRole)
        if self.display_window is not None:
            self.display_window.set_background(key)

    def _on_mute_changed(self, state):
        if self.display_window is not None:
            self.display_window.set_background_muted(bool(state))

    # ------------------------------------------------------------------ #

    def closeEvent(self, event):
        # Never let Qt destroy a still-running QThread: that can terminate the
        # whole process. If a large/network folder scan is still unwinding,
        # defer closing until its thread has actually finished.
        thread = self._scanner_thread
        if thread is not None and thread.isRunning():
            if self._scanner is not None:
                self._scanner.stop()
            thread.quit()
            if not self._close_after_scan:
                self._close_after_scan = True
                thread.finished.connect(self.close)
            event.ignore()
            self.status_label.setText("WAITING FOR FOLDER SCAN TO STOP…")
            return

        try:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
        except RuntimeError:
            pass
        if self.display_window is not None:
            try:
                self.display_window.close()
            except RuntimeError:
                pass
        try:
            self._save_queue_state()
            self.audio.stop()
        except Exception as exc:
            import logging
            logging.getLogger("arxiiv_studio").exception("Error during shutdown: %s", exc)
        super().closeEvent(event)
