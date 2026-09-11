# Bug / Regression Risk Audit

> This document is a snapshot of the project at inspection time. Before modifying code, verify important claims against the current source.

Inspection point: 2026-09-12, Asia/Seoul. Inspector: GPT-5.6 Luna Reserve. The audit used source inspection, the available unit suite, existing Qt/OpenGL probes, and small read-only runtime checks. Evidence and inference are explicitly separated below.

## A. Current or nearly certain bugs

### A-1. Persistent NEGATIVE is cleared by sleep/wake

- Priority: A / high
- Files/functions: `desktop_pet.py`, `PetCanvas.sleep`, `PetCanvas.wake`
- Condition: persistent `idle_kind == 'negative'`, followed by sleep or wake
- Symptom: `cancel_idle_reaction(include_negative=True)` removes the negative expression
- Evidence: reproduced in Hibana runtime; state changed from `negative` to `None`
- Existing tests: not caught
- Difficulty: low
- Recommended timing: next explicit mood-policy decision

This conflicts with the product rule that successful petting is the only direct negative-to-positive transition. For the current poke finishing scope this is intentionally deferred and must not be changed without an explicit UX/mood decision.

### A-2. Non-object active-profile state can crash startup

- Priority: A / medium
- Files/functions: `model_profiles.py`, `ProfileRegistry.active_id`
- Condition: `local/app-state.json` contains `[]`, `null`, or a JSON string
- Symptom: uncaught `AttributeError` from `data.get`
- Evidence: direct read-only check reproduced the exception for all three values
- Existing tests: only normal object state is covered
- Difficulty: low
- Recommended timing: now

### A-3. Full unittest discovery is red because of missing native research module

- Priority: A / test infrastructure
- Files/functions: `research/__init__.py`
- Condition: `python -m unittest discover -v`
- Symptom: `ModuleNotFoundError: research._v3cpp`
- Evidence: current discovery run: 23 tests passed, 1 import error
- Existing tests: targeted invocation hides the issue
- Difficulty: low
- Recommended timing: now if discovery/CI is the acceptance command

This is unrelated to poke/annoyance runtime behavior.

## B. High-risk potential bugs

### B-1. Cross-profile state reuses stale visual bounds

- Priority: B / high
- Files/functions: `desktop_pet.py`, `load_state`, `recover_geometry`, `refresh_input_region`
- Condition: switch profiles near a screen edge; saved state belongs to another model
- Symptom: new model can extend beyond the right/bottom movement area after real framebuffer bounds replace the old bounds
- Evidence: constructed IceGirl-edge state → Hibana runtime produced `visual_within_movement_area=False` after the new bounds were read
- Existing tests: profile verifiers use isolated state files and do not test cross-profile geometry
- Difficulty: medium
- Recommended timing: now / before more profile switching work

The state stores `profile_id`, but `load_state` does not use it to invalidate or recompute model-specific `visual_bounds`.

### B-2. Petting during transient poke restores the stale poke expression

- Priority: B / high
- Files/functions: `desktop_pet.py`, `start_reaction`, `register_poke`, `cancel_idle_reaction`
- Condition: transient 疑惑/白眼/生气 is active, then a successful petting stroke occurs
- Symptom: positive starts, but after positive ends the old poke expression returns instead of the baseline
- Evidence: IceGirl runtime check produced `after_positive = 疑惑.exp3.json`
- Existing tests: persistent-negative recovery is covered conceptually; transient-poke/petting ordering is not
- Difficulty: low to medium
- Recommended timing: before poke is considered complete

### B-3. Missing positive capability enters a no-op positive state

- Priority: B / medium
- Files/functions: `desktop_pet.py`, `loaded`, `start_reaction`
- Condition: a future profile has no `expressions.positive` candidates
- Symptom: reaction count and hold timer advance, but there is no positive expression or parameter layer
- Evidence: runtime check with IceGirl positive candidates removed produced `reaction_values={}` and a live reaction state
- Existing tests: profile parsing only; no runtime test for positive capability absence
- Difficulty: low
- Recommended timing: before adding more capability-optional profiles

### B-4. Transparent gaps inside the head interaction rectangle are not native click-through

- Priority: B / medium
- Files/functions: `desktop_pet.py`, `head_input_region`, `apply_input_region`
- Condition: pixel is inside the profile head rect but outside strict rendered silhouette
- Symptom: the native mask still contains the point; left click can participate in drag handling and right click can open the menu
- Evidence: IceGirl runtime found a head-rect point with `silhouette_contains=False` and `mask.contains=True`
- Existing tests: only test transparent pixels outside the main character region
- Difficulty: medium
- Recommended timing: before changing input semantics

This is an intentional trade-off made to keep hover strokes continuous across hair gaps, so it is a policy/architecture issue rather than an unconditional regression.

### B-5. Profile validation is incomplete for nested behavior values

