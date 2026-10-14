# Testing and headless diagnostics

Run from the repository root. Tests do not prove rendered smoothness, complete avatar skinning or all Schedule I lifecycle behavior.

## Source checks

After `python3 scripts/setup_dev.py`, these checks need no running game or private game exports:

```bash
python3 -B scripts/test_setup.py
python3 -B scripts/test_install.py
vendor/python/bin/python -B scripts/test_export.py
vendor/python/bin/python -B scripts/test_collision.py
vendor/dotnet/dotnet build tools/GeometryChecks/GeometryChecks.csproj
vendor/dotnet/dotnet tools/GeometryChecks/bin/Debug/net10.0/GeometryChecks.dll
cargo test --locked
cargo test --locked -p skate-core player::offboard::ground_motion
```

`test_setup.py` checks application, repeated application and refusal of conflicting vendor patches in a temporary checkout. Expected patch-conflict messages can appear while it passes. `test_install.py` uses temporary fake game/loader files. The PS3 input fixture is synthetic. The real collision-cache test is skipped until you export your own cache; other collision tests use synthetic shapes.

## Native integration tests

Build the native executable and export your own PS3 data as described in the README:

```bash
cargo build --locked
target/debug/skate-schedule --assets data/ps3-assets --smoke-test
python3 -B scripts/test_bridge.py
python3 -B scripts/headless.py
python3 -B scripts/riding_checks.py --out data/riding-report.json
```

These tools start and stop their own isolated host on an unused loopback port. They do not launch Schedule I or modify saves. Avoid them during a rendered frame-time comparison: their CPU load distorts measurements.

The socket suite builds the actual C# mapper with controlled Unity-input/XInput fixtures, checks generated packets and verifies a mouse charge/flick produces a kickflip and lands. Riding checks cover walking, jumps, landed/failed flips, ragdoll/teleport recovery, collision streaming and slopes. Headless checks measure camera aim and continued responses during dense collision rebuilding. Timing includes client work and is not a Unity frame rate or pure server benchmark.

## Capturing a reported failure

After automatic connection on mounting a board, `<game>/SkateScheduleDiagnostics/` records `runtime.jsonl` and packed `.skb` worlds. Preserve that directory, `data/logs/bridge.log` and `<game>/MelonLoader/Latest.log` **before another launch**. The trace caps at 40,000 records; geometry retention keeps the newest 64 worlds. `latest-failure/` retains only eight, which may be insufficient for a full replay.

Examples using your private capture paths:

```bash
python3 -B scripts/runtime_checks.py /path/to/private-capture/runtime.jsonl \
  --out data/runtime-report.json
python3 -B scripts/headless.py --replay /path/to/private-capture/activation.skb \
  --out data/activation-report.json
python3 -B scripts/replay_runtime.py --trace /path/to/private-capture/runtime.jsonl \
  --out data/replay-report.json
```

Activation replay restores the world/spawn, not the original inputs. Full replay uses recorded packets/available geometry; collision installation ticks and complete skeleton/contact history are not captured, so paths can diverge.

`replay_runtime.py --rejected-request /path/to/rejected-request.json` appends a frozen on-foot launch after a full session replay. It requires matching BipedGround context within 0.25 m and cannot be combined with truncated/local probes. A failed fidelity check is not evidence that the original issue is fixed.

`scripts/hill_checks.py` is a maintainer regression for specific private road captures. Those captures and archived baseline executables are **not distributed**; it is not a general fresh-clone test. Local development handoff/history files are excluded too.

## Rendered acceptance

Test automatic startup/mount connection, silent normal states, error popup visibility/expiry, input selection, pushing/turning, flips, failed landings, grinding into on-foot movement/jumping, hills, F8 exits, pauses, dismount/remount and normal quitting. Check mouse look, board physics and avatar restoration on each exit.

For stutters, compare short F8-off/on passes in the same place without competing builds/tests. Preserve exact version/error text. Do not publish game-derived worlds, game assemblies or private exports with bug reports.
