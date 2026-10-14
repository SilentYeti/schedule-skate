"""Exercise the real bridge process with the exported PS3 gameplay data."""
from pathlib import Path
import json
import math
import socket
import subprocess
import tempfile
import time
import unittest

PROJECT=Path(__file__).resolve().parents[1]


class BridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.process=subprocess.Popen([str(PROJECT/"target/debug/skate-schedule"),
            "--assets",str(PROJECT/"data/ps3-assets"),"--listen","127.0.0.1:0"],
            stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
        startup=cls.process.stderr.readline().strip()
        if not startup.startswith("Skate bridge listening on "):
            cls.process.terminate()
            cls.process.wait()
            raise RuntimeError(startup)
        cls.address=startup.split()[-1]
        cls.connection=socket.create_connection(("127.0.0.1",int(cls.address.split(":")[-1])),timeout=20)
        cls.stream=cls.connection.makefile("rw",encoding="utf-8",newline="\n")
    @classmethod
    def tearDownClass(cls):
        cls.stream.close()
        cls.connection.close()
        cls.process.terminate()
        cls.process.wait(timeout=10)
        cls.process.stdout.close()
        cls.process.stderr.close()
    def send(self,request):
        self.stream.write(json.dumps(request)+"\n")
        self.stream.flush()
        response=self.stream.readline()
        self.assertLess(len(response),1024*1024)
        return json.loads(response)
    def test_real_simulation_and_failure_recovery(self):
        self.assertFalse(self.send({"op":"step","buttons":0,"triggers":[0,0],
            "left":[0,0],"right":[0,0],"ticks":1})["ok"])
        floor=[[[-100,0,-100],[-100,0,100],[100,0,100]],
               [[-100,0,-100],[100,0,100],[100,0,-100]]]
        reply=self.send({"op":"activate","triangles":floor,"rails":[],"spawn":[0,0.3,0],"heading":0})
        self.assertTrue(reply["ok"],reply)
        self.assertEqual(len(reply["bones"]),len(reply["names"]))
        self.assertIn("SKATEBOARD_ROOT",reply["names"])
        self.assertEqual(len(reply["rest"]),len(reply["names"]))
        for matrix in reply["rest"]:
            self.assertEqual(len(matrix),16)
            self.assertTrue(all(math.isfinite(v) for v in matrix))
        self.assertGreater(reply["period"],0)
        start=reply["tick"]
        for _ in range(15):
            reply=self.send({"op":"step","buttons":0,"triggers":[0,0],"left":[0,0],"right":[0,0],"ticks":8})
            self.assertTrue(reply["ok"],reply)
        settled=reply["root"][12:15]
        for _ in range(15):
            reply=self.send({"op":"step","buttons":4096,"triggers":[0,0],"left":[0,0],"right":[0,0],"ticks":8})
            self.assertTrue(reply["ok"],reply)
        self.assertEqual(reply["tick"],start+240)
        self.assertGreater(sum((a-b)**2 for a,b in zip(settled,reply["root"][12:15])),0.1)
        self.assertLess(abs(reply["root"][13]),5)
        report={"ok":True,"ticks":reply["tick"],"state":reply["state"],
                "final_position":reply["root"][12:15],"bones":reply["names"]}
        (PROJECT/"data/bridge-report.json").write_text(json.dumps(report,indent=2)+"\n")
        self.assertFalse(self.send({"op":"collision","triangles":[[[0,0,0]]*3],"rails":[]})["ok"])
        self.assertFalse(self.send({"op":"step","buttons":0,"triggers":[0,0],
            "left":[0,0],"right":[0,0],"ticks":1})["ok"])
        print(json.dumps(report))
    def test_unknown_operation_is_rejected(self):
        self.assertFalse(self.send({"op":"unknown"})["ok"])

    def test_partial_binary_upload_does_not_block_input_and_resume_keeps_velocity(self):
        from headless import floor
        from riding_checks import packed
        self.assertTrue(self.send({"op":"activate","triangles":floor(1),"rails":[],"spawn":[0,.3,0],"heading":0})["ok"])
        alternate=socket.create_connection(("127.0.0.1",int(self.address.split(":")[-1])),timeout=20)
        reader=alternate.makefile("rb")
        try:
            payload=packed(floor(1))
            alternate.sendall(b'{"op":"collision_packed","count":2}\n'+payload[:8])
            start=time.perf_counter()
            reply=self.send({"op":"step","buttons":4096,"triggers":[0,0],"left":[0,0],"right":[0,0],"ticks":1})
            self.assertTrue(reply["ok"],reply)
            self.assertLess(time.perf_counter()-start,.25,"Partial collision socket blocked the control channel")
            alternate.sendall(payload[8:])
            self.assertTrue(json.loads(reader.readline())["ok"])
            self.assertTrue(self.send({"op":"suspend"})["ok"])
            self.assertFalse(self.send({"op":"step","buttons":0,"triggers":[0,0],"left":[0,0],"right":[0,0],"ticks":1})["ok"])
            reply=self.send({"op":"resume","spawn":[0,.3,0],"heading":0,"velocity":[2,0,3]})
            self.assertTrue(reply["ok"],reply)
            self.assertAlmostEqual(reply["velocity"][0],2,places=3)
            self.assertAlmostEqual(reply["velocity"][2],3,places=3)
            self.assertEqual(len(reply["rest"]),36)
            alternate.sendall(b'{"op":"collision_packed","count":1}\n'+bytes(36))
            self.assertFalse(json.loads(reader.readline())["ok"])
            self.assertFalse(self.send({"op":"step","buttons":0,"triggers":[0,0],"left":[0,0],"right":[0,0],"ticks":1})["ok"])
        finally:
            reader.close(); alternate.close(); self.send({"op":"unknown"})

    def test_background_collision_keeps_ticks_running(self):
        from headless import floor
        triangles=floor(64)
        try:
            self.assertTrue(self.send({"op":"activate","triangles":floor(1),"rails":[],"spawn":[0,.3,0],"heading":0})["ok"])
            reply=self.send({"op":"collision","triangles":triangles,"rails":[]})
            self.assertTrue(reply["ok"],reply)
            self.assertTrue(reply["collision_pending"],reply)
            deadline=time.monotonic()+10
            while reply["collision_pending"]:
                tick=reply["tick"]
                reply=self.send({"op":"step","buttons":0,"triggers":[0,0],"left":[0,0],"right":[0,0],"ticks":1})
                self.assertTrue(reply["ok"],reply)
                self.assertEqual(reply["tick"],tick+1)
                self.assertLess(time.monotonic(),deadline,"Collision worker did not finish")
            self.assertEqual(reply["state"],"PhysicsGround")
        finally:
            self.send({"op":"unknown"})

    def test_controller_serialization_reaches_engine(self):
        dotnet=PROJECT/"vendor/dotnet/dotnet"
        checks=PROJECT/"tools/ControllerChecks"
        subprocess.run([str(dotnet),"build",str(checks/"ControllerChecks.csproj"),"--nologo"],check=True,
                       stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
        with tempfile.TemporaryDirectory() as temporary:
            fixture=Path(temporary)/"xinput.so"
            subprocess.run(["cc","-shared","-fPIC",str(checks/"xinput_fixture.c"),"-o",str(fixture)],check=True)
            output=subprocess.check_output([str(dotnet),str(checks/"bin/Debug/net10.0/ControllerChecks.dll"),
                                            str(fixture)],text=True)
        keyboard,pad,charge,flick=map(json.loads,output.splitlines())
        self.assertEqual(keyboard,{"op":"step","buttons":4096,"triggers":[0,0],
            "left":[0,32767],"right":[-32767,0],"ticks":8,"aspect":keyboard["aspect"]})
        self.assertAlmostEqual(keyboard["aspect"],16/9,places=6)
        self.assertEqual(pad,{"op":"step","buttons":4096,"triggers":[1,255],
            "left":[-32768,32767],"right":[123,-456],"ticks":1,"aspect":1.25})
        floor=[[[-100,0,-100],[-100,0,100],[100,0,100]],
               [[-100,0,-100],[100,0,100],[100,0,-100]]]
        try:
            self.assertTrue(self.send({"op":"activate","triangles":floor,"rails":[],
                                      "spawn":[0,0.3,0],"heading":0})["ok"])
            for request in (keyboard,pad):
                reply=self.send(request)
                self.assertTrue(reply["ok"],reply)
            self.assertTrue(self.send({"op":"activate","triangles":floor,"rails":[],"spawn":[0,.3,0],"heading":0})["ok"])
            neutral={**charge,"right":[0,0]}
            for _ in range(60):self.assertTrue(self.send(neutral)["ok"])
            for _ in range(60):self.assertTrue(self.send({**neutral,"buttons":4096})["ok"])
            self.assertTrue(self.send(charge)["ok"])
            self.assertTrue(self.send(flick)["ok"])
            tricks=[]
            for _ in range(180):
                reply=self.send(neutral);self.assertTrue(reply["ok"],reply);tricks.append(reply["line_trick"])
            self.assertTrue(any("KICKFLIP" in name for name in tricks),"Real keyboard/mouse flick did not trigger a native kickflip")
            self.assertEqual(reply["state"],"PhysicsGround")
        finally:
            self.send({"op":"unknown"}) # Leave the next test with no active session.

    def test_camera_aspect_changes_fov_and_invalid_aspect_resets_session(self):
        from headless import floor
        step={"op":"step","buttons":0,"triggers":[0,0],"left":[0,0],"right":[0,0],"ticks":8}
        try:
            reply=self.send({"op":"activate","triangles":floor(1),"rails":[],"spawn":[0,.3,0],"heading":0,"aspect":1})
            self.assertTrue(reply["ok"],reply)
            base=reply["camera"]["fov"]
            for aspect in (1,4/3,16/9,21/9,1):
                reply=self.send({**step,"aspect":aspect})
                self.assertTrue(reply["ok"],reply)
                # The native lens output scales FOV by sqrt(aspect + 1).
                self.assertAlmostEqual(reply["camera"]["fov"],base*math.sqrt(2/(aspect+1)),places=3)
            self.assertFalse(self.send({**step,"aspect":0})["ok"])
            self.assertFalse(self.send(step)["ok"])
        finally:
            self.send({"op":"unknown"})


if __name__=="__main__":
    unittest.main()
