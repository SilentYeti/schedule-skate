"""Exercise real skating, failed-trick recovery and collision streaming without Unity."""
from array import array
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
import argparse
import json
import math
import multiprocessing
import socket
import subprocess
import sys
import threading
import time

from headless import PROJECT, floor, summary


class Channel:
    def __init__(self, port):
        self.socket = socket.create_connection(("127.0.0.1", port), timeout=60)
        self.socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.stream = self.socket.makefile("rb")

    def send(self, request, payload=None):
        started = time.perf_counter()
        header = json.dumps(request, separators=(",", ":"), allow_nan=False).encode() + b"\n"
        encoded = time.perf_counter()
        self.socket.sendall(header)
        if payload is not None:
            self.socket.sendall(payload)
        written = time.perf_counter()
        line = self.stream.readline(1024 * 1024 + 1)
        if not line or len(line) > 1024 * 1024 or not line.endswith(b"\n"):
            raise RuntimeError("Missing or oversized engine response")
        reply = json.loads(line)
        if not reply.get("ok"):
            raise RuntimeError(reply)
        finished = time.perf_counter()
        return reply, {"total_ms": (finished - started) * 1000,
                       "encode_ms": (encoded - started) * 1000,
                       "write_ms": (written - encoded) * 1000,
                       "response_wait_ms": (finished - written) * 1000}

    def close(self):
        self.stream.close()
        self.socket.close()


@contextmanager
def engine(host, log_path):
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("w") as log:
        process = subprocess.Popen([str(host), "--assets", str(PROJECT / "data/ps3-assets"),
                                    "--listen", "127.0.0.1:0"], stdout=log,
                                   stderr=subprocess.PIPE, text=True)
        drain = None
        channels = []
        try:
            startup = process.stderr.readline().strip()
            log.write(startup + "\n")
            if not startup.startswith("Skate bridge listening on "):
                raise RuntimeError(startup or "Engine exited before announcing its port")

            def drain_log():
                for line in process.stderr:
                    log.write(line)
                log.flush()

            drain = threading.Thread(target=drain_log, daemon=True)
            drain.start()
            port = int(startup.rsplit(":", 1)[1])
            channels.append(Channel(port))
            channels.append(Channel(port))
            yield channels
        finally:
            for channel in channels:
                channel.close()
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if drain is not None:
                drain.join(timeout=2)
            process.stderr.close()


def packed(triangles):
    values = array("f", (v for triangle in triangles for point in triangle for v in point))
    if sys.byteorder != "little":
        values.byteswap()
    assert values.itemsize == 4 and len(values) == len(triangles) * 9
    return values.tobytes()


def patch(cells, centre=(0, 0), slope=0):
    return [[[p[0] + centre[0], slope * (p[2] + centre[1]), p[2] + centre[1]]
             for p in triangle] for triangle in floor(cells)]


def pack_patch(cells, centre, slope):
    started = time.perf_counter()
    # Avoid a second large Python triangle tree whose allocation time can outrun the test's floor.
    values=array('f')
    for z in range(cells):
        for x in range(cells):
            x0=-24+48*x/cells+centre[0];x1=-24+48*(x+1)/cells+centre[0]
            z0=-24+48*z/cells+centre[1];z1=-24+48*(z+1)/cells+centre[1]
            for px,pz in ((x0,z0),(x0,z1),(x1,z1),(x0,z0),(x1,z1),(x1,z0)):
                values.extend((px,slope*pz,pz))
    if sys.byteorder!='little': values.byteswap()
    return values.tobytes(), (time.perf_counter() - started) * 1000


def joint_position(frame, name):
    bone = frame["bones"][frame["names"].index(name)]
    root = frame["root"]
    return [sum(root[row + col * 4] * bone[12 + col] for col in range(4)) for row in range(3)]


def board_position(frame):
    return joint_position(frame, "SKATEBOARD_ROOT")


