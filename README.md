# Live2D Companion Engine

An experimental Windows desktop companion engine that lets Live2D characters live, move, react, and interact directly on the desktop.

Rather than treating Live2D models merely as static assets inside a viewer or transparent overlay, this project explores turning them into interactive desktop companions. The goal is to give characters a believable, responsive presence on your screen—moving autonomously, reacting to direct user input, expressing shifting emotions, and sustaining a stateful relationship with the desktop environment.

This is an active personal project focused on interaction mechanics, behavior design, and engine architecture.

---

## What It Does

The engine currently implements an interactive runtime loop designed specifically for continuous desktop companionship:

- **Transparent Borderless Window**: Renders characters with a fully transparent OpenGL background and clean alpha blending without taskbar clutter.
- **Non-Intrusive Always-on-Top**: Keeps the companion visible above standard windows while letting mouse clicks outside the character's active silhouette pass through to underlying applications.
- **Drag & Positioning**: Allows effortless dragging with the left mouse button anywhere on the character body.
- **Autonomous Relocation**: Periodically moves across the desktop after extended idle intervals, fading out smoothly and fading back in at distant screen coordinates.
- **Natural Petting Interaction**: Senses reciprocal, continuous mouse movement over the character's head region, triggering character-specific positive reactions mapped in each profile.
- **Emotional States & Reactions**: Transitions between neutral, positive (happy/blushing), and negative (pouting/annoyed) emotional states based on user interaction frequency and type.
- **Ambient & Major Idle Behavior**: Plays subtle ambient expressions periodically to keep the character lively, transitioning to persistent major idle states (such as sulking or sleeping) if left untouched.
- **State Persistence Across Launches**: Automatically saves window position, configured size, and active character choice, restoring them seamlessly on restart.
- **Flexible Sizing & Movement Bounds**: Supports a wide scaling range with cached silhouette bounds, allowing characters to sit comfortably along screen edges without artificial boundary clipping.
- **Multi-Model Support via Profiles**: Seamlessly switches between different Live2D characters through a right-click context menu or CLI parameters.

*(Note: Advanced capabilities such as LLM-driven dialogue, voice synthesis, minigames, and desktop inventory are future concepts and not part of the current build.)*

---

## Extensible Character Profiles

The project initially began with a single character, but hardcoding model-specific assumptions quickly revealed clear scalability limits. Live2D models differ widely in parameter naming conventions, canvas aspect ratios, hit area availability, texture allocations, and included expressions or motions.

To address this, the engine was refactored around a **profile-driven architecture** (`profiles/*.json`). The common engine remains strictly character-agnostic, while individual profiles declare model-specific semantics:

- **Runtime Paths & Asset References**: Maps local runtime configurations and model descriptors.
- **Semantic Parameter Mappings**: Links abstract behaviors (eye blinks, head rotation, breathing, mouth opening, blush) to specific Live2D parameter IDs.
- **Reactions & Expression Logic**: Categorizes expressions into ambient, positive, negative, and sleep states.
- **Physical Presentation**: Defines scaling, window aspect ratios, vertical headroom, and screen-bottom offscreen margins.
- **Hit Areas & Interaction Zones**: Specifies head interaction bounding boxes in model space or normalized coordinates.
- **Graceful Degradation**: If a model lacks certain assets (such as sleep motions or pre-authored hit areas), the engine automatically adapts behavior without errors or hardcoded engine branches.

### Validated Across Three Distinct Models

The profile architecture has been empirically validated with three structurally different Live2D models:

1. **Hibana (火花)**: The initial prototype model featuring a full suite of expressions, sleep animations, and comprehensive parameter setups.
2. **Tsubaki (椿)**: An onboarded model lacking dedicated sleep motions and using non-standard file encodings. The profile successfully maps custom hit regions and degrades gracefully to persistent negative idle states when sleep is unavailable.
3. **IceGirl**: A complex model with multiple high-resolution 8K/4K textures and a collection of 20 expressions. The profile handles downscaled runtime textures and selects weighted semantic reactions.