- Priority: B / medium
- Files/functions: `model_profiles.py`, `ModelProfile._validate_assets`
- Condition: interval lists have wrong length, probabilities are out of range, or required nested behavior keys are missing
- Symptom: profile discovery may succeed, followed by `KeyError`, `TypeError`, or invalid scheduler behavior at runtime
- Evidence: validation checks collection types for several fields but not all shapes/ranges consumed by `IdleScheduler`, `Relocator`, and `StrokeDetector`
- Existing tests: unknown assets, thresholds, and basic capability absence only
- Difficulty: medium
- Recommended timing: next capability/profile addition

### B-6. Profile-switch process failure has no handshake rollback

- Priority: B / medium
- Files/functions: `desktop_pet.py`, `select_profile`
- Condition: `startDetached` returns success but the new process fails during model/GL initialization
- Symptom: old process exits, active profile points to the failed profile, and the next launch can repeat the failure
- Evidence: static control-flow finding; no failing initialization was induced
- Existing tests: normal profile menu and profile discovery only
- Difficulty: medium
- Recommended timing: next profile-management change

This is an inference from the process handoff design, not a currently reproduced production failure.

### B-7. Native hover/petting coverage is currently unreliable

- Priority: B / verification risk
- Files/functions: `probe_poke.py`, `probe_petting.py`, `probe_idle.py`
- Condition: button-free QTest mouse movement in the current Windows/Qt environment
- Symptom: event filter sees no hover moves; detector never activates; probes stop before reaction assertions
- Evidence: current probes produced zero mouse-move counts and `reaction_delta == 0`
- Existing tests: direct-call full verifier bypasses this path
- Difficulty: medium
- Recommended timing: before declaring poke complete

This is confirmed as a test/harness limitation. It is not enough evidence to label the production mouse-move path broken.

## C. Lower-priority and edge cases

### C-1. Double-click semantics are unspecified

- Priority: C / low
- Files/functions: `desktop_pet.py`, mouse event handlers
- Condition: OS/Qt emits a double-click sequence
- Symptom: depending on event sequence, two clicks may count as two pokes or the second press may be handled as a double-click event without a corresponding poke
- Evidence: no `mouseDoubleClickEvent` override and no native double-click test
- Existing tests: not covered
- Difficulty: low
- Recommended timing: later, after product semantics are decided

### C-2. Lost mouse-release can leave pointer state active

- Priority: C / low
- Files/functions: `begin_pointer_press`, `end_pointer_release`
- Condition: mouse capture is interrupted by focus/application/window state changes
- Symptom: subsequent moves may continue through pointer-press logic instead of hover logic
- Evidence: control-flow possibility; not reproduced
- Existing tests: not covered
- Difficulty: low
- Recommended timing: later

### C-3. Invisible relocation window may still intercept input

- Priority: C / low to medium
- Files/functions: `tick`, relocation opacity handling
- Condition: relocation reaches zero opacity while its input mask remains installed
- Symptom: an invisible window can briefly consume input or interrupt the underlying application
- Evidence: opacity changes are tested, but no native input test exists during the zero-opacity phase
- Existing tests: programmatic relocation only
- Difficulty: medium
- Recommended timing: later

### C-4. Legacy ZIP encoding heuristic can misclassify uncommon archives

- Priority: C / low
- Files/functions: `prepare_model.zip_metadata_encoding`
- Condition: unflagged Japanese/mixed-encoding filenames where GBK and CP932 both produce a lower mojibake score
- Symptom: wrong extracted filenames and missing model/profile assets
- Evidence: current GBK fixture passes; CP932/mixed legacy cases are not covered
- Existing tests: GBK, UTF-8, and ASCII only
- Difficulty: medium
- Recommended timing: later, when onboarding another legacy model

### C-5. Large textures and giant character sizes are not fully stress-tested

- Priority: C / low
- Files/functions: `prepare_model.copy_runtime_texture`, `PetCanvas.paintGL`, mask readback, resize path
- Condition: several 4096 textures or character size near the 3000-pixel upper bound
- Symptom: startup latency, GPU memory pressure, expensive QRegion/mask operations, or possible taskbar flicker
- Evidence: current machine passes existing checks; giant-size/taskbar-flicker behavior is not fully verified
- Existing tests: small texture unit tests and limited resize probes
- Difficulty: medium to high
- Recommended timing: later / hardware-specific validation

## Recommended Priority

1. 지금 당장 고칠 것

   - persistent NEGATIVE sleep/wake behavior after the separate UX policy is explicitly decided
   - malformed `active_profile` startup handling
   - cross-profile geometry re-clamp
   - restore full unittest discovery if it is used as the acceptance command

2. 다음 기능 추가 전에 볼 것

   - transient poke → petting expression restoration
   - native hover test harness
   - no-positive-capability fallback
   - transparent head-gap input policy
   - nested profile validation

3. 나중에 미뤄도 되는 것

   - double-click semantics
   - lost mouse-release recovery
   - zero-opacity relocation input behavior
   - uncommon legacy ZIP encodings
   - giant character size, taskbar flicker, and low-memory GPU stress
