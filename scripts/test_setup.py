"""Check the pinned vendor patch without downloading tools or changing vendor files."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from setup_dev import PROJECT, apply_floor_seam_patch


class PatchChecks(unittest.TestCase):
    def test_apply_repeat_and_conflict(self):
        relative=Path('skate/crates/skate-core/src/physics/board_world.rs')
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); target=root/relative
            for filename in (relative,relative.parent/'collision/sphere_triangle.rs',relative.parent/'world_contact/primitive_query.rs',
                             Path('skate/crates/skate-core/src/player/offboard/ground_motion/support.rs'),
                             Path('skate/crates/skate-core/src/player/offboard/ground_motion/tests.rs')):
                contents=subprocess.check_output(['git','-C',str(PROJECT/'vendor/mashup'),'show',f'HEAD:{filename}'])
                (root/filename).parent.mkdir(parents=True,exist_ok=True); (root/filename).write_bytes(contents)
            pristine=target.read_bytes()
            apply_floor_seam_patch(root)
            applied=target.read_bytes()
            self.assertNotEqual(applied,pristine)
            apply_floor_seam_patch(root)
            self.assertEqual(target.read_bytes(),applied)
            conflict=pristine.replace(b'if face.y.abs() > 0.2',b'if face.y.abs() > 0.3')
            target.write_bytes(conflict)
            with self.assertRaises(subprocess.CalledProcessError): apply_floor_seam_patch(root)
            self.assertEqual(target.read_bytes(),conflict)


if __name__=='__main__': unittest.main()