def sample(frame, timing, request):
    return {"tick": frame["tick"], "state": frame["state"], "trick": frame["line_trick"],
            "root": frame["root"][12:15], "board": board_position(frame),
            "hips": joint_position(frame, "HIPS"), "left_foot": joint_position(frame, "LEFTFOOT"),
            "right_foot": joint_position(frame, "RIGHTFOOT"),
            "velocity": frame["velocity"], "collision_pending": frame.get("collision_pending", False),
            "latency_ms": timing["total_ms"], "input": request}


def check(report, condition, message):
    if not condition:
        report["failures"].append(message)


def step(channel, buttons=0, right=(0, 0), left=(0, 0), ticks=1, aspect=16/9):
    request = {"op": "step", "buttons": buttons, "triggers": [0, 0],
               "left": list(left), "right": list(right), "ticks": ticks, "aspect": aspect}
    frame, timing = channel.send(request)
    return frame, sample(frame, timing, request)


def activate(channel, triangles, spawn=(0, .25, 0), heading=0, aspect=16/9):
    encode_started = time.perf_counter()
    payload = packed(triangles)
    encode_ms = (time.perf_counter() - encode_started) * 1000
    frame, timing = channel.send({"op": "activate_packed", "count": len(triangles),
                                  "spawn": list(spawn), "heading": heading, "aspect": aspect}, payload)
    timing["pack_ms"] = encode_ms
    return frame, timing


def tricks(control, report):
    # Tiny charge and a held flick really fail; a longer charge can land a double flip.
    for name, hold in (("landed_kickflip", 1), ("failed_kickflip", 90)):
        case = {"charge_ticks": 1, "hold_ticks": hold, "trace": []}
        report["cases"][name] = case
        large_floor = [[[v * (100 / 24) for v in point] for point in triangle] for triangle in floor(1)]
        frame, case["activate"] = activate(control, large_floor)
        for buttons, count in ((0, 60), (4096, 60)):
            for _ in range(count):
                frame, entry = step(control, buttons=buttons)
                case["trace"].append(entry)
        start_tick = frame["tick"]
        frame, entry = step(control, right=(0, -32767))
        case["trace"].append(entry)
        for _ in range(hold):
            frame, entry = step(control, right=(29772, 13669))
            case["trace"].append(entry)
        # Record every native tick so short teleport transitions cannot be missed.
        for _ in range(math.ceil(12 / frame["period"])):
            frame, entry = step(control)
            case["trace"].append(entry)
        trial = [entry for entry in case["trace"] if entry["tick"] > start_tick]
        states = [entry["state"] for entry in trial]
        check(report, any("KICKFLIP" in entry["trick"] for entry in trial), f"{name}: no real kickflip recognized")
        if hold == 1:
            check(report, "KnownAir" in states and "WipeoutGround" not in states and states[-1] == "PhysicsGround",
                  "Single flick kickflip did not land cleanly")
        else:
            bail = next((i for i, state in enumerate(states) if state == "WipeoutGround"), None)
            teleport = next((i for i, state in enumerate(states) if state == "Teleporting"), None)
            recovered = next((i for i, state in enumerate(states) if teleport is not None and i > teleport and state == "PhysicsGround"), None)
            check(report, bail is not None and teleport is not None and recovered is not None and bail < teleport < recovered,
                  "Failed kickflip did not bail and automatically recover through the native teleport state")
            if recovered is not None and bail is not None:
                case["recovery_seconds"] = (trial[recovered]["tick"] - trial[bail]["tick"]) * frame["period"]
                check(report, case["recovery_seconds"] <= 12, "Native bail recovery exceeded 12 simulation seconds")
                ragdoll = [entry for entry in trial if entry["state"] == "WipeoutGround"]
                case["max_bail_rider_board_separation_m"] = max(math.dist(entry["hips"], entry["board"]) for entry in ragdoll)
                check(report, len(ragdoll) >= 30, "Native bail skipped the falling/recovery period")
        case["state_changes"] = [entry for i, entry in enumerate(trial) if i == 0 or entry["state"] != trial[i - 1]["state"]]
        case["step_latency"] = summary([entry["latency_ms"] for entry in case["trace"]])


