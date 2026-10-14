import importlib.util
from pathlib import Path
import struct
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("export_ps3", Path(__file__).with_name("export_ps3.py"))
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


class ExportTests(unittest.TestCase):
    def test_ps3_controller_mapping_and_invalid_remaps(self):
        # Synthetic controller schema, independent of private retail exports.
        names=("Select","L3","R3","Start","DPadU","DPadR","DPadD","DPadL","L2","R2","L1","R1",
               "Triangle","Circle","Cross","Square","RStickL","RStickR","RStickU","RStickD",
               "LStickL","LStickR","LStickU","LStickD","SensorX","SensorY","SensorZ","SensorG")
        original="\n".join(f"{name} = Button{i};" for i,name in enumerate(names))+"\n"+"""
GP_LStickX = garg0; GP_LStickY = garg1; GP_LStickIn = L3;
GP_RStickX = RStickR-RStickL; GP_RStickY = RStickU-RStickD; GP_RStickIn = R3;
GP_LTrigger = L2; GP_RTrigger = R2; GP_LBumper = L1; GP_RBumper = R1;
GP_UDPad = DPadU; GP_DDPad = DPadD; GP_LDPad = DPadL; GP_RDPad = DPadR;
GP_XFace = Square; GP_YFace = Triangle; GP_AFace = Cross; GP_BFace = Circle;
"""
        mapped = exporter.bridge_input(original)
        self.assertIn("GP_AFace = A;", mapped)
        self.assertIn("GP_LStickX = LStickR-LStickL;", mapped)
        self.assertIn("GP_RTrigger = RTrigger;", mapped)
        for changed in (
            original.replace("Cross = Button14", "Cross = Button13"),
            original + "\nGP_AFace = Cross;",
            original.replace("GP_AFace", "MISSING_AFace"),
        ):
            with self.assertRaises(ValueError):
                exporter.bridge_input(changed)

    def test_headless_scene_is_a_valid_glb_json_chunk(self):
        import json
        blob = exporter.placeholder_scene()
        magic, version, total, length, kind = struct.unpack_from("<4sIII4s", blob)
        self.assertEqual((magic, version, total, kind), (b"glTF", 2, len(blob), b"JSON"))
        self.assertEqual(len(blob) - 20, length)
        self.assertEqual(json.loads(blob[20:])["asset"]["version"], "2.0")

    def test_invalid_game_cannot_create_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                exporter.export(root, root / "output")
            self.assertFalse((root / "output").exists())

    def test_archive_paths_cannot_escape_output(self):
        for path in ("../save", "/home/file", "data/../../save", "C:/save"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                exporter.BigArchive.safe_relative(path)

    def test_output_overlap_detection(self):
        root = Path("/tmp/example")
        self.assertTrue(exporter.overlaps(root, root / "data"))
        self.assertTrue(exporter.overlaps(root / "data", root))
        self.assertFalse(exporter.overlaps(root, Path("/tmp/other")))


if __name__ == "__main__":
    unittest.main()
