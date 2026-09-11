# NEXT FEATURE RECON

> This document is a snapshot of the project at inspection time. Before modifying code, verify important claims against the current source.

## Inspection scope and authority

- Inspection date: 2026-09-12, Asia/Seoul.
- Requested path: `C:\\Codex\\_project\\live2d\\_pet`.
- Actual inspected workspace: `C:\\Codex_project\\live2d_pet`.
- The requested alias was not present in this environment.
- Read first: `CURRENT_STATUS.md`, `POKE_HANDOFF.md`, `BUG_AUDIT.md`, and `ANTIGRAVITY_REVIEW.md`.
- When a handoff document conflicts with source, the current source below is authoritative.
- No application source, profile, test, probe, model/runtime asset, Git setting, or README was modified.

The independent review's claim that the previous “double-click press is lost” concern is a false positive is retained: the review exercised the current Qt path and observed the base double-click handling forwarding into `mousePressEvent`, producing two normal click/poke registrations. That finding only addresses event delivery. It does not define the future semantic relationship between double-click surprise and poke counting.

Baseline test executed during this reconnaissance:

```text
.\\.venv\\Scripts\\python.exe -X utf8 -m unittest test_model_profiles test_pet_behavior test_prepare_model -v
Ran 23 tests ... OK
```

The existing GUI probes were not rerun because they generate artifacts and their known QTest button-free-hover limitation is already documented. The full discovery issue (`research._v3cpp` import failure) remains unrelated to these features.

## Current architecture

### Main files and responsibilities

| File | Relevant classes/functions | Responsibility |
|---|---|---|
| `desktop_pet.py` | `PetCanvas` | Qt press/move/release/context-menu dispatch |
| `desktop_pet.py` | `PetWindow.begin_pointer_press`, `pointer_move`, `end_pointer_release` | click candidate, drag threshold, release classification |
| `desktop_pet.py` | `hover`, `StrokeDetector` integration | button-free petting/stroke detection |
| `desktop_pet.py` | `register_poke` | annoyance count and threshold reactions |
| `desktop_pet.py` | `start_idle_reaction`, `cancel_idle_reaction` | ambient/poke/negative expression lifecycle |
| `desktop_pet.py` | `start_reaction`, `cancel_reaction`, `tick` | positive lifecycle, fade, timeout, relocation blocking |
| `desktop_pet.py` | `build_menu`, `open_menu` | context menu and menu-active cancellation policy |
| `app.py` | `Canvas.initializeGL` | loads all runtime expressions and motions into indexes |
| `app.py` | `Canvas.select_expression` | resets expression parameters and selects native expression |
| `app.py` | `Canvas.sleep`, `Canvas.wake` | global motion/expression reset and sleep motion |
| `app.py` | `Canvas.paintGL` | base values → behavior layer → native `model.Update()` → draw |
| `model_profiles.py` | `ModelProfile._validate_assets` | profile/runtime asset validation |
| `model_profiles.py` | `motion_asset`, `expression_candidates`, `behavior_settings` | semantic capability lookup |
| `pet_behavior.py` | `StrokeDetector`, `IdleScheduler`, `Relocator` | gesture, autonomous idle, and movement scheduling |

### Current input flow

1. `PetCanvas.mousePressEvent()` first calls `clear_petting_input()`.
2. A left press while awake calls `begin_pointer_press()`.
3. `begin_pointer_press()` records global/local position and time, checks strict silhouette membership, resets the detector, and calls `grabMouse()`.
4. While `pointer_pressed` is true, `mouseMoveEvent()` calls `pointer_move()` only. It does not call `hover()`.
5. `pointer_move()` compares displacement with `profile.behavior.poke.drag_threshold_pixels`, falling back to 8px when poke is absent. Crossing the threshold calls `begin_drag()`.
6. `mouseReleaseEvent()` calls `end_pointer_release()`.
7. A non-drag release is a poke candidate only when both press and release are inside the strict silhouette and the press duration is within `max_click_seconds`.
8. Button-free movement reaches `hover()` and then `StrokeDetector.sample()`. A successful stroke calls `start_reaction()`.
9. Right click reaches `contextMenuEvent()` → `open_menu()` and never directly calls `register_poke()`.

The native window mask is the dilated framebuffer silhouette unioned with the profile head rectangle. The strict click test uses the non-dilated silhouette. Consequently, a transparent gap inside the head rectangle can receive an event even though `silhouette_contains()` is false. This is an existing interaction trade-off used to keep hover strokes continuous.

