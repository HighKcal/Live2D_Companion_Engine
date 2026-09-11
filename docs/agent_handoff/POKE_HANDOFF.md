# Poke / Reconciliation Handoff

> Snapshot updated 2026-09-12. Current source is authoritative.

## Input arbitration

A poke requires a short left press/release inside both the profile's model-space `interaction_region` and the current silhouette. Drag, head click, right click, transparent pixels, and other body areas are excluded. 슈아 uses `poke_chest = [-0.1, 0.18, 0.1, 0.34]`.

At large restored sizes, the full framebuffer read is intentionally skipped. Until a strict alpha silhouette exists, hit validation uses the same saved/computed visual-bounds fallback as the window input mask.

## Profile schema and behavior

`behavior.poke.levels` defines the anger progression. `behavior.poke.reconciliation` defines ordered persistent recovery steps with semantic `role`, `asset`, `remaining_level`, and optional semantic `parameter_values`. `behavior.poke.completion` defines the final timed response. The engine only interprets this profile data; expression names stay in the profile.

슈아 mapping:

| Input | Result | Lifetime |
|---|---|---|
| chest poke 1 | `疑惑` | until interaction |
| chest poke 2 | `生气` | until interaction |
| chest poke 3+ | `脸黑` | persistent NEGATIVE |
| pet `脸黑` | `生气` | until petting |
| pet `生气` | `舌头` | until petting |
| pet `舌头` | `脸红` | until petting |
| pet `脸红` | production petting parameters: closed smiling eyes, blush, head tilt; no heart eyes | until petting |
| pet petting response | `爱心眼` | 3–4 seconds, then original baseline |

There is no inactivity reset. Intermediate reconciliation never starts the generic positive response. Each persistent owner retains the original pre-anger baseline, and the final completion restores that baseline without exposing a neutral frame or reviving an older owner.

Random idle negative remains a separate transient `生气`; it never changes `annoyance_count` or reconciliation state. Profiles without `behavior.poke` continue without poke capability.

## Diagnostics

`app.py --debug-poke` shows the projected chest rectangle and logs press/release region hits, drag classification, registration, chosen expression, and lifecycle ownership. It is disabled during normal `run.cmd` use.

## Verification record

- Explicit unit suite: 24 passed (`test_prepare_model.py`, `test_model_profiles.py`, `test_pet_behavior.py`).
- IceGirl OpenGL poke probe: passed, including rapid three-click progression, level-3 cap, inactivity persistence, five reconciliation pettings, persistent parameter response without heart eyes, final `爱心眼`, stale-owner restoration, input exclusions, ambient-negative separation, and relocation blocking.
- Petting probe: passed for hibana, tsubaki, and icegirl. Models without sleep motions return `False` and keep normal state.
- Idle probe: passed for all three profiles. Persistent major negative blocks relocation; IceGirl transient idle `生气` expires without changing annoyance.
- Full desktop-pet verifier: passed for all three profiles.
- Live2D runtime verifier: passed for hibana (622 frames), tsubaki (273 frames), and icegirl (646 frames). The verifier now reads physics metadata and does not demand exact manual min/max values from physics-owned outputs.
- Broad unittest discovery still imports the unrelated `research` package and fails on the pre-existing missing `research._v3cpp`; the explicit real test modules pass.

Human testing remains required for the perceived clarity and timing of the complete on-screen sequence.