Adding Tsubaki and IceGirl after the profile refactoring served as a practical stress test of the architecture, demonstrating that new characters could be prepared and onboarded without introducing model-specific branches or special cases into the common engine.

*(Live2D model assets themselves are not distributed in this repository.)*

---

## Development So Far

The project's architectural trajectory evolved through iterative runtime milestones:

### 2026-09-11
- Integrated the initial Live2D character (Hibana) into a transparent desktop runtime.
- Established core companion loops: transparent OpenGL rendering, basic dragging, and always-on-top positioning.
- Implemented the first petting interaction mechanics, allowing the character to recognize continuous strokes over the head.

### 2026-09-12
- Expanded character movement boundaries across the desktop based on silhouette detection.
- Added dynamic character sizing support with high-resolution silhouette scaling.
- Refactored the architecture around extensible character profiles (`profiles/*.json`), decoupling model specifics from the engine.
- Onboarded two structurally different Live2D characters (Tsubaki and IceGirl) without introducing model-specific branches into the common engine.
- Ran regression checks across existing characters to verify behavior consistency while extending the system.

---

## Development Philosophy

- **Interactivity Over Passive Display**: An authentic companion requires tactile feedback, believable timing, and emotional responsiveness—not just an idle animation loop.
- **Expressions as Reactions**: Expressions and motions should correspond to real interaction states (petted, dragged, left idle, relocated), not isolated button presses.
- **Engine Agnosticism**: Character-specific quirks, parameter IDs, and geometry belong in configuration profiles, never in common engine `if/else` conditionals.
- **Runtime & Visual Verification**: Features are verified through a combination of automated probes and manual runtime observation on physical hardware.
- **Extensibility Through Architecture**: Adding a new character should test and reinforce existing abstractions rather than weaken them with special cases.
- **Regression Awareness**: Regression checks are run when shared systems change to protect existing character behavior.

---

## AI-Assisted Development

Live2D Companion Engine is a personal project conceived, designed, and directed by **김응재 (Eungjae Kim)**.

- **Human Direction**: 김응재 defines the project concept, UX and interaction design, behavioral rules, architectural structure, model onboarding strategy, acceptance criteria, and hands-on iteration based on actual desktop experience.
- **Agent Assistance**: AI coding agents—including OpenAI Codex and other AI-assisted programming tools—are utilized extensively for implementation, codebase inspection, debugging, automated testing, Live2D model parameter analysis, refactoring, and independent regression verification.
- **Iterative Methodology**: Development proceeds through structured cycles of:
  `Plan` → `Implement` → `Execute & Observe` → `Diagnose` → `Refactor` → `Verify`

AI agents are treated as development tools within an iterative, human-directed workflow rather than as a one-shot project generator.

---

## Getting Started

### Prerequisites
- Windows 10/11 x64
- Python 3.12 (compatible with `live2d-py` wheels)
- Dedicated or integrated GPU with OpenGL 3.3+ support

### Installation
1. Clone the repository:
   ```bash
   git clone https://github.com/HighKcal/Live2D_Companion_Engine.git
   cd Live2D_Companion_Engine
   ```
2. Create and activate a virtual environment, then install dependencies:
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Prepare a compatible Live2D model (see [Model Assets](#model-assets--disclaimer) below) or inspect existing profile definitions in `profiles/`.

### Running the Companion
```powershell
# Launch default companion (or last saved profile)
python -X utf8 app.py

# Launch with a specific profile
python -X utf8 app.py --profile hibana

# Launch the developer inspection lab
python -X utf8 app.py --lab
```

---

## Model Assets & Disclaimer

- **No Model Assets Included**: Live2D character model files (`.moc3`, `.model3.json`, textures, motions, expressions) are **intentionally excluded** from this repository.
- **User Responsibility**: Users must provide compatible Live2D model assets independently.
- **Copyright & Licensing**: All character models remain the exclusive intellectual property and copyright of their respective original creators and are governed by their original licenses.
- **Repository Scope**: This repository distributes only the engine source code, profile schemas, preparation tooling, test harnesses, and technical documentation.
