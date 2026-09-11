# Current Project Status

> This document is a snapshot of the project at inspection time. Before modifying code, verify important claims against the current source.

- Supported models:
  - `hibana`
  - `tsubaki`
  - `icegirl`

- Current feature in progress: IceGirl poke / annoyance progression

- Current state: implementation mostly complete; verification and finishing stage

- Read first:
  - `docs/agent_handoff/POKE_HANDOFF.md`
  - `docs/agent_handoff/BUG_AUDIT.md`

- Deferred examples:
  - sleep/wake versus persistent mood policy
  - QTest non-button hover reliability
  - giant character size / taskbar flicker validation
  - lower-priority items listed in `BUG_AUDIT.md`

- Recommended next task: finish poke-specific tests and native-input verification, then run the hibana/tsubaki regression suite again.