def offboard_jumps(control, report):
    case={"trace":[],"trials":[],"limits":"Synthetic on-foot jumps; does not restore the failed live grind/runout history."}
    report["cases"]["offboard_jumps"]=case
    for slope,left in ((0,(482,-429)),(.087,(0,32767)),(-.087,(32767,0))):
        activate(control,patch(1,slope=slope))
        for _ in range(60): step(control)
        step(control,buttons=32768)
        for _ in range(90): step(control,left=left)
        trial=[]
        for buttons,count in ((16384,6),(0,150)):
            for _ in range(count):
                frame,entry=step(control,buttons=buttons,left=left if buttons else (0,0))
                trial.append(entry);case["trace"].append(entry)
        states=sorted({entry["state"] for entry in trial})
        minimum=min(entry["root"][1]-slope*entry["root"][2] for entry in trial)
        case["trials"].append({"slope":slope,"states":states,"min_root_clearance_m":minimum,"final_state":frame["state"]})
        check(report,"BipedAir" in states and frame["state"]=="BipedGround" and minimum>-.12,
              f"On-foot jump failed to launch and land on slope {slope}")


def walking(control, collision, report):
    case={"trace":[],"baseline_trace":[],"refreshes":0,
          "limits":"Native walking coverage; stationary loose-board focus and retargeting are checked separately by GeometryChecks."}
    report["cases"]["walking_ground_coverage"]=case
    for refresh in (False,True):
        frame,_=activate(control,floor(1))
        for _ in range(60): frame,_=step(control)
        frame,_=step(control,buttons=32768)
        centre=[0,0,0]
        trace=case["trace"] if refresh else case["baseline_trace"]
        for _ in range(600 if refresh else 330):
            frame,entry=step(control,buttons=4096,left=(0,32767))
            trace.append(entry)
            root=frame["root"][12:15]
            if refresh and math.dist(root,centre)>24*.4 and not frame.get("collision_pending",False):
                centre=root
                payload=packed(patch(1,(root[0],root[2])))
                collision.send({"op":"collision_packed","count":2},payload)
                case["refreshes"]+=1
        if not refresh:
            case["baseline_min_root_y_m"]=min(e["root"][1] for e in trace)
            check(report,case["baseline_min_root_y_m"] < -1,
                  "Walking fixture did not reproduce leaving the finite exported floor")
        else:
            case["min_root_y_m"]=min(e["root"][1] for e in trace)
            case["distance_m"]=math.hypot(root[0],root[2])
            check(report,case["distance_m"]>50 and case["refreshes"]>=5,
                  "Walking test did not cross enough exported-floor boundaries")
            check(report,case["min_root_y_m"]>-.12 and frame["state"]=="BipedGround",
                  "Skater lost ground while collision followed the on-foot root")