### Current reaction ownership

The project does not have a reaction stack or general FSM.

- Positive uses one `reaction_expression` slot, `reaction_values`, `reaction_until`, and a fade `reaction_level`.
- Ambient, transient poke, and persistent negative use one `idle_expression` slot, `idle_kind`, `idle_previous_expression`, and `idle_until`.
- `cancel_idle_reaction()` protects `idle_kind == 'negative'` unless `include_negative=True` is explicitly passed.
- `IdleScheduler` knows autonomous `ambient`, `negative`, and `sleep`; it does not know surprise or user-triggered greeting.
- `Canvas.paintGL()` applies the positive parameter layer before native `model.Update()`. Native motions can therefore override overlapping parameters during update.

`PetCanvas.sleep()` and `wake()` intentionally call `cancel_idle_reaction(include_negative=True)`. Sleep/wake clearing persistent NEGATIVE is a separate UX policy decision and must not be changed as part of these feature additions.

## 1. Double-click surprise

### Current insertion points

The relevant existing points are:

- `PetCanvas.mousePressEvent()`
- `PetCanvas.mouseReleaseEvent()`
- `PetWindow.end_pointer_release()`
- `PetWindow.register_poke()`
- `PetWindow.start_idle_reaction()` / `cancel_idle_reaction()`
- `PetWindow.tick()`

There is no explicit `PetCanvas.mouseDoubleClickEvent()` override in the current source.

### Qt event relationship and the false positive distinction

The independent review reports that, in the current PySide6/Qt environment, the base double-click path forwards the second double-click press into `PetCanvas.mousePressEvent()`. Its runtime trace observed two ordinary press/release paths and therefore two normal poke registrations. This means the earlier concern that a second press is necessarily lost is a false positive.

However, the current system still has no semantic double-click classification. Each qualifying release currently calls `register_poke()` immediately. Therefore a future double-click can still become:

- two annoyance increments,
- one or two increments depending on where an event is intercepted,
- or surprise plus an already-created poke reaction if surprise is added after the first release.

Those are semantic/arbitration risks, not Qt press-loss risks.

### Recommended minimal arbitration

Do not begin by overriding `mouseDoubleClickEvent()` and calling the existing press handler again. The current base forwarding behavior makes duplicate processing easy.

A safer minimal design is a small pending-click candidate in `PetWindow`:

1. `end_pointer_release()` validates the same strict silhouette, duration, and non-drag conditions already used for poke.
2. Instead of immediately calling `register_poke()`, store the candidate position/time.
3. Use the normal Qt double-click interval and a small position tolerance to detect the second click.
4. If the second valid click arrives, cancel the pending single-click poke and fire one surprise reaction.
5. If the interval expires, commit the pending candidate as one ordinary poke.
6. A drag, release outside the silhouette, sleep, menu, or invalid second click cancels the pending candidate without changing annoyance count.

This preserves the existing drag threshold and strict silhouette rules. It delays single-click poke confirmation by one double-click interval, but avoids trying to undo an already-triggered threshold reaction.

The exact native sequence should still be confirmed with a real `QMouseEvent.MouseButtonDblClick` test and a `QTest.mouseDClick()` test before implementation. The current evidence confirms forwarding, not the desired product semantics.

### Petting and drag collision

- A left press enters the pointer/drag path and does not call `hover()` while held, so a normal click/drag cannot simultaneously feed the stroke detector.
- `begin_drag()` resets/block-limits the detector and `end_pointer_release()` returns before poke registration for a drag.
- Button-free petting remains a separate hover path.
- A future surprise handler must not invoke `hover()` or `register_poke()` as a side effect.
- Right-click and transparent-area clicks should remain excluded by the existing button and strict silhouette checks.

### Persistent NEGATIVE policy

Three policies are possible:

1. Suppress surprise while `idle_kind == 'negative'`. This is the safest minimal policy.
2. Show surprise temporarily and restore persistent NEGATIVE afterward. This requires careful preservation of the existing negative slot and is not naturally supported by the current single-slot state.
3. Clear NEGATIVE through double-click. This violates the intended “successful petting directly recovers NEGATIVE” invariant and is not recommended.

Recommendation: suppress surprise during persistent NEGATIVE initially. Do not call `cancel_idle_reaction(include_negative=True)` from surprise handling.

### Minimal lifecycle integration

