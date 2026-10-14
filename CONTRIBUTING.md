# Contributing

This is an experimental integration. Start with [README.md](README.md), [CONTROLS.md](CONTROLS.md) and [docs/TESTING.md](docs/TESTING.md).

## Code layout

| Location | Responsibility |
| --- | --- |
| `src/` | Native TCP host, validation, avatar reference and grind rails |
| `mod/` | MelonLoader adapter, controls, collision, retargeting, camera and diagnostics |
| `scripts/` | Setup, local export, installation/removal, launcher and harnesses |
| `tools/` | IL2CPP references and C# headless checks |
| `patches/` | Changes to the pinned upstream engine |
| `vendor/`, `data/`, `target/` | Dependencies, private game data and build output; ignored by Git |

Keep fixes focused on a demonstrated failure. Preserve request/collision validation and installer ownership checks. Native/session changes need a fresh host; adapter changes need rebuilding and a normal game restart.

Upstream modifications belong in `patches/skate-core-floor-seams.patch`, including attribution/change notices. Do not commit a whole vendor checkout or silently move pinned revisions. Run `scripts/test_setup.py` after updating the patch; an older applied patch may need manual review.

## Bug reports

Include adapter/game versions, OS/Proton version, input method, steps, location if relevant and exact error text. Say whether it returns to first person, leaves the skater on foot, freezes or falls through collision. Report mouse feel separately from physics/frame-time issues.

Start with a short reviewed text excerpt. Logs may include local paths. Do not attach `.skb` worlds, `.mesh` caches, game DLLs, PS3 data, saves or private exports. Reproduction using your own data may be necessary; this project does not host commercial content.

## Pull requests

Explain the trigger, resulting behavior, relevant checks and rendered limitations. Add a meaningful regression for new input/geometry/lifecycle logic. Transport success with a different simulation path is not an exact reproduction.

Keep generated references, downloaded tools, build products, personal handoffs and captures out of commits. Retain Apache-2.0 licensing and upstream notices.
