import importlib.util
from pathlib import Path
from types import SimpleNamespace
import struct
import unittest
import numpy as np

spec=importlib.util.spec_from_file_location("export_collision",Path(__file__).with_name("export_collision.py"))
exporter=importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)

class CollisionTests(unittest.TestCase):
    def test_submesh_base_vertex_is_applied_and_invalid_indices_are_rejected(self):
        mesh=SimpleNamespace(baseVertex=4)
        actual=exporter.triangle_indices([mesh],[[(0,1,2)]],7)
        self.assertEqual(actual.tolist(),[4,5,6])
        with self.assertRaises(ValueError):
            exporter.triangle_indices([mesh],[[(0,1,3)]],7)

    def test_binary_geometry_has_valid_lengths_and_rejects_nonfinite_vertices(self):
        vertices=[[0,0,0],[1,0,0],[0,0,1]]
        blob=exporter.mesh_blob(vertices,[0,1,2])
        self.assertEqual(struct.unpack_from("<4sII",blob),(b"SKM1",3,3))
        self.assertEqual(len(blob),12+3*12+3*4)
        for bad_vertices,bad_indices in (
            ([[float("nan"),0,0]],[0,0,0]),(vertices,[0,1]),(vertices,[0,1,3])):
            with self.assertRaises(ValueError):
                exporter.mesh_blob(bad_vertices,bad_indices)

    def test_convex_faces_point_outward(self):
        vertices=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]],dtype=float)
        indices=exporter.convex_indices(vertices).reshape(-1,3)
        self.assertEqual(len(indices),4)
        centre=vertices.mean(axis=0)
        for face in indices:
            a,b,c=vertices[face]
            self.assertGreater(np.dot(np.cross(b-a,c-a),(a+b+c)/3-centre),0)

    def test_exported_cache_covers_failing_meshes_with_valid_buffers(self):
        import json
        directory=exporter.PROJECT/"data/schedule-collision"
        if not (directory/"index.json").is_file():
            self.skipTest("Export your own Schedule I collision cache to run the real-mesh check")
        index=json.loads((directory/"index.json").read_text())
        self.assertTrue({"Rock1_LOD0","Rock3_LOD0","Main"} <= {e["name"] for e in index["meshes"]})
        files={e["file"] for e in index["meshes"]}|{e["convex_file"] for e in index["meshes"] if e["convex_file"]}
        for name in files:
            blob=(directory/name).read_bytes()
            magic,vertices,indices=struct.unpack_from("<4sII",blob)
            self.assertEqual(magic,b"SKM1")
            self.assertEqual(len(blob),12+vertices*12+indices*4)
            points=np.frombuffer(blob,dtype="<f4",count=vertices*3,offset=12)
            faces=np.frombuffer(blob,dtype="<i4",count=indices,offset=12+vertices*12)
            self.assertTrue(np.isfinite(points).all())
            self.assertGreater(vertices,0)
            self.assertEqual(indices%3,0)
            self.assertGreaterEqual(faces.min(),0)
            self.assertLess(faces.max(),vertices)

if __name__=="__main__":
    unittest.main()