If surprise is implemented through the existing transient idle slot, use a new transient kind such as `surprise`, but also update the explicit expiration and autonomous-idle blocking paths in `tick()`. `IdleScheduler` itself should not autonomously schedule surprise. The current scheduler has special behavior for `negative` and treats any other active kind generically, so simply adding a string without an explicit blocking/expiry path is unsafe.

The normal lifecycle should be:

```text
valid double-click
→ cancel/replace only an existing transient ambient or poke state
→ capture the prior expression/mood
→ select profile-provided surprise expression
→ hold for configured transient duration
→ cancel and restore the captured state
```

Do not allow surprise to overwrite persistent NEGATIVE or an active positive reaction unless a separate explicit priority policy is added.

### Capability/schema

The natural profile extension is:

```text
expressions.surprise: [{asset, weight?, hold_seconds?}]
behavior.double_click: {interval/tolerance/hold policy}
```

The current expression validator only validates `ambient`, `negative`, and `positive`, so optional `surprise` validation would need to be added. Profiles without the semantic should gracefully ignore/disable the feature. IceGirl already has `惊讶.exp3.json` in runtime inventory, but it is not currently mapped in `profiles/icegirl.json`.

## 2. Greeting motion

### Current menu and motion path

The current path is:

```text
PetCanvas.contextMenuEvent()
→ PetWindow.open_menu()
→ PetWindow.build_menu()
→ QMenu.exec()
```

`build_menu()` already uses the sleep capability pattern:

```text
profile.motion_asset('sleep')
→ canvas.motion_indexes membership check
→ action enabled/disabled
```

`Canvas.initializeGL()` loads every runtime motion into the `ProfileMotions` group and stores an asset-identifier-to-index map. `ModelProfile.motion_asset()` already accepts arbitrary semantic keys. This is sufficient for a character-agnostic greeting capability.

### Recommended profile shape

Add an optional semantic motion mapping, not a filename branch:

```text
motions:
  greeting:
    asset: HuiShou.motion3.json
    priority: ...
    duration_seconds: ...   # optional override if needed
```

The common engine should only ask for `motion_asset('greeting')`, resolve the index, and use profile metadata. IceGirl-specific file names must remain in `profiles/icegirl.json` only.

Profiles without greeting should either omit the action or expose a disabled `인사하기` action. Reusing the existing disabled-sleep style is the most predictable fallback and avoids a menu layout that changes per model.

### Motion completion finding

IceGirl runtime inventory contains:

| Semantic candidate | Runtime asset | Duration | Loop |
|---|---|---:|---|
| greeting candidate | `HuiShou.motion3.json` / `motion_01.motion3.json` | 7 sec | `True` |
| strong-positive candidate | `MeiYan.motion3.json` / `motion_02.motion3.json` | 3.983 sec | `True` |

The native API exposes `StartMotion`, `onFinish`, `IsMotionFinished`, and `StopAllMotions`, but the two target motions are looped. `IsMotionFinished()` or an on-finish callback alone cannot be assumed to terminate these actions. A configured/runtime duration deadline is required, followed by stopping the motion owned by the action.

The current API only exposes broad `StopAllMotions()`, so simultaneous user-triggered motion ownership should be disallowed initially. A new action should not start while sleep is active, and sleep/wake must clear any owner/deadline state if a future action is interrupted.

### Menu and state policy

`open_menu()` currently:

- sets `menu_active=True`,
- interrupts relocation,
- cancels positive reaction,
- cancels transient idle reaction,
- preserves persistent NEGATIVE because `cancel_idle_reaction()` defaults to excluding it.

Greeting should preserve that policy. It should not clear persistent NEGATIVE. Since menu actions are triggered while `QMenu.exec()` is still active, a greeting callback that rejects `menu_active` will silently do nothing. The implementation should either schedule the motion with `QTimer.singleShot(0, ...)` after the menu closes or provide a menu-specific start path.

While greeting plays, the owner should block autonomous idle and relocation, and define whether manual drag/petting/poke are ignored or cancel the greeting. The smallest safe policy is to ignore new reactions and allow drag only after the motion deadline; do not let relocation run concurrently.

After the deadline, stop the owned motion and leave the restored expression/mood in place. Do not force neutral by calling `wake()` or `select_expression(None)`.

## 3. Strong positive / rare petting reward

### Current petting lifecycle

`StrokeDetector.sample()` identifies the successful stroke. `PetWindow.start_reaction()` then:

