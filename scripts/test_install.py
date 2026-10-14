"""Verify that installation/removal preserves existing and externally changed files."""
import contextlib
import importlib.util
import io
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location("install_mod",Path(__file__).with_name("install_mod.py"))
installer=importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)/"project"
        self.game=Path(self.temporary.name)/"game"
        fixtures={
            self.game/"Schedule I.exe":b"game",
            self.game/"GameAssembly.dll":b"game binary",
            self.game/"existing-save.txt":b"preserve",
            self.root/"vendor/melonloader/version.dll":b"loader",
            self.root/"vendor/melonloader/MelonLoader/net6/loader.dll":b"managed",
            self.root/"vendor/windows-dotnet6/host/fxr/6.0.36/hostfxr.dll":b"runtime",
            self.root/"mod/bin/Debug/net6.0/SkateSchedule.dll":b"mod",
        }
        for path,content in fixtures.items():
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(content)
        self.addCleanup(patch.stopall)
        patch.object(installer,"PROJECT",self.root).start()
        patch.object(installer,"require_game_closed",lambda:None).start()
        self.output=contextlib.redirect_stdout(io.StringIO())
        self.output.__enter__()
        self.addCleanup(self.output.__exit__,None,None,None)

    def test_dry_run_does_not_install_and_removal_preserves_untracked_files(self):
        installer.install(self.game,True)
        self.assertFalse((self.game/"version.dll").exists())
        self.assertFalse((self.root/"data/install-manifest.json").exists())
        installer.install(self.game,False)
        installer.uninstall(True)
        self.assertTrue((self.game/"version.dll").is_file())
        installer.uninstall(False)
        self.assertFalse((self.game/"version.dll").exists())
        self.assertEqual((self.game/"existing-save.txt").read_bytes(),b"preserve")
        self.assertEqual((self.game/"Schedule I.exe").read_bytes(),b"game")

    def test_conflicting_loader_file_prevents_all_writes(self):
        (self.game/"version.dll").write_bytes(b"existing loader")
        with self.assertRaises(FileExistsError):
            installer.install(self.game,False)
        self.assertFalse((self.game/"Mods").exists())
        self.assertFalse((self.root/"data/install-manifest.json").exists())
        self.assertEqual((self.game/"version.dll").read_bytes(),b"existing loader")

    def test_changed_mod_prevents_update_and_removal(self):
        installer.install(self.game,False)
        (self.game/"Mods/SkateSchedule.dll").write_bytes(b"other tool changed this")
        with self.assertRaises(ValueError):
            installer.update_mod()
        with self.assertRaises(ValueError):
            installer.uninstall(False)
        self.assertTrue((self.game/"version.dll").is_file())
        self.assertEqual((self.game/"Mods/SkateSchedule.dll").read_bytes(),b"other tool changed this")

    def test_collision_update_is_tracked_and_changed_cache_prevents_mod_replacement(self):
        installer.install(self.game,False)
        cache=self.root/"data/schedule-collision"
        cache.mkdir()
        (cache/"index.json").write_text(json.dumps({"version":1,"meshes":[],
            "GameAssemblyHash":hashlib.sha512(b"game binary").hexdigest().upper()}))
        (cache/"probe.mesh").write_bytes(b"geometry")
        source=self.root/"mod/bin/Debug/net6.0/SkateSchedule.dll"
        source.write_bytes(b"new mod")
        installer.update_mod()
        state=json.loads((self.root/"data/install-manifest.json").read_text())
        self.assertIn("Mods/SkateScheduleAssets/probe.mesh",{e["path"] for e in state["files"]})
        (self.game/"Mods/SkateScheduleAssets/probe.mesh").write_bytes(b"user modification")
        (cache/"probe.mesh").write_bytes(b"updated geometry")
        source.write_bytes(b"another mod")
        with self.assertRaises(ValueError):
            installer.update_mod()
        self.assertEqual((self.game/"Mods/SkateSchedule.dll").read_bytes(),b"new mod")

if __name__=="__main__":
    unittest.main()
