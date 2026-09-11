# Current Project Status

> Snapshot updated 2026-09-12. Current source is authoritative.

- Supported profiles: `hibana` (스파키), `tsubaki` (카멜리아), `icegirl` (슈아).
- 슈아 has profile-driven chest poke and head-petting reconciliation.
- Chest poke progression is `0 → 疑惑 → 生气 → 脸黑`, capped at `脸黑`.
- Head petting reconciliation is `脸黑 → 生气 → 舌头 → 脸红 → petting response → 爱心眼`.
- `生气`, `舌头`, `脸红`, and the petting response persist until the next valid head petting. Only the fifth reconciliation petting starts the timed `爱心眼` completion.
- Ambient/major-idle `生气` remains transient and does not mutate interaction anger.
- The common engine contains no profile ID or model-specific expression branch.
- Large restored sizes use visual bounds when a full framebuffer silhouette is intentionally skipped, keeping chest interaction available.

See `POKE_HANDOFF.md` for schema and verification details.

Verification: 24 explicit unit tests, the IceGirl poke probe, three-profile petting and idle probes, three-profile full pet verifier, and three-profile runtime verifier pass. Broad unittest discovery retains the unrelated missing `research._v3cpp` collection error.
