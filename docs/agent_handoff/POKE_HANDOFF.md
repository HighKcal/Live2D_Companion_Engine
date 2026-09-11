# Poke / Annoyance Implementation Handoff

> This document is a snapshot of the project at inspection time. Before modifying code, verify important claims against the current source.

## Inspection metadata

- Inspection point: 2026-09-12, Asia/Seoul
- Inspector: GPT-5.6 Luna Reserve
- Method: source inspection plus available unit tests, Qt/OpenGL probes, and small read-only runtime checks
- Inspected workspace: `C:\Codex_project\live2d_pet`
- The user-provided alias `C:\Codex\_project\live2d\_pet` was not present at inspection time.

## Current status

IceGirl poke/annoyance progression is mostly implemented and is in verification/finishing stage. The implementation is optional and profile-driven; no IceGirl-specific common-engine branch is required.

The code already contains the following behavior path:

- short left click on the strict silhouette → optional poke
- movement beyond the configured threshold → drag, never poke
- button-free head movement → `StrokeDetector` petting path
- right click → context menu, never poke
- transparent outside-mask click → underlying application, never poke
- annoyance thresholds on IceGirl: 2 / 4 / 6 / 8
- transient threshold reactions expire normally
- threshold 8 enters persistent `negative`
- successful petting can leave persistent negative and start positive directly

The implementation is present, but native hover-based verification is incomplete because the current QTest probes do not deliver non-button `MouseMove` events reliably in the inspected Windows/Qt environment.

## Main files and functions

- `desktop_pet.py`
  - `PetCanvas.mousePressEvent`
  - `PetCanvas.mouseMoveEvent`
  - `PetCanvas.mouseReleaseEvent`
  - `PetCanvas.contextMenuEvent`
  - `PetCanvas.leaveEvent`
  - `PetWindow.begin_pointer_press`
  - `PetWindow.pointer_move`
  - `PetWindow.end_pointer_release`
  - `PetWindow.begin_drag` / `end_drag`
  - `PetWindow.hover`
  - `PetWindow.reset_annoyance` / `register_poke`
  - `PetWindow.start_idle_reaction` / `cancel_idle_reaction`
  - `PetWindow.start_reaction`
  - `PetWindow.tick`
- `model_profiles.py`
  - optional `behavior.poke` validation in `ModelProfile._validate_assets`
  - profile-to-runtime behavior mapping in `behavior_settings`
- `profiles/icegirl.json`
  - IceGirl poke capability and threshold assets
- `probe_poke.py`
  - intended end-to-end poke/annoyance scenario
- `test_model_profiles.py`
  - optional poke schema and threshold validation

## Input flow

1. `PetCanvas.mousePressEvent` clears an active stroke candidate.
2. A left press calls `begin_pointer_press`, records global/local positions and press time, checks strict silhouette membership, and grabs the mouse.
3. While pressed, `mouseMoveEvent` calls `pointer_move`; it does not call `hover`.
4. `pointer_move` compares displacement with `behavior.poke.drag_threshold_pixels` when present, otherwise the existing 8-pixel fallback.
5. Once the threshold is crossed, `begin_drag` owns the gesture. `end_pointer_release` sees `was_dragging` and returns without registering a poke.
6. A non-drag left release checks strict silhouette at press and release plus `max_click_seconds`, then calls `register_poke`.
7. A button-free move calls `hover`, which maps to model coordinates and samples `StrokeDetector`.
8. A successful stroke calls `start_reaction`; it is independent of the click/poke count.
9. Right click goes through `contextMenuEvent` and `open_menu`; it does not call `register_poke`.

## Profile-driven capability

`behavior.poke` is optional. Profiles without this object retain the existing behavior and do not get annoyance progression. The current shape is:

```json
"poke": {
  "reset_seconds": 15.0,
  "max_click_seconds": 0.5,
  "drag_threshold_pixels": 8.0,
  "reactions": [
    {"threshold": 2, "asset": "...", "hold_seconds": [1.2, 1.8]},
    {"threshold": 8, "asset": "...", "persistent_negative": true}
  ]
}
```

IceGirl mapping in `profiles/icegirl.json`:

| Count | Asset | Kind |
|---:|---|---|
| 2 | `疑惑.exp3.json` | transient poke |
| 4 | `白眼.exp3.json` | transient poke |
| 6 | `生气.exp3.json` | transient poke |
| 8 | `脸黑.exp3.json` | persistent negative |

The implementation only restarts a reaction at an exact configured threshold. Counts 1, 3, 5, and 7 do not restart the currently displayed transient reaction.

## Annoyance timeout

- `annoyance_count` and `annoyance_last_poke` live on `PetWindow`.
- A new poke after `reset_seconds` starts from count 1.
- `tick` cancels a transient `poke` expression and resets the count after inactivity.
- The timeout is deliberately skipped while `idle_kind == 'negative'`.
- Further poke input while persistent negative is displayed does not increment or restart it.

## Persistent NEGATIVE and positive recovery

At threshold 8, `register_poke` calls `start_idle_reaction(..., kind='negative')` with an infinite hold. `idle_kind == 'negative'` is protected by the default `cancel_idle_reaction` behavior.

Successful petting enters `start_reaction`:

- it stores the previous expression
- it removes persistent negative without restoring it first
- it resets annoyance
- it starts the positive parameter/expression layer directly
- positive completion restores the stored expression

Important policy decision for the next implementation session:

> The current sleep/wake behavior clears persistent NEGATIVE through `include_negative=True`. This is intentionally not being changed as part of the poke finishing work. It is a separate UX/mood policy decision and remains deferred.

## Known verification issue

`probe_poke.py`, `probe_petting.py`, and the IceGirl idle probe fail at the native hover/petting portion because button-free QTest `MouseMove` events are not delivered in the current environment. The poke probe passes its early transparent/right-click and drag checks, then stops before the threshold sequence.

This is a verification gap, not proof that the production hover path is broken. The existing full verifier calls `w.hover()` directly and therefore does not cover the native mouse-move route.

## Test baseline

- Targeted unit suite: 23 tests passed:

  ```text
  python -m unittest test_model_profiles test_pet_behavior test_prepare_model -v
  ```

- Full unittest discovery: 23 tests passed and 1 error. The error is `research._v3cpp` missing from `research/__init__.py`; this is separate from poke functionality.
- Full profile verifier: hibana, tsubaki, and icegirl passed their existing full regression checks.
- Poke-specific end-to-end threshold checks did not complete because the QTest hover phase failed first.

## Minimum next-session order

1. Re-read the current source and confirm the above paths are unchanged.
2. Make the native hover test reliable, or add a narrowly scoped runtime/state test that does not falsely claim native hover coverage.
3. Verify click, drag, petting, right-click, transparent-area, and double-click semantics.
4. Run the full IceGirl 1/2/3/4/5/6/7/8 sequence and timeout/persistent-negative checks.
5. Run hibana and tsubaki regression checks and confirm that profiles without `behavior.poke` remain unchanged.
6. Keep sleep/wake versus persistent mood policy deferred unless explicitly re-opened.

## Existing regressions that must remain green

- transparent framebuffer and native alpha click-through
- drag displacement and clamping
- relocation fade phases and user interruption
- stationary pointer and single-pass petting rejection
- repeated stroke detection, cooldown, inactivity, and post-drag blocking
- petting response parameter ownership and previous-expression restoration
- ambient and major idle scheduling
- persistent negative behavior for the existing idle path
- sleep capability filtering for profiles without sleep motion
- profile discovery, asset mapping, active profile persistence, and source/runtime preparation tests
- hibana, tsubaki, and icegirl full verifiers