1. resets annoyance,
2. detects whether persistent NEGATIVE is being recovered,
3. removes NEGATIVE without restoring it first when applicable,
4. stores the previous expression,
5. chooses one current positive candidate by weight,
6. applies parameter values or a native expression,
7. fades the positive reaction in/out using `reaction_level` and `reaction_until`,
8. restores the stored previous state through `cancel_reaction()`.

The current IceGirl positive candidate has parameter values for blush, heart-eyes, smiling eyes, and head pose. Because `loaded()` sets `expression` to `None` when parameter values are present, the native `脸红` asset is not the primary positive layer in this path; the absolute parameter layer is.

### Minimal strong-positive design

Use the existing positive candidate list and weights instead of adding a second progression FSM. An optional positive candidate can carry a semantic motion reference:

```text
expressions.positive:
  - name: blush_heart_smile
    weight: 9
    parameter_values: ...
  - name: rare_strong_positive
    weight: 1
    motion: strong_positive
    parameter_values: ...

motions:
  strong_positive:
    asset: MeiYan.motion3.json
    priority: ...
    duration_seconds: ...
```

This makes rarity profile-driven. If a future product requirement needs a condition rather than a rare weight, add one small candidate-level condition such as a minimum successful-petting count or probability; do not introduce a general rule engine.

`ModelProfile._validate_assets()` currently validates the expression asset and parameter semantic keys but does not validate an optional motion field. That validation and the `loaded()` candidate normalization would be the minimum schema changes.

### Motion/expression interaction

`MeiYan` curves include `ParamAngleZ` and `ParamEyeROpen`, which overlap with current IceGirl positive values. It also touches `ParamMouthForm`, `ParamMouthOpenY`, and `Param60`. Since `apply_behavior()` runs before native `model.Update()`, motion values may win on overlapping parameters during playback. Blush and heart-eyes parameters appear non-overlapping and should remain available, but this must be confirmed visually/runtime because the renderer owns final parameter blending.

The strong motion should be part of the same positive reaction record, not a separate idle reaction. Its recommended lifecycle is:

```text
successful petting
→ choose ordinary or rare positive candidate
→ if candidate has motion capability, start owned motion once
→ keep existing positive parameter layer active
→ stop the looped motion at its deadline
→ allow ordinary positive fade/hold to finish
→ cancel positive and restore the captured baseline
```

For persistent NEGATIVE recovery, the captured baseline must be the expression beneath NEGATIVE. The flow must remain:

```text
persistent NEGATIVE
→ successful petting
→ remove NEGATIVE without displaying neutral first
→ start ordinary/strong POSITIVE
```

The motion cleanup must not call `wake()`, restore NEGATIVE, or independently clear the positive reaction.

## Common engine compatibility

All three features can be implemented without model-specific common-engine branches.

Recommended semantic capability surface:

- `expression_candidates('surprise')`
- `motion_asset('greeting')`
- `motion_asset('strong_positive')`
- optional candidate metadata for duration/priority and positive motion selection

The common engine must not contain:

- `if profile == 'icegirl'`
- `HuiShou.motion3.json` literals outside profile data
- `MeiYan.motion3.json` literals outside profile data
- `惊讶`, `疑惑`, `脸黑`, or other character-specific expression names outside profile data/tests

Profiles without the capability must remain valid and preserve existing Hibana/Tsubaki behavior. The current profile loader already permits arbitrary motion semantic keys and optional motion mappings; the missing pieces are feature-specific validation and lifecycle use.

## Shared invariants and regression risks

1. **Do not clear persistent NEGATIVE by timeout or by surprise/greeting/MeiYan cleanup.** Sleep/wake is the existing explicit exception and remains a separate policy decision.
2. **Successful petting must remain the only direct NEGATIVE → POSITIVE path.** Greeting and surprise must never call the negative-inclusive cancellation path.
3. **Keep one reaction owner at a time.** Existing positive and idle reactions use single slots; avoid nested surprise/poke/positive state without explicit capture/restore.
4. **Block autonomous idle and relocation during user-triggered motion/reaction.** Current `tick()` blocked conditions do not know a future motion owner automatically.
5. **Do not use `StopAllMotions()` casually.** It also stops sleep or any other active motion. User-triggered motions should be mutually exclusive or have an explicit owner.
6. **Do not treat the QTest hover failure as proof of production failure.** It is a current verification limitation; native input coverage remains required.
7. **Keep the planned fixes separate.** B-2, NEW-2, and NEW-4 are known work items; NEW-1 is intentionally deferred. This recon does not modify or redesign them.

## Required tests

### Double-click and poke arbitration

