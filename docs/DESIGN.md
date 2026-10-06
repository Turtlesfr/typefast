# TypeFast design

TypeFast is a tray app for X11 desktops that types speech into whatever field
has the focus, live, then swaps that text for a polished transcript when the
user finishes.

## Goals

- Start in under a second from anywhere, with the keyboard only.
- Always show what was heard: an earlier prototype that typed silently felt invisible.
- Never damage text the user wrote: only ever replace what TypeFast typed.
- A custom vocabulary that is quick to pick per dictation.
- Usable by anyone: own API key, settings UI, no personal data in the code.

## Flow

```
Ctrl ×3 ──► overlay (keyboard grab, focus untouched)
              ← → language, ↓ grid, a-z jump, Space tick, Enter
                 │
                 ▼
          DictationSession
   mic ──┬─► LiveTranscriber (WebSocket) ──► LiveTranscript ──► Typer ──► focused field
         │                                         └──────────► HUD (live text)
         └─► Polisher (Dictation API, uploads while recording)
Ctrl ×3 ──► stop ──► PolishResult ──► Typer.replace() or clipboard
```

## Components

| Module | Job |
| - | - |
| `x11.py` | XInput2 raw key and button events on the root window. Raw events never grab anything. Events from the XTEST device (our own typing) are ignored. |
| `taps.py` | Pure tap-sequence detector: trigger key pressed and released alone, within 350 ms, N times inside the window. Any other key resets. |
| `overlay.py`, `overlay_model.py` | Override-redirect GTK popup that grabs the keyboard and pointer, so the app below keeps its focus and caret. The model holds all key handling and is unit tested. |
| `audio.py` | sounddevice capture, 16 kHz mono s16le, 50 ms chunks, fanned out to subscribers. |
| `streaming.py` | AssemblyAI v3 streaming. `universal-streaming-multilingual` (word-by-word partials, en/es/fr/de/it/pt) or `universal-3-6-pro` (32 languages, language steering, rewrites the turn about once a second). |
| `textops.py` | Live transcript from turns, Backspace-and-type edit planning, final clean-up. |
| `polish.py` | Dictation API (`/v1/transcribe/live`), multipart body streamed with chunked encoding while recording. Segments of at most ~110 s, cut at the first end of turn after 45 s. Firm deadline after stop (`polish_timeout_s`, 10 s): then the live text stays, and requests still in flight deliver a late result to the history. One retry from memory for a segment that fails fast, within the deadline. Timings are logged per segment. |
| `injector.py` | One worker thread owns the field. It diffs what it typed against the target and only uses Backspace and typing. Small chunks (8 characters live). Multi-line or long final texts are pasted whole (Ctrl+V, or Ctrl+Shift+V in terminals) and stay in the clipboard. |
| `clipboard.py` | Takes the CLIPBOARD selection in-process (so it is owned before Ctrl+V), answers the first requests, then hands the text to xclip. Never restores the previous clipboard: Chrome pre-reads every new clipboard immediately and Gmail handles Ctrl+V later, so any restore could be what gets pasted (it happened twice in field tests). |
| `xkeys.py` | XTEST typing in-process. Uses only the active layout; characters it lacks are bound once to spare keycodes (same symbol in every layout slot, LRU reuse, released on quit). Before every key it checks that no modifier is physically held and that the Typer has not been paused (trigger press, freeze, new session). |
| `styles.py` | Plain / Clean / Formatted: which Dictation output goes into the field, the `llm_instruction` for Formatted (editable), per-style deadline (25 s for Formatted) and backup delay (2.5 s Clean, 8 s Formatted, none for Plain). |
| `rewrite.py` | Backup rewrite of the live text (or of an unrewritten transcript) through the AssemblyAI LLM Gateway, same key, `gemini-2.5-flash-lite` by default (about 2.5 s). Hedged: it only starts when the Dictation API is late, and the first good answer wins. |
| `hud.py` | Click-through popup (empty input shape) with level meter, timer, live text and status. |
| `app.py` | State machine `idle → overlay → recording → polishing → idle`, tray actions, settings, IPC. |
| `ipc.py` | Unix socket in `$XDG_RUNTIME_DIR` for `typefast toggle` and single-instance control. |

## Safety rules for the field

1. Typing only ever removes characters TypeFast typed (the diff starts from its own record).
2. Once text is in the field, any real key press or click freezes the field for the
   rest of the session. The final text then goes to the clipboard. Exception: a click
   used to open the tray menu and choose "Finish and polish".
3. The final swap is skipped (clipboard instead) if a different window has the focus.
4. If the polish fails, is late, or returns nothing, the live text stays as it is.

## Decisions

- **Trigger**: triple Ctrl tap by default, because three clean taps never happen by
  accident and five are slow to fire and easy to miscount. A `typefast toggle`
  command covers people who prefer a regular shortcut.
- **Final text**: the Dictation API returns both a verbatim transcript and a
  cleaned one. Clean is the default; both are kept in the history.
- **Vocabulary**: one shared list for all languages (names and product terms are the
  same everywhere), in a fixed grid with type-ahead jump rather than a filter, so the
  layout never shifts under the user's eyes. Up to 100 terms per dictation (API limit).
- **X11 only**: global key listening and typing into other apps are not possible
  on Wayland without root-level tricks; the app explains this at startup.

## Verified during development (October 2026)

- Dictation API in French with keyterms: 25 s of audio, result about 2 s after the
  upload ended; with real-time upload, about 0.5 s after stop.
- Both streaming models with French and keyterms.
- xdotool was dropped after field tests: with es/fr/us layouts it switched the
  active layout 28 times while typing 250 characters, typed "Qhora" for "Ahora",
  and coincided with a 25 s GNOME Shell stall. The XTEST injector makes no layout
  switches.
- Apps behind IBus (GTK's default input method on Ubuntu) drop a few keys when
  Backspace follows typing with no pause, or the reverse: 1 wrong live rewrite in
  12 without a pause, none in 36 with 30 to 60 ms. The Typer waits 60 ms at each
  change of direction.
- The Dictation API's latency varies a lot: about 0.5 s after the last byte on a good
  morning, 2 to 13 s (sometimes more) the same afternoon for a 6 to 12 s clip. The first
  version waited 90 s, retried, then fell back to the pre-recorded API, so a polish could
  take minutes; hence the deadline and the late delivery.
- Restoring the user's clipboard after a paste is not safe: in Gmail the paste read came
  more than 0.4 s after Chrome's automatic pre-read, so the restored content was pasted.
- XTEST cannot make "is Ctrl up?" and "press the key" atomic. A Ctrl pressed in that
  gap is detected right after the key lands; the field is then frozen and the final
  text goes to the clipboard, so a shortcut that slipped through (select all, delete
  word) is never followed by more typing.
- Live typing, corrections, typed and pasted final swap, clipboard restore.
