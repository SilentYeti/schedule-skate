# Skate Schedule controls

This layout is provided by adapter **0.1.21**. Physical mouse feel and Schedule I key overlaps still need wider in-game testing.

The starting point is [Skate3Recomp's documented keyboard layout](https://github.com/mchughalex/skate3recomp/blob/f6e0ae87fdfecbadb5c1e36c55d66a744187a3cd/README.md#keyboard-keybinds), checked on 2026-10-13. Its WASD, face-button keys, mouse triggers and bumper keys provide a complete virtual controller. This mod adds the user's requested Alt mouse modifier, arrow alternatives, half-stick input and F7 device selection. It does not import that project's game or input implementation.

Skate 3 mode starts enabled and connects automatically when you mount a Schedule I skateboard. **F8** restores Schedule I control; press it again to re-enable automatic connection. Native Skate 3 on-foot mode still runs inside the mounted Schedule I session; **F** switches the native skater between riding and walking, while F8 exits the integration. Normal connection/waiting/recovery states have no popup; only integration failures do.

| Key/input | Skate 3 action / virtual controller control |
| --- | --- |
| F8 | Enable/disable Skate 3 mode; enabled on startup |
| F7 while connected | Toggle forced keyboard/mouse versus controller preference with keyboard fallback |
| WASD | Left stick: steer or walk |
| Space | A: push while riding; sprint on foot |
| E | X: push with the other foot; jump on foot |
| C | B: brake |
| F | Y: get on/off the native skateboard |
| Hold left/right Alt + move mouse | Analog right stick: charge and flick tricks |
| Arrow keys | Digital right-stick alternative |
| Hold Ctrl + arrow key/mouse | Half right-stick deflection for manuals/precision |
| Right mouse / Z | Left trigger: left-hand grab / native off-board trigger actions |
| Left mouse / X | Right trigger: right-hand grab / native off-board trigger actions |
| Q / R | Left/right bumper |
| Shift | Left-stick click |
| Middle mouse | Right-stick click |
| I / K / J / L | D-pad up/down/left/right |
| Tab / Enter | Back/Start buttons forwarded to the engine |
| Escape | Schedule I pause/menu |

Back/Start forwarding does not implement Skate 3's console menus. Schedule I continues to own menus and equipment; overlapping game actions still need a rendered test.

## Mouse tricks

Hold Alt, drag downward to charge, then move upward to pop. An upward diagonal flick can produce a flip trick. The mouse moves a virtual stick position: stopping the mouse retains the charge, and releasing Alt centres it. The initial Alt frame ignores previous camera movement. Arrow keys take priority when held.

The virtual stick is capped to its circular range. Inputs are sampled every rendered frame even while a bridge reply is pending; short face-button taps are retained until the next request. This does not provide a complete timestamped input queue or guarantee every extremely fast flick under a frame stall is retained.

## Mouse calibration and controllers

With the game closed, set `MouseStickSensitivity = 0.08` under `[SkateSchedule]` in `<game>/UserData/MelonPreferences.cfg`; add that category if it has not been saved yet. Increase the value if full stick travel takes too much mouse movement, or decrease it if it feels twitchy, then restart. Values are clamped to 0.001–1; nonfinite values use the default. This scales Unity's configured mouse delta, so physical mouse feel still needs calibration in the rendered game.

Controllers retain their existing XInput mapping. By default, the first connected pad takes priority; F7 forces keyboard/mouse even with an idle pad connected. The F7 selection lasts for this game process and survives F8 toggles; it resets on a new launch. Menus, F8 exit, scene unload and connection failure clear pending keyboard gestures without taking ownership of cursor/mouse-look settings.

## Verification

`tools/ControllerChecks` executes the actual C# input mapper with controlled Unity inputs and an XInput fixture. It checks full button/trigger mapping, short taps, held mouse charge, diagonal reversal, Alt release, reset, half-stick input, invalid sensitivity and forced keyboard selection with a connected pad. `scripts/test_bridge.py` forwards its real mouse-charge/flick messages to the native engine and verifies a kickflip that returns to PhysicsGround. All six socket tests pass; the mod builds with zero warnings/errors.

Physical mouse feel, Schedule I key overlaps, F7 notices and menu/dismount behavior still require an in-game retest. The engine and collision changes from 0.1.19 are unchanged.
