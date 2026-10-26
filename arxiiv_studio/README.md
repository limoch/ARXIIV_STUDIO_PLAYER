# ARXIIV.STUDIO — Show Control

Приложение для управления визуальными эффектами на экране (проектор / LED-стена)
на концерте, по принципу PowerPoint Presenter View: на своём мониторе ты видишь
панель управления + миниатюру вывода, а на выбранном экране открывается чистый
полноэкранный showtime-дисплей.

## Возможности

- **FLASH** — одиночная вспышка выбранным цветом по кнопке / хоткею
- **STROBE** — быстрое мерцание (частота регулируется), тумблер вкл/выкл
- **WHITE SCREEN** — просто включить/выключить сплошной цвет на весь экран
- **ELEMENT** — любая картинка/gif подключается через drag & drop и появляется
  на экране поверх фона по кнопке (плюс регулировка размера)
- **BACKGROUND** — фон меняется на лету без лагов: PNG, JPG, GIF (зацикленный)
  и MP4 (зацикленный автоматически). Все добавленные фоны предзагружены —
  переключение мгновенное, без повторной загрузки файла
- **KEYBINDS** — на каждое действие можно назначить свою клавишу
- Живая миниатюра showtime-экрана в панели управления
- Выбор любого подключённого монитора для вывода

## Установка

```bash
pip install -r requirements.txt
```

(Python 3.10+, работает на Windows / macOS / Linux)

## Запуск

```bash
python main.py
```

1. В правом верхнем углу выбери экран, на который будет выводиться показ
   (`SCREEN 1`, `SCREEN 2`, ...).
2. Нажми **OPEN DISPLAY** — на выбранном экране откроется чёрный полноэкранный
   showtime-дисплей, на своём мониторе останется панель управления.
3. Добавь фон(ы) через **[ BACKGROUND ]** — перетащи файлы в зону или нажми
   **ADD BACKGROUND**. Клик по фону в списке — мгновенно переключает его
   на выходном экране.
4. При желании перетащи картинку/gif в **[ ELEMENT ]** — она появится/исчезнет
   поверх фона по кнопке **SHOW ELEMENT** или хоткею.
5. В **[ EFFECTS ]** выбери цвет и жми **FLASH** / **STROBE** / **WHITE SCREEN**.
6. В **[ KEYBINDS ]** можно переназначить клавиши под свой пульт/клавиатуру.

## Структура проекта

```
arxiiv_studio/
├── main.py              # точка входа
├── control_window.py    # панель управления (твой монитор)
├── display_window.py    # showtime-дисплей (проектор/экран)
├── styles.py             # оформление (тёмная тема, моноширинный шрифт)
└── requirements.txt
```

## Как превратить в .exe (Windows)

1. Установи PyInstaller:

   ```bash
   pip install pyinstaller
   ```

2. Собери приложение (рекомендуется `--onedir`, а не `--onefile` — для
   `--onefile` Qt Multimedia распаковывается заново при каждом запуске,
   это может дать лишнюю задержку прямо перед началом показа):

   ```bash
   pyinstaller --noconfirm --windowed --name ARXIIV_STUDIO ^
       --collect-all PySide6 ^
       main.py
   ```

   Готовое приложение будет в `dist/ARXIIV_STUDIO/ARXIIV_STUDIO.exe`.
   Перед показом обязательно протестируй .exe на том же ПК и с теми же
   мониторами/проектором, которые будут использоваться на концерте —
   особенно проигрывание MP4-фонов (это зависит от кодеков Windows Media
   Foundation, установленных в системе).

3. Если нужна иконка — добавь `.ico`-файл и флаг `--icon=icon.ico`.

4. Если хочешь один файл (`--onefile`), команда такая же, но
   `--onefile` вместо `--windowed` не убирать, а добавить рядом:

   ```bash
   pyinstaller --noconfirm --onefile --windowed --name ARXIIV_STUDIO ^
       --collect-all PySide6 ^
       main.py
   ```

## Заметки / что можно доделать

- Позиционирование ELEMENT сейчас по центру экрана (только размер регулируется).
  Если нужно произвольное позиционирование — можно добавить drag прямо по
  миниатюре превью.
- Хоткеи сейчас локальные (работают, пока фокус на окне ARXIIV.STUDIO —
  как в PowerPoint), а не глобальные для всей ОС. Если нужны глобальные
  хоткеи (работающие даже когда фокус на другом приложении), это делается
  через отдельную библиотеку (`keyboard` или `pynput`) — могу добавить.
- Список фонов можно расширить до drag-переупорядочивания / плейлиста
  с автопереключением по таймеру, если понадобится для конкретного шоу.


## PLAYER + AUDIO REACTIVE

The built-in player analyzes the **same track that is playing**. No microphone or
LINE-IN is needed.

Features:
- drag & drop MP3/WAV/FLAC/OGG/M4A/AAC into PLAYER;
- PLAY / PAUSE / PREVIOUS / NEXT;
- seek and volume;
- real-time FFT spectrum visualization;
- bass/energy pulse;
- automatic flash on peaks;
- automatic strobe speed driven by energy.

