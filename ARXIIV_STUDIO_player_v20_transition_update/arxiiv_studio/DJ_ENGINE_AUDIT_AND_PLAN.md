# ARXIIV.STUDIO DJ engine audit and implementation plan

## Verified in the supplied v20 archive

- `control_window.py` imports `AudioReactivePlayer` and wires its analysis, beat, position, state, track and crossfade signals into the existing Qt UI.
- The queue is persisted as JSON; per-track start offsets and transition settings are saved in the queue state.
- The current `audio_player.py` creates two `QMediaPlayer` objects and two `QAudioOutput` objects. It performs crossfades by running a 30 ms `QTimer` and setting the output volumes.
- `prepare_transition`, `start_prepared_transition` are compatibility no-ops returning `False`.
- Because each `QMediaPlayer` owns its output path, the current design does not expose a common PCM mixer stage where per-deck EQ, pitch/tempo processing, and sample-accurate automation can be implemented.
- `requirements.txt` currently lists PySide6, NumPy and Mutagen. The uploaded archive itself does not bundle FFmpeg; this environment has FFmpeg installed, but end-user Windows setup must document/provision it.
- The archive's README describes show-control features as well as the player UI; keep existing visual/show-control behavior intact while refactoring audio.

## Added in this working update

- `dj_audio_engine.py`: headless DSP primitives, FFprobe content-based inspection, and FFmpeg decoding to float32 stereo PCM. It does not trust file extensions.
- `tests/test_dj_audio_engine.py`: automated checks for crossfade curves, deck routing, gain, limiter bounds and PCM block slicing.
- This file documents the boundary between verified current behavior and the recommended next implementation steps.

## Important status

The new module is a tested DSP/decode foundation, **not yet the live audio backend wired into the GUI**. The GUI continues to use Qt Multimedia in this update. Do not describe this update as a completed two-deck DJ engine: it does not yet provide a sounddevice/WASAPI callback, per-deck EQ filters, tempo/pitch shifting, beatgrid sync, or a full Auto DJ scheduler.

## Next implementation sequence

1. **Backend contract and safety boundary**
   - Add `AudioBackend` interface with `load(deck, path)`, `play/pause/stop(deck)`, `seek(deck, ms)`, `set_gain(deck, value)`, `set_eq(deck, low, mid, high)`, `set_crossfader(value)`, `set_master_gain(value)`, `status(deck)` and output-device selection.
   - Keep all file I/O, FFmpeg process management and analysis off the audio callback.
   - Use bounded per-deck PCM ring buffers; callbacks must never wait on a decoder, UI lock or disk operation.

2. **Streaming decode and live output**
   - Replace whole-file decoding in production with FFmpeg pipe streaming into bounded PCM buffers.
   - Add a tested WASAPI-capable output backend (e.g. sounddevice/PortAudio) with a callback that only pops available PCM, applies DSP and writes output. Define underflow behavior as silence plus a counter, not blocking.
   - Make seek restart/flush the decoder safely and invalidate stale buffered samples.
   - Use one common output stream for both decks; this is required for coherent mixing.

3. **DSP**
   - Apply per-deck gain and 3-band EQ before crossfader.
   - Smooth all UI-controlled parameters over short ramps to avoid zipper noise/clicks.
   - Provide linear and equal-power crossfade curves and a safety limiter.
   - Test filter response using sine sweeps and test output for finite values, clipping and discontinuities.

4. **UI adapter**
   - Preserve existing playlist, queue state, resizable workspace, display window and effects.
   - Replace the current `AudioReactivePlayer` internals only behind a compatibility adapter; avoid changing unrelated visual UI code.
   - Add deck A/B controls, cue and sync controls only after the live mixer is verified.

5. **Analysis and Auto DJ**
   - Worker processes produce cached track profiles: BPM confidence, beatgrid, loudness and phrase candidates.
   - Transition planner creates an automation plan; the audio callback executes it sample/block-wise from precomputed parameters.
   - Manual controls take priority; cancel/override must smoothly hand off from automation.

## Acceptance criteria before calling the DJ engine complete

- Two independent files play simultaneously through one output stream.
- A/B crossfader endpoints isolate the selected deck; midpoint follows the chosen curve within numerical tolerance.
- Gain and EQ demonstrably alter captured PCM output; EQ response is measured, not inferred from UI state.
- Sync is tested on synthetic clicks with known tempo and phase drift; target must be chosen and measured before release.
- No audio callback performs file reads, waits for locks, launches subprocesses, or allocates large arrays.
- 60-minute soak test with repeated load/seek/crossfade operations has zero crashes and zero unhandled device errors; underruns are counted and target zero on the reference machine.
- Test supported formats and mislabelled AAC-in-MP4 files. Unsupported/corrupt files report a recoverable error.
- Manual fader/EQ movement during an automatic transition does not cause abrupt parameter jumps.
- A GUI stress test (resizing panels, changing tracks, toggling visuals) does not produce audio underruns on the reference machine.
