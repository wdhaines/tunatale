# UI review backlog (2026-06-10)

Open items from the post-redesign UI review (screenshots-driven, Jet Age/Noir
theme era). Everything else from that review shipped: review keyboard
shortcuts + answer hierarchy + double-grade guard, cards `[sound:]` stripping +
overflow menu, nav badge a11y, transcript help disclosure + speaker chips,
dolphin logo/favicon, home dashboard with Continue, shared queue-stats store.

## Open

1. **Custom audio player** — build only as the foundation for Listen mode
   (`docs/learning-modes.md`), not as standalone polish. Scope: hidden
   `<audio>` element stays (free buffering/MediaSession); custom UI adds
   playback speed (0.7–1×), skip-back ~5s, section-aware seek (Key Phrases /
   Natural / Slow), per-lesson resume, and exposes current playback time for
   future blur-KNOWN subtitle sync. Also demote "Download All Sections" to a
   secondary action and drop the "Audio Player" card heading.

2. **Per-grade interval hints on review buttons** (Anki-style "10m / 1d / 4d"
   under Again/Hard/Good/Easy). Backend work: preview FSRS schedule per rating
   without committing a grade. Parity-sensitive — read
   `.claude/rules/anki-queue-parity.md` first and reuse `schedule()` /
   load-balancer helpers rather than duplicating (Pre-Layer checklist applies).

3. **Minor, grab-bag**
   - Theme toggle: cycle-button (🖥️/☀️/🌙) is ambiguous; a 3-option menu is
     clearer.
   - Transcript mastery ramp is color-only encoding; add a redundant cue for
     red/green colorblindness (e.g. dotted underline by bucket).
   - Suppressed names render as strikethrough (~~Ana~~) which reads as an
     error; dim/mute instead.

## Decided — do not revisit

- KNOWN words stay on the green end of the mastery ramp (deliberate,
  commit 279f571); the "mute KNOWN to plain text" idea was rejected.
- Logo asset lives at `frontend/src/lib/assets/logo.png` (512px, transparent,
  processed from the original); reuse it (e.g. home empty state) rather than
  reprocessing.