def streaming(control, collision, report, cells, refreshes, slope):
    name = "moving_flat_floor" if slope == 0 else "moving_five_percent_grade" if slope == .05 else f"moving_grade_{slope:g}"
    case = {"slope": slope, "triangles_per_patch": cells * cells * 2, "trace": [], "updates": []}
    report["cases"][name] = case
    frame, case["activate"] = activate(control, patch(1, slope=slope))
    for _ in range(120):
        frame, entry = step(control, buttons=4096)
        case["trace"].append(entry)
    initial = board_position(frame)
    # Python mesh allocations otherwise stop this process through its GIL/GC and fake engine hitches.
    with ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn")) as baker, ThreadPoolExecutor(max_workers=1) as executor:
        for _ in range(refreshes):
            centre = board_position(frame)
            update = {"centre": centre, "bytes": cells * cells * 2 * 36, "start_tick": frame["tick"]}
            case["updates"].append(update)
            packing = threading.Event()
            packing.set()
            prepared = baker.submit(pack_patch, cells, (centre[0], centre[2]), slope)

            def send_patch():
                payload, update["pack_ms"] = prepared.result()
                packing.clear()
                return collision.send({"op": "collision_packed", "count": cells * cells * 2}, payload)

            future = executor.submit(send_patch)
            deadline = time.monotonic() + 30
            advancing_during_pack = 0
            advancing_during_transfer = 0
            advancing_during_build = 0
            queued = None
            ticks = 0
            # Recenter before the 24 m radius ends; preserve the real build/simulation time ratio.
            while (ticks < 60 and math.dist(board_position(frame),centre)<9.6) or not future.done() or frame.get("collision_pending", False):
                tick_started = time.perf_counter()
                is_packing = packing.is_set()
                transferring = not future.done() and not is_packing
                previous_tick = frame["tick"]
                frame, entry = step(control, buttons=4096)
                case["trace"].append(entry)
                check(report, frame["tick"] == previous_tick + 1, f"{name}: simulation skipped or repeated a tick")
                advancing_during_pack += is_packing
                advancing_during_transfer += transferring
                advancing_during_build += bool(frame.get("collision_pending", False))
                ticks += 1
                if queued is None and future.done():
                    queued, update["reply_timing"] = future.result()
                    update["queued_pending"] = queued.get("collision_pending", False)
                if time.monotonic() > deadline:
                    raise RuntimeError(f"{name}: collision transfer/build exceeded 30 seconds")
                time.sleep(max(0, frame["period"] - (time.perf_counter() - tick_started)))
            update.update(end_tick=frame["tick"], control_ticks_during_transfer=advancing_during_transfer,
                          control_ticks_during_build=advancing_during_build, control_ticks_during_pack=advancing_during_pack)
            check(report, advancing_during_transfer > 0 or advancing_during_build > 0,
                  f"{name}: no simulation progress observed during collision transfer/build")
    measured = case["trace"][120:]
    heights = [entry["board"][1] - slope * entry["board"][2] for entry in measured]
    speeds = [math.hypot(entry["velocity"][0], entry["velocity"][2]) for entry in measured]
    drops = [a - b for a, b in zip(speeds, speeds[1:])]
    case.update(min_board_height_above_floor_m=min(heights), min_speed_mps=min(speeds),
                largest_tick_speed_drop_mps=max(drops),
                distance_m=math.dist(initial, board_position(frame)),
                step_latency=summary([entry["latency_ms"] for entry in measured]))
    check(report, min(heights) > -.12, f"{name}: board penetrated floor by more than 12 cm")
    check(report, min(speeds) > 1, f"{name}: momentum stopped on empty rideable floor")
    check(report, max(drops) < 1.5, f"{name}: speed dropped more than 1.5 m/s within a tick")
    check(report, all(entry["state"] in ("PhysicsGround", "GroundAnimation") for entry in measured),
          f"{name}: riding lost ground contact or bailed on empty floor")
    check(report, case["step_latency"]["max_ms"] < 100 and case["step_latency"]["p95_ms"] < 33,
          f"{name}: host control replies exceeded hitch budget (max100ms / p95 33ms)")
    _, case["suspend"] = control.send({"op": "suspend"})
    _, case["resume"] = control.send({"op": "resume", "spawn": [centre[0], slope * centre[2] + .25, centre[2]],
                                     "heading": 0, "aspect": 16/9})
    check(report, case["resume"]["total_ms"] < 250, f"{name}: warm resume exceeded 250 ms")
    case["resume_trace"] = []
    for buttons, count in ((0, 60), (4096, 60)):
        for _ in range(count):
            frame, entry = step(control, buttons=buttons)
            case["resume_trace"].append(entry)
    check(report, frame["state"] == "PhysicsGround" and math.hypot(frame["velocity"][0], frame["velocity"][2]) > 1,
          f"{name}: resumed session did not return to grounded skating")


def read_capture(path):
    payload=None
    if path.suffix == ".skb":
        with path.open("rb") as capture:
            header = capture.readline(16385)
            if not header.endswith(b"\n") or len(header) > 16384:
                raise ValueError("Binary capture has an invalid header")
            request = json.loads(header)
            count = request.get("count")
            if request.get("op") != "activate_packed" or type(count) is not int or not 1 <= count <= 200000:
                raise ValueError("Binary replay must contain an activate_packed header and bounded triangle count")
            payload = capture.read(count * 36 + 1)
            if len(payload) != count * 36:
                raise ValueError("Binary capture triangle payload is truncated or has trailing bytes")
    else:
        request=json.loads(path.read_text())
        if request.get("op")!="activate": raise ValueError("JSON replay must contain an activation request")
    return request,payload


