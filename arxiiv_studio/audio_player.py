import os
import time
import numpy as np

from PySide6.QtCore import QObject, QUrl, Signal, QTimer
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput, QAudioBufferOutput, QMediaDevices


class AudioReactivePlayer(QObject):
    """Lightweight dual-deck player with optional real-time crossfade.

    Two QMediaPlayers act as alternating decks. Crossfade only changes output
    volumes; it does not decode/render the whole files in Python, so there is
    no offline DSP step or large temporary WAV involved.
    """
    track_changed = Signal(str)
    position_changed = Signal(int, int)
    state_changed = Signal(str)
    analysis = Signal(float, float, float, object)
    beat_signal = Signal(float)
    error = Signal(str)
    crossfade_finished = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._players = [QMediaPlayer(self), QMediaPlayer(self)]
        self._outputs = [QAudioOutput(self), QAudioOutput(self)]
        self._buffers = [QAudioBufferOutput(self), QAudioBufferOutput(self)]
        self._active = 0
        self._base_volume = 0.85
        self._audio_devices = list(QMediaDevices.audioOutputs())
        self._crossfade = None
        self._crossfade_retry_count = 0
        self._crossfade_max_retries = 30
        self._crossfade_timer = QTimer(self)
        self._crossfade_timer.setInterval(30)
        self._crossfade_timer.timeout.connect(self._tick_crossfade)

        for i, player in enumerate(self._players):
            player.setAudioOutput(self._outputs[i])
            player.setAudioBufferOutput(self._buffers[i])
            self._outputs[i].setVolume(self._base_volume if i == self._active else 0.0)
            player.positionChanged.connect(self._on_position)
            player.playbackStateChanged.connect(self._on_state)
            player.mediaStatusChanged.connect(self._on_media_status)
            player.errorOccurred.connect(
                lambda error, msg, idx=i: self._on_player_error(idx, error, msg)
            )
            self._buffers[i].audioBufferReceived.connect(self._on_audio_buffer)

        # Public aliases retained for compatibility with the rest of the app.
        self.player = self._players[self._active]
        self.output = self._outputs[self._active]

        self._pending_start_ms = None
        self._pending_crossfade = None
        self._energy_smooth = 0.0
        self._bass_smooth = 0.0
        self._prev_bass = 0.0
        self._prev_energy = 0.0
        self._beat_cooldown = 0
        self._last_analysis_emit = 0.0
        self._analysis_emit_interval = 1.0 / 20.0
        self._reactive_sensitivity = 55

    def _inactive(self):
        return 1 - self._active

    def set_source(self, path, start_ms=0):
        self.cancel_crossfade()
        old = self._active
        other = 1 - old
        self._players[other].stop()
        self._outputs[other].setVolume(0.0)
        self._players[old].stop()
        self._active = old
        self.player = self._players[self._active]
        self.output = self._outputs[self._active]
        self._pending_start_ms = int(start_ms) if start_ms > 0 else None
        self.player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        self._outputs[self._active].setVolume(self._base_volume)
        self.track_changed.emit(path)
        self._reset_analysis()

    def start_crossfade(self, next_path, duration_ms, start_ms=0):
        if self._crossfade is not None:
            return False
        if not next_path or not os.path.isfile(next_path):
            return False
        duration_ms = max(1000, int(duration_ms))
        old = self._active
        new = 1 - old
        self._players[new].stop()
        self._outputs[new].setVolume(0.0)
        self._crossfade = {
            "old": old,
            "new": new,
            "path": os.path.abspath(next_path),
            "duration": duration_ms,
            "start_ms": max(0, int(start_ms)),
            "started": None,
            "loaded": False,
        }
        self._pending_crossfade = self._crossfade
        self._crossfade_retry_count = 0
        self._players[new].setSource(QUrl.fromLocalFile(os.path.abspath(next_path)))
        # Most local files become LoadedMedia quickly; the media-status handler
        # starts the deck. A short timer is a safe fallback for backends that
        # report BufferedMedia without LoadedMedia.
        QTimer.singleShot(80, self._try_start_crossfade)
        return True

    def _try_start_crossfade(self):
        cf = self._crossfade
        if cf is None or cf["loaded"]:
            return
        player = self._players[cf["new"]]
        status = player.mediaStatus()
        if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
            cf["loaded"] = True
            media_duration = int(player.duration())
            if media_duration > 0:
                remaining = max(250, media_duration - cf["start_ms"])
                cf["duration"] = min(cf["duration"], remaining)
            cf["started"] = time.monotonic()
            player.setPosition(cf["start_ms"])
            player.play()
            self._crossfade_timer.start()
        else:
            self._crossfade_retry_count += 1
            if self._crossfade_retry_count >= self._crossfade_max_retries:
                failed_path = cf.get("path", "")
                self.cancel_crossfade()
                self.error.emit(
                    "Crossfade cancelled: the next track did not load in time"
                    + (f" ({os.path.basename(failed_path)})" if failed_path else "")
                )
                return
            QTimer.singleShot(100, self._try_start_crossfade)

    def _on_player_error(self, index, error, message):
        detail = (message or "").strip() or str(error)
        cf = self._crossfade
        if cf is not None and index == cf.get("new"):
            # A failed next deck must not leave the current deck muted or a
            # retry timer spinning forever.
            self.cancel_crossfade()
            self.error.emit(f"Crossfade cancelled: {detail}")
            return
        self.error.emit(detail)

    def _tick_crossfade(self):
        cf = self._crossfade
        if cf is None or not cf["loaded"] or cf["started"] is None:
            return
        elapsed = (time.monotonic() - cf["started"]) * 1000.0
        t = max(0.0, min(1.0, elapsed / cf["duration"]))
        old_vol = self._base_volume * (1.0 - t)
        new_vol = self._base_volume * t
        self._outputs[cf["old"]].setVolume(old_vol)
        self._outputs[cf["new"]].setVolume(new_vol)
        if t >= 1.0:
            self._crossfade_timer.stop()
            self._players[cf["old"]].stop()
            self._outputs[cf["old"]].setVolume(0.0)
            self._active = cf["new"]
            self.player = self._players[self._active]
            self.output = self._outputs[self._active]
            self._outputs[self._active].setVolume(self._base_volume)
            path = cf["path"]
            self._crossfade = None
            self._pending_crossfade = None
            self._reset_analysis()
            self.track_changed.emit(path)
            self.crossfade_finished.emit(path)

    def cancel_crossfade(self):
        if self._crossfade is not None:
            old = self._crossfade["old"]
            new = self._crossfade["new"]
            self._crossfade_timer.stop()
            self._players[new].stop()
            self._outputs[new].setVolume(0.0)
            self._outputs[old].setVolume(self._base_volume)
        self._crossfade = None
        self._pending_crossfade = None
        self._crossfade_retry_count = 0

    # Compatibility no-ops for older callers/projects.
    def prepare_transition(self, wav_path):
        return False

    def start_prepared_transition(self):
        return False

    def stop_prepared_transition(self):
        self.cancel_crossfade()

    def set_loop(self, enabled):
        loops = QMediaPlayer.Loops.Infinite if enabled else QMediaPlayer.Loops.Once
        for player in self._players:
            player.setLoops(loops)

    def _on_media_status(self, status):
        sender = self.sender()
        try:
            idx = self._players.index(sender)
        except ValueError:
            return
        if idx == self._active and status == QMediaPlayer.MediaStatus.LoadedMedia and self._pending_start_ms is not None:
            ms = self._pending_start_ms
            self._pending_start_ms = None
            self.player.setPosition(ms)
            self.player.play()
        if self._crossfade is not None and idx == self._crossfade["new"]:
            if status in (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia):
                self._try_start_crossfade()
        if idx == self._active and status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._crossfade is None:
                self.state_changed.emit("ENDED")

    def play(self):
        self.player.play()

    def pause(self):
        self.cancel_crossfade()
        self.player.pause()

    def stop(self):
        self.cancel_crossfade()
        for player in self._players:
            player.stop()
        self._reset_analysis()

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.pause()
        else:
            self.play()

    def set_position(self, ms):
        self.cancel_crossfade()
        self.player.setPosition(int(ms))

    def set_volume(self, value):
        self._base_volume = max(0.0, min(1.0, value / 100.0))
        if self._crossfade is None:
            self._outputs[self._active].setVolume(self._base_volume)
        else:
            # Preserve the current fade ratio when the master volume changes.
            cf = self._crossfade
            elapsed = ((time.monotonic() - cf["started"]) * 1000.0) if cf["started"] else 0.0
            t = max(0.0, min(1.0, elapsed / cf["duration"]))
            self._outputs[cf["old"]].setVolume(self._base_volume * (1.0 - t))
            self._outputs[cf["new"]].setVolume(self._base_volume * t)

    def audio_outputs(self):
        return list(self._audio_devices)

    def set_audio_output(self, index):
        if 0 <= index < len(self._audio_devices):
            device = self._audio_devices[index]
            for output in self._outputs:
                output.setDevice(device)

    def set_reactive_sensitivity(self, value):
        self._reactive_sensitivity = max(0, min(100, int(value)))

    def duration(self):
        return int(self.player.duration())

    def position(self):
        return int(self.player.position())

    def is_playing(self):
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def _on_position(self, position):
        sender = self.sender()
        if sender is not self.player:
            return
        self.position_changed.emit(int(position), self.duration())

    def _on_state(self, state):
        sender = self.sender()
        if sender is not self.player:
            return
        names = {
            QMediaPlayer.PlaybackState.PlayingState: "PLAYING",
            QMediaPlayer.PlaybackState.PausedState: "PAUSED",
            QMediaPlayer.PlaybackState.StoppedState: "STOPPED",
        }
        self.state_changed.emit(names.get(state, "STOPPED"))

    def _reset_analysis(self):
        self._energy_smooth = 0.0
        self._bass_smooth = 0.0
        self._prev_bass = 0.0
        self._prev_energy = 0.0
        self._beat_cooldown = 0
        self._last_analysis_emit = 0.0
        self.analysis.emit(0.0, 0.0, 0.0, np.zeros(24, dtype=np.float32))

    def _on_audio_buffer(self, buffer):
        # During a crossfade the secondary deck is intentionally excluded from
        # the visualizer until it becomes the active deck. This avoids doubling
        # FFT work and prevents the visualizer from reacting to both tracks.
        sender = self.sender()
        active_buffer = self._buffers[self._active]
        if sender is not active_buffer or not buffer.isValid() or buffer.frameCount() <= 0:
            return
        try:
            fmt = buffer.format()
            raw = bytes(buffer.constData())
            channels = max(1, int(fmt.channelCount()))
            sample_rate = max(1, int(fmt.sampleRate()))
            sf = str(fmt.sampleFormat())
            if "UInt8" in sf:
                x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
            elif "Int16" in sf:
                x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            elif "Int32" in sf:
                x = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
            elif "Float" in sf:
                x = np.frombuffer(raw, dtype=np.float32)
            else:
                return
            usable = (x.size // channels) * channels
            if usable < channels * 16:
                return
            mono = x[:usable].reshape(-1, channels).mean(axis=1)
            mono -= mono.mean()
            n = min(1024, len(mono))
            if n < 128:
                return
            segment = mono[-n:] * np.hanning(n)
            spectrum = np.abs(np.fft.rfft(segment))
            spectrum /= max(1.0, np.sqrt(np.mean(segment * segment)) * n / 8.0)
            freqs = np.fft.rfftfreq(n, 1.0 / sample_rate)
            energy = float(np.sqrt(np.mean(mono * mono)))
            bass_mask = (freqs >= 35) & (freqs <= 180)
            bass = float(np.mean(spectrum[bass_mask])) if np.any(bass_mask) else energy
            peak = float(np.max(np.abs(mono)))
            energy_n = min(1.0, energy * 4.0)
            bass_n = min(1.0, bass * 0.45)
            peak_n = min(1.0, peak * 1.4)
            self._energy_smooth = self._energy_smooth * 0.84 + energy_n * 0.16
            self._bass_smooth = self._bass_smooth * 0.80 + bass_n * 0.20
            sens = self._reactive_sensitivity / 100.0
            rise_bass = max(0.0, self._bass_smooth - self._prev_bass)
            rise_energy = max(0.0, self._energy_smooth - self._prev_energy)
            onset = rise_bass * 3.8 + rise_energy * 1.5
            threshold = 0.045 + (1.0 - sens) * 0.105
            beat = onset > threshold and self._beat_cooldown <= 0
            if beat:
                self._beat_cooldown = int(8 + (1.0 - sens) * 12)
            else:
                self._beat_cooldown = max(0, self._beat_cooldown - 1)
            self._prev_bass = self._bass_smooth
            self._prev_energy = self._energy_smooth

            edges = np.geomspace(35, min(16000, sample_rate / 2), 25)
            bars = np.zeros(24, dtype=np.float32)
            for i in range(24):
                mask = (freqs >= edges[i]) & (freqs < edges[i + 1])
                if np.any(mask):
                    bars[i] = min(1.0, float(np.mean(spectrum[mask])) * 0.8)
            now = time.monotonic()
            if now - self._last_analysis_emit >= self._analysis_emit_interval:
                self._last_analysis_emit = now
                self.analysis.emit(self._energy_smooth, self._bass_smooth, peak_n, bars)
            if beat:
                self.beat_signal.emit(min(1.0, onset * 2.2))
        except Exception as exc:
            self.error.emit(f"FFT: {exc}")
