"""Export locally owned PS3 gameplay data for the headless skating bridge."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
import struct
import sys
import tempfile

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "vendor/engine"))
from tools.owned_game.big import BigArchive
from tools.asset_pipeline.vlt import convert as convert_database
from tools.asset_pipeline.physics_skeleton import convert as convert_skeleton


def game_data(game):
    game = game.resolve()
    data = game / "USRDIR/data"
    if not (game / "PARAM.SFO").is_file() or not (game / "USRDIR/EBOOT.BIN").is_file():
        raise ValueError("Select the PS3_GAME directory with PARAM.SFO and USRDIR/EBOOT.BIN.")
    for name in ("miscload.big", "miscboot.big", "db.big"):
        if not (data / "big" / name).is_file():
            raise ValueError(f"Missing PS3 gameplay archive: {name}")
    return data


def overlaps(a, b):
    return a == b or a.is_relative_to(b) or b.is_relative_to(a)


def placeholder_scene():
    # A real, original empty GLB satisfies the engine's scene manifest; the host does no rendering.
    document = {"asset": {"version": "2.0", "generator": "skate-schedule headless"},
                "scene": 0, "scenes": [{"nodes": []}], "nodes": []}
    payload = json.dumps(document).encode()
    payload += b" " * (-len(payload) % 4)
    return struct.pack("<4sIII4s", b"glTF", 2, 20 + len(payload), len(payload), b"JSON") + payload


def bridge_input(source):
    # The bridge sends Xbox-style packets; validate PS3 meanings before adapting its config.
    clean = "\n".join(line.split("//", 1)[0] for line in source.splitlines())
    definitions = {}
    for statement in clean.split(";"):
        if "=" not in statement:
            continue
        name, expression = statement.split("=", 1)
        name = name.strip()
        if name in definitions:
            raise ValueError(f"Duplicate input definition: {name}")
        definitions[name] = "".join(expression.split())
    physical = ("Select", "L3", "R3", "Start", "DPadU", "DPadR", "DPadD", "DPadL",
                "L2", "R2", "L1", "R1", "Triangle", "Circle", "Cross", "Square",
                "RStickL", "RStickR", "RStickU", "RStickD",
                "LStickL", "LStickR", "LStickU", "LStickD", "SensorX", "SensorY", "SensorZ", "SensorG")
    for index, name in enumerate(physical):
        if definitions.get(name) != f"Button{index}":
            raise ValueError(f"Unsupported PS3 controller definition: {name}")
    aliases = {"L3": "LStick", "R3": "RStick", "L2": "LTrigger", "R2": "RTrigger",
               "L1": "LBumper", "R1": "RBumper", "Square": "X", "Triangle": "Y",
               "Cross": "A", "Circle": "B", "garg0": "LStickR-LStickL", "garg1": "LStickU-LStickD"}
    actions = {
        "GP_LStickX": "LStickR-LStickL", "GP_LStickY": "LStickU-LStickD", "GP_LStickIn": "LStick",
        "GP_RStickX": "RStickR-RStickL", "GP_RStickY": "RStickU-RStickD", "GP_RStickIn": "RStick",
        "GP_LTrigger": "LTrigger", "GP_RTrigger": "RTrigger",
        "GP_LBumper": "LBumper", "GP_RBumper": "RBumper",
        "GP_UDPad": "DPadU", "GP_DDPad": "DPadD", "GP_LDPad": "DPadL", "GP_RDPad": "DPadR",
        "GP_XFace": "X", "GP_YFace": "Y", "GP_AFace": "A", "GP_BFace": "B",
    }
    for key, expected in actions.items():
        actual = definitions.get(key)
        if aliases.get(actual, actual) != expected:
            raise ValueError(f"Unsupported PS3 gameplay expression: {key} = {actual}")
    buttons = ("DPadU", "DPadD", "DPadL", "DPadR", "Start", "Back", "LStick", "RStick",
               "LBumper", "RBumper", "LTrigger", "RTrigger", "A", "B", "X", "Y",
               "LStickR", "LStickL", "LStickU", "LStickD", "RStickR", "RStickL", "RStickU", "RStickD")
    return ("// skate-schedule: virtual Xbox-style bridge controls; original PS3 config preserved.\n"
            "// shortcut: SixAxis tilt is not transported; add sensor packets if tilt controls are needed.\n"
            + "".join(f"{name} = Button{i};\n" for i, name in enumerate(buttons))
            + "".join(f"{name} = {expression};\n" for name, expression in actions.items()))


def export(game, destination):
    data = game_data(game)
    destination = destination.resolve()
    if overlaps(game.resolve(), destination) or overlaps(PROJECT / "vendor", destination):
        raise ValueError("Output must be separate from the game and vendor directories.")
    if destination.exists():
        raise FileExistsError(f"Refusing to replace existing output: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".ps3-export-", dir=destination.parent) as temporary:
        stage = Path(temporary) / "assets"
        stock = stage / "private/stock"
        bank_archive = BigArchive(data / "big/miscload.big")
        wanted = [e for e in bank_archive.entries
                  if Path(e.path).suffix.lower() in (".abin", ".stategraph", ".pat", ".shk")]
        print(f"Extracting {len(wanted)} animation banks and state graphs", flush=True)
        bank_archive.extract_entries(wanted, stock)
        boot = BigArchive(data / "big/miscboot.big")
        config = [e for e in boot.entries if e.path.lower() == "data/config/input.cfg"]
        if len(config) != 1:
            raise ValueError("Expected one controller input configuration.")
        boot.extract_entries(config, stock)
        input_file = stock / "data/config/input.cfg"
        source_input = input_file.read_text()
        normalized_input = bridge_input(source_input)
        input_file.with_name("input.ps3-original.cfg").write_text(source_input)
        input_file.write_text(normalized_input)
        loose = data / "anim"
        if loose.is_dir():
            for bank in loose.glob("*.abin"):
                target = stock / "data/anim" / bank.name
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists() and target.read_bytes() != bank.read_bytes():
                    raise ValueError(f"Conflicting archived and loose animation bank: {bank.name}")
                if not target.exists():
                    shutil.copy2(bank, target)
        required = ("OnBoard.abin", "OffBoard.abin")
        for name in required:
            if not (stock / "data/anim" / name).is_file():
                raise ValueError(f"Missing required animation bank: {name}")
        database = BigArchive(data / "big/db.big")
        names = {"skaterschema.bin", "skaterschema.vlt",
                 "skatercollections.bin", "skatercollections.vlt"}
        entries = [e for e in database.entries if Path(e.path).name.lower() in names]
        if len(entries) != len(names):
            raise ValueError("Missing or duplicate gameplay database inputs.")
        database.extract_entries(entries, stock)
        known_names = (PROJECT / "vendor/engine/tools/asset_pipeline/names.txt").read_text().splitlines()
        collections = convert_database(stock / "data/db/skaterschema",
                                       stock / "data/db/skatercollections", known_names)
        skeletons = convert_skeleton(stock / "data/anim/OnBoard.abin")
        (stock / "skater-collections.json").write_text(json.dumps(collections))
        (stock / "physics-skeletons.json").write_text(json.dumps(skeletons))
        (stage / "private/headless.glb").write_bytes(placeholder_scene())
        manifest = {"version": 1, "character_scene": "private/headless.glb",
                    "initial_animation": "R_IDLE_HCOM_000",
                    "action_graph": "private/stock/data/state/ActionGraph_OnBoard.stategraph",
                    "motion_graph": "private/stock/data/state/MotionGraph_OnBoard.stategraph"}
        (stage / "private/game.json").write_text(json.dumps(manifest))
        report = {"source": str(game.resolve()), "platform": "PS3",
                  "collections": len(collections["collections"]),
                  "physics_skeletons": len(skeletons["skeletons"]),
                  "banks": {name: hashlib.sha256((stock / "data/anim" / name).read_bytes()).hexdigest()
                            for name in required},
                  "graphics": "Original empty scene; PS3 graphics are not converted."}
        (stage / "export-report.json").write_text(json.dumps(report, indent=2) + "\n")
        stage.rename(destination)
    print(json.dumps(report, indent=2))
    print(f"Assets ready: {destination}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game", type=Path, required=True)
    parser.add_argument("--out", type=Path, default=PROJECT / "data/ps3-assets")
    args = parser.parse_args()
    try:
        export(args.game, args.out)
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Export failed: {error}\n")


if __name__ == "__main__":
    main()