def replay(control, report, path):
    case = {"fixture": str(path), "trace": [], "limits": "Unknown walls, terrain and spawn; no clear-floor assertion."}
    report["cases"]["captured_scene"] = case
    request,payload=read_capture(path)
    case["triangles"]=request["count"] if payload is not None else len(request["triangles"])
    _,case["activate"]=control.send(request,payload)
    for buttons, right, count in ((0, (0, 0), 60), (4096, (0, 0), 120), (0, (0, -32767), 1),
                                   (0, (29772, 13669), 1), (0, (0, 0), 180)):
        for _ in range(count):
            _, entry = step(control, buttons=buttons, right=right, aspect=request.get("aspect", 16/9))
            case["trace"].append(entry)
    check(report, all(all(math.isfinite(v) for v in entry["board"] + entry["velocity"]) for entry in case["trace"]),
          "Captured scene produced invalid position/velocity")
    case["step_latency"] = summary([entry["latency_ms"] for entry in case["trace"]])
    case["push_distance_m"] = math.dist(case["trace"][59]["board"], case["trace"][179]["board"])
    case["min_board_y_m"] = min(entry["board"][1] for entry in case["trace"])
    opposite = {"heading": request["heading"] + math.pi, "trace": [],
                "limits": "Opposite heading checks local obstacle sensitivity, not every map surface."}
    report["cases"]["captured_scene_opposite_heading"] = opposite
    _, opposite["activate"] = control.send({**request, "heading": opposite["heading"]}, payload)
    for buttons, count in ((0, 60), (4096, 120)):
        for _ in range(count):
            _, entry = step(control, buttons=buttons, aspect=request.get("aspect", 16/9))
            opposite["trace"].append(entry)
    opposite["push_distance_m"] = math.dist(opposite["trace"][59]["board"], opposite["trace"][179]["board"])
    opposite["min_board_y_m"] = min(entry["board"][1] for entry in opposite["trace"])
    opposite["step_latency"] = summary([entry["latency_ms"] for entry in opposite["trace"]])


def run(args):
    report = {"ok": False, "host": str(args.host), "failures": [], "cases": {},
              "limits": ["Real native gameplay and transport only; no Unity frame, collider snapshot or GC measurement.",
                         "Does not establish native Schedule I dismount suppression, camera render or seamless mount.",
                         "Synthetic floor assertions do not apply to captured streets containing obstacles."]}
    try:
        with engine(args.host, args.out.with_suffix(".log")) as (control, collision):
            walking(control, collision, report)
            tricks(control, report)
            offboard_jumps(control, report)
            streaming(control, collision, report, args.cells, args.refreshes, 0)
            streaming(control, collision, report, args.cells, args.refreshes, .05)
            streaming(control, collision, report, args.cells, args.refreshes, .268)
            streaming(control, collision, report, args.cells, args.refreshes, -.268)
            if args.replay:
                replay(control, report, args.replay)
    except Exception as error:
        report["failures"].append(f"Harness stopped: {type(error).__name__}: {error}")
    report["ok"] = not report["failures"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"ok": report["ok"], "failures": report["failures"], "report": str(args.out),
                      "cases": {name: {k: v for k, v in case.items() if k not in ("trace", "baseline_trace", "resume_trace", "state_changes")}
                                for name, case in report["cases"].items()}}, indent=2))
    return 0 if report["ok"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", type=Path, default=PROJECT / "target/debug/skate-schedule")
    parser.add_argument("--cells", type=int, default=224)
    parser.add_argument("--refreshes", type=int, default=3)
    parser.add_argument("--out", type=Path, default=PROJECT / "data/riding-report.json")
    parser.add_argument("--replay", type=Path, help="Exact activation.json/.skb, exercised without obstacle-free assertions")
    args = parser.parse_args()
    if not 1 <= args.cells <= 300 or not 1 <= args.refreshes <= 10:
        parser.error("cells: 1..300; refreshes: 1..10")
    args.host = args.host.resolve()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