The implementation uses `QAudioBufferOutput` from Qt Multimedia, which receives
decoded audio buffers directly from `QMediaPlayer` (Qt 6.8+ / FFmpeg backend).


## PLAYER / TRACKLIST UPDATE

The player now supports a show-style playlist workflow:

- **LOAD FOLDER** loads all supported audio files recursively, including subfolders.
- Folder scanning happens in a background thread, so the control window remains responsive with large libraries.
- You can also drop a folder directly into the audio drop zone.
- Tracks are shown as a larger, readable queue with `01`, `02`, `03` numbering.
- **Drag & Drop** inside the queue changes the actual playback order.
- **Search** filters a large playlist instantly without changing its underlying order.
- Double-click / Enter plays a selected track.
- `Space` = play/pause, `Ctrl+Left` / `Ctrl+Right` = previous/next, `Ctrl+F` = search.
- Duplicate files are ignored.
- FFT UI updates are throttled to ~30 FPS while beat/onset detection continues separately, reducing unnecessary GUI work.

The playlist keeps file paths in `Qt.UserRole`, so display text can be changed without breaking playback order.

## Playlist queue v4

The player now has a dedicated scrollable queue sidebar. Load a whole folder (including subfolders), then drag tracks vertically to change playback order. During drag, the grabbed tile becomes a floating enlarged ghost and neighboring tiles animate into the available slot. The queue also supports search/filtering and auto-scroll while dragging near the top/bottom edge.

Keyboard controls: `Space` play/pause, `Ctrl+Left/Right` previous/next, `Ctrl+F` focus search, `Esc` clear search.


## Playlist persistence / queue controls

- The queue is saved automatically to the OS application-data directory as `playlist_queue.json`.
- Existing files are restored on the next launch; missing files are skipped.
- **Right-click a track tile → REMOVE FROM QUEUE** removes that file from the queue.
- The manually arranged order is preserved. Automatic Crossfade never reorders the playlist.
- Track typography is slightly larger for live/show readability.

## Automatic Crossfade / Transition Engine

`audio_player.py` contains a standalone, documented DSP pipeline:

1. Offline analysis:
   - BPM / beat positions;
   - four-beat downbeat grid approximation;
   - integrated LUFS;
   - lightweight key estimate.
2. Decision:
   - `ΔBPM <= 5%` → Beatmatched EQ-Split;
   - `ΔBPM > 5%` → Echo/Reverb Tail Drop;
   - failed analysis → 3–5 s fallback crossfade.
3. Gain staging targets approximately `-14 LUFS` before rendering.
4. Heavy analysis/rendering runs in a background Qt thread.
5. Automatic Crossfade follows the current queue order and never changes it.
6. A rendered transition is preloaded into a second `QMediaPlayer` and started near the end of the current track. The real next track resumes at the elapsed transition position.

### Presets

`DEFAULT` and `Chill / Slow` are available. `Chill / Slow` applies approximately 6 dB attenuation to the low/sub-bass region on DSP-rendered transitions. This is intentionally implemented in the DSP engine rather than pretending that `QAudioOutput` itself has an EQ.

### Dependencies

Automatic Crossfade adds:


If analysis cannot be performed for a particular file, playback continues and the engine uses the normal crossfade fallback.


## Queue / video optimizations
- Track duration is loaded asynchronously and shown on each tile; the queue header shows total playlist duration.
- Right-click a track for a persistent color marker or a custom start position (`MM:SS` / `HH:MM:SS`).
- Per-track start positions and markers are stored in `playlist_queue.json` and are used for manual playback and crossfade targets.
- Video backgrounds are lazy-loaded: importing a video does not create a decoder. Only the selected video is instantiated, and inactive video decoders are released.
- Video display copies/paints at a capped ~30 FPS and the operator preview refreshes less often to keep the control UI responsive.

## Стабильность и диагностика

- Лог ошибок сохраняется в пользовательской папке данных приложения как
  `arxiiv_studio.log` (лог ротируется, старые файлы ограничены по размеру).
- Ошибки Python и сообщения Qt Multimedia/графического backend записываются в лог.
- Очередь сохраняется через временный файл с атомарной заменой, чтобы сбой во
  время сохранения с меньшей вероятностью повредил единственную копию очереди.
- Если очередь повреждена, приложение пытается сохранить её копию с суффиксом
  `.corrupt-...` и продолжить запуск.
- Если следующий трек не загружается при crossfade, переход отменяется по таймауту
  и текущая дорожка восстанавливает громкость вместо бесконечного ожидания.

Лог помогает найти причину вылета, но не гарантирует, что любой сбой будет
перехвачен: аварийное завершение внутри драйвера, кодека или Qt может обойти
обработчики Python. Перед живым выступлением проверь приложение на том же ПК,
с теми же аудиоустройствами, мониторами и видеофайлами.