- Native Qt double-click event path and `QTest.mouseDClick()` both produce one surprise semantic action.
- The same double-click does not increment annoyance once or twice.
- A normal single click still becomes one poke after the pending-click interval.
- Counts 1/2/4/6/8 and timeout behavior remain unchanged for IceGirl.
- Drag beyond the existing threshold produces neither poke nor surprise.
- Release outside strict silhouette cancels the click candidate.
- Button-free petting does not create a double-click candidate.
- Surprise is transient and restores the captured non-negative state.
- Persistent NEGATIVE policy is explicitly tested: recommended initial result is no surprise and no state change.
- Right-click and transparent-area click remain excluded.

### Greeting

- IceGirl exposes an enabled `인사하기` action when `greeting` is mapped.
- Hibana/Tsubaki without the mapping do not crash; action is disabled or omitted according to the chosen UI policy.
- The action resolves through semantic profile mapping, not an IceGirl branch.
- Menu callback starts after `menu_active` is cleared or deliberately supports the menu context.
- HuiShou stops at a configured/runtime duration despite `Loop=True`.
- Relocation and autonomous idle do not run during greeting.
- Greeting does not clear persistent NEGATIVE.
- Sleep/wake interruption leaves no stale motion-owner state.

### Strong positive

- Ordinary positive behavior is unchanged when no strong candidate exists.
- Rare candidate selection is bounded and does not create a second progression counter unless explicitly configured.
- MeiYan starts once per positive reaction and ends by deadline.
- Blush/heart-eyes remain active while compatible with MeiYan.
- Overlapping eye/head parameters are visually checked.
- Positive fade restores the pre-positive state.
- Persistent NEGATIVE recovers directly to ordinary or strong POSITIVE without a neutral frame.
- Hibana/Tsubaki regression remains green.

Current tests cover profile parsing, optional capabilities, `StrokeDetector`, scheduler, relocation, and existing verifier flows. They do not currently cover double-click semantic classification, greeting menu actions, looped user motions, or strong-positive selection/lifecycle.

## Recommended implementation order

The following order assumes the separately planned B-2 and NEW-2 fixes land first. NEW-1 remains intentionally deferred, and NEW-4 is a verification task rather than a feature prerequisite.

1. **Shared capability/lifecycle preparation**
   - Decide the semantic keys and duration ownership.
   - Add only the small generic motion-start/owner path needed by user-triggered motions.
   - Extend profile validation for optional surprise and candidate motion metadata.
2. **Greeting**
   - It is isolated from click arbitration and validates menu timing, capability fallback, looped motion deadlines, and relocation blocking.
3. **Double-click surprise**
   - Add pending-click arbitration around the existing release-based poke path.
   - Reuse strict silhouette and drag thresholds.
   - Make persistent NEGATIVE behavior explicit before coding.
4. **Strong positive / MeiYan**
   - It has the greatest interaction with positive parameter blending and NEGATIVE recovery, so implement it after the generic motion lifecycle is proven.

## Minimum implementation plan for Codex/Sol

1. Re-open `desktop_pet.py`, `app.py`, and `model_profiles.py`; do not rely on this snapshot alone.
2. Land or verify the separately owned B-2 and NEW-2 fixes before layering new reaction states.
3. Add a generic profile-semantic motion lookup/start helper, with a single owner and a duration deadline; do not add an FSM or event bus.
4. Extend profile validation only for optional `surprise`, positive-candidate motion references, and motion timing metadata.
5. Add `greeting` to the menu through the same capability check used by sleep. Defer action start until the menu is no longer active.
6. Add a pending single-click arbiter. Do not undo already-fired poke reactions after recognizing a double-click.
7. Reuse the existing transient idle slot for surprise only if `tick()` explicitly handles its expiry and autonomous scheduler blocking. Suppress surprise during persistent NEGATIVE in the first implementation.
8. Represent strong positive as an optional weighted positive candidate with a semantic motion reference. Keep motion cleanup inside the existing positive lifecycle.
9. Add focused unit/profile tests first, then native Qt tests for double-click/menu/motion timing, then run the existing Hibana/Tsubaki/IceGirl regression suite.
10. Keep the known `research._v3cpp` discovery failure separate from feature acceptance unless the test command is intentionally changed.

## Files modified during this reconnaissance

Exactly one file was created/modified:

- `docs/agent_handoff/NEXT_FEATURE_RECON.md`

No application source, profile, test, probe, model/runtime asset, Git setting, or README was modified.
