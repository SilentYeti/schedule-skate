"""Replay recorded native requests and packed collision, retaining timing/fidelity evidence."""
from array import array
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import argparse
import hashlib
import json
import math
import sys
import time

from headless import PROJECT, summary
from riding_checks import engine


def requests(rows):
    geometry = []
    events = []
    for row in rows:
        record = row["record"]
        if record["kind"] == "geometry":
            geometry.append(row)
        elif record["kind"] == "reply":
            request = record["request"]
            event = {"request": request, "expected": record,
                     "at_ms": row["at_ms"] - record["ms"], "reply_at_ms": row["at_ms"]}
            if request["op"] in ("activate_packed", "collision_packed"):
                match = next((i for i, candidate in enumerate(geometry)
                              if candidate["record"]["request"] == request), None)
                if match is None:
                    raise ValueError(f"No geometry captured for {request}")
                capture = geometry.pop(match)
                event["geometry_file"] = capture["record"]["file"]
                event["capture_at_ms"] = capture["at_ms"]
            events.append(event)
    if geometry:
        raise ValueError("Geometry capture has no matching logged reply")
    return sorted(events, key=lambda event: event["at_ms"])


def load_payload(directory, event):
    path = (directory / event["geometry_file"]).resolve()
    if not path.is_relative_to(directory.resolve()):
        raise ValueError("Capture file escapes diagnostic directory")
    with path.open("rb") as capture:
        line = capture.readline(16385)
        if len(line) > 16384 or not line.endswith(b"\n"):
            raise ValueError("Invalid capture header")
        header = json.loads(line)
        count = header.get("count")
        if header != event["request"] or type(count) is not int or not 1 <= count <= 200000:
            raise ValueError("Capture header disagrees with recorded request")
        payload = capture.read(count * 36 + 1)
        if len(payload) != count * 36:
            raise ValueError("Truncated or overlong packed geometry")
    return payload


def speed(record):
    velocity = record.get("velocity", [0, 0, 0])
    if not velocity or len(velocity) < 3:
        return 0
    return math.hypot(velocity[0], velocity[2])


def riding_speed_loss(frames):
    return max((speed(a)-speed(b) for a,b in zip(frames,frames[1:])
                if a['state']==b['state']=='PhysicsGround'),default=0.)


def launch_context_matches(event):
    return (event.get('state') == event.get('expected_state') == 'BipedGround'
            and event.get('root_error_m') is not None and event['root_error_m'] < .25)


def drops(events):
    result = []
    previous = None
    active_geometry = None
    for event in events:
        record = event["expected"]
        if "geometry_file" in event:
            active_geometry = event["geometry_file"]
        if not record.get("root") or len(record["root"]) < 15:
            previous = None
            continue
        if previous is not None and speed(previous) - speed(record) > .8:
            result.append({"drop_mps": speed(previous) - speed(record), "tick": record["tick"],
                           "op": event["request"]["op"], "state": record["state"],
                           "previous_state": previous["state"], "root": record["root"][12:15],
                           "geometry_file": active_geometry, "previous_root": previous["root"][12:15],
                           "previous_velocity": previous["velocity"], "velocity": record["velocity"],
                           "input": event["request"]})
        previous = record
    return sorted(result, key=lambda drop: drop["drop_mps"], reverse=True)


def nearby_geometry(directory, drop):
    """Retain nearby real faces; a face near the rider root is not proof of a solved contact."""
    path = directory / drop["geometry_file"]
    with path.open("rb") as capture:
        header = json.loads(capture.readline())
        values = array("f")
        values.frombytes(capture.read())
    if sys.byteorder != "little":
        values.byteswap()
    if len(values) != header["count"] * 9:
        raise ValueError("Invalid packed triangle count")
    root = drop["root"]
    faces = []
    for index in range(header["count"]):
        triangle = [values[index * 9 + lane:index * 9 + lane + 3].tolist() for lane in (0, 3, 6)]
        if any(max(p[axis] for p in triangle) < root[axis] - 1.2 or
               min(p[axis] for p in triangle) > root[axis] + 1.2 for axis in (0, 1, 2)):
            continue
        a, b, c = triangle
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        normal = [u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]]
        size = math.sqrt(sum(x * x for x in normal))
        if size > 0:
            normal = [x / size for x in normal]
        # Flat tessellated floor is summarized separately; preserve possible raised faces and barriers.
        if abs(normal[1]) < .5 or max(p[1] for p in triangle) > root[1] + .03:
            faces.append({"index": index, "normal": normal, "triangle": triangle})
    return {"nearby_obstacle_faces": faces[:100], "nearby_obstacle_face_count": len(faces),
            "limits": "1.2m root bounding window; latest captured world may still be building, and no contact impulse is logged."}


def self_test():
    header = {"op": "collision_packed", "count": 1}
    rows = [{"at_ms": 12, "record": {"kind": "geometry", "file": "one.skb", "request": header}},
            {"at_ms": 20, "record": {"kind": "reply", "request": {"op": "step"}, "ms": 5}},
            {"at_ms": 30, "record": {"kind": "reply", "request": header, "ms": 17}}]
    events = requests(rows)
    assert [e["at_ms"] for e in events] == [13, 15]
    assert events[0]["geometry_file"] == "one.skb"
    assert events[0]["capture_at_ms"] == 12
    try:
        requests(rows[1:])
    except ValueError:
        pass
    else:
        raise AssertionError("Uncaptured geometry accepted")
    assert speed({"velocity": [3, 5, 4]}) == 5
    assert riding_speed_loss([{'state':'PhysicsGround','velocity':[0,0,8]},
                              {'state':'PhysicsGround','velocity':[0,0,3]},
                              {'state':'BipedGround','velocity':[0,0,0]}])==5
    assert riding_speed_loss([{'state':'KnownAir','velocity':[0,0,8]},
                              {'state':'PhysicsGround','velocity':[0,0,0]}])==0
    context={'state':'BipedGround','expected_state':'BipedGround','root_error_m':.1}
    assert launch_context_matches(context)
    assert not launch_context_matches({**context,'state':'PhysicsAir'})
    assert not launch_context_matches({**context,'root_error_m':.3})
    assert not launch_context_matches({**context,'root_error_m':None})
    from riding_checks import packed,pack_patch,patch
    for slope in (0.,.268,-.268):
        assert pack_patch(3,(1.25,-2.5),slope)[0]==packed(patch(3,(1.25,-2.5),slope))
    floor=packed([[[-2,0,-2],[-2,0,2],[2,0,2]],[[-2,0,-2],[2,0,2],[2,0,-2]]])
    assert abs(ground_height(floor,[0,.1,0]))<1e-6
    try: ground_height(floor,[5,.1,0])
    except ValueError: pass
    else: raise AssertionError("Missing supporting floor accepted")


def ground_height(payload,root):
    values=array('f'); values.frombytes(payload)
    if sys.byteorder!='little': values.byteswap()
    x,y,z=root; heights=[]
    for i in range(0,len(values),9):
        a,b,c=(values[i+j:i+j+3] for j in (0,3,6))
        if not (min(a[0],b[0],c[0])<=x<=max(a[0],b[0],c[0]) and min(a[2],b[2],c[2])<=z<=max(a[2],b[2],c[2])): continue
        det=(b[2]-c[2])*(a[0]-c[0])+(c[0]-b[0])*(a[2]-c[2])
        if det>=-1e-8: continue
        u=((b[2]-c[2])*(x-c[0])+(c[0]-b[0])*(z-c[2]))/det
        w=((c[2]-a[2])*(x-c[0])+(a[0]-c[0])*(z-c[2]))/det
        height=u*a[1]+w*b[1]+(1-u-w)*c[1]
        if u>=0 and w>=0 and u+w<=1 and height<=y+.1: heights.append(height)
    if not heights: raise ValueError("Recorded seed has no supporting upward floor")
    return max(heights)


def approach(args):
    events=requests([json.loads(line) for line in args.trace.read_text().splitlines() if line.strip()])
    steps=[event for event in events if event['request']['op']=='step']
    seed=next(event for event in steps if event['expected']['tick']>=args.approach_tick-60)
    geometry=next(event for event in reversed(events[:events.index(seed)]) if 'geometry_file' in event)
    payload=load_payload(args.trace.parent,geometry)
    position=seed['expected']['root'][12:15]
    spawn=[position[0],ground_height(payload,position),position[2]]
    matrix=seed['expected']['root']; heading=math.atan2(matrix[8],matrix[10])
    packets=[event for event in steps if seed['expected']['tick']<event['expected']['tick']<=args.approach_tick+20]
    if not packets or packets[-1]['expected']['tick']<args.approach_tick: raise ValueError('Approach tick is outside recording')
    report={'trace':str(args.trace),'host_sha256':hashlib.sha256(args.host.read_bytes()).hexdigest(),
            'seed_tick':seed['expected']['tick'],'geometry_file':geometry['geometry_file'],'spawn':spawn,'cases':{},
            'limits':'Restores nearby floor, heading and velocity; skeleton/contact history and later collision refreshes are not restored.'}
    with engine(args.host,args.out.with_suffix('.log')) as (control,_):
        for name in ('recorded_inputs','neutral_right_stick'):
            control.send({'op':'activate_packed','count':len(payload)//36,'spawn':spawn,'heading':heading,'aspect':16/9},payload)
            control.send({'op':'resume','spawn':spawn,'heading':heading,'aspect':16/9,'velocity':seed['expected']['velocity']})
            frames=[];report['cases'][name]=frames
            for event in packets:
                request=event['request'] if name=='recorded_inputs' else {**event['request'],'right':[0,0]}
                frame,_=control.send(request)
                frames.append({key:frame[key] for key in ('tick','root','velocity','state')})
                frames[-1].update(recorded_tick=event['expected']['tick'],request=request,expected_state=event['expected']['state'])
    report['unexpected_offboard']=any(f['state'].startswith('Biped') and not f['request']['buttons']&32768
                                      for f in report['cases']['recorded_inputs'])
    report['largest_riding_speed_loss_mps']={name:riding_speed_loss(frames) for name,frames in report['cases'].items()}
    report['ok']=not report['unexpected_offboard'] and max(report['largest_riding_speed_loss_mps'].values())<1.5
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps({'ok':report['ok'],'speed_loss_mps':report['largest_riding_speed_loss_mps'],'states':{name:sorted({f['state'] for f in frames}) for name,frames in report['cases'].items()},'report':str(args.out)},indent=2))
    return 0 if report['ok'] else 1


def probe(args):
    """Coast across a recorded street seam and an actual raised curb from the same capture."""
    rows = [json.loads(line) for line in args.trace.read_text().splitlines() if line.strip()]
    events = requests(rows)
    index = next(i for i, event in enumerate(events) if event["expected"]["tick"] == args.probe_tick and event["request"]["op"] == "step")
    target = events[index]
    before = next(event["expected"] for event in reversed(events[:index]) if event["request"]["op"] == "step")
    geometry = next(event for event in reversed(events[:index]) if "geometry_file" in event)
    payload = load_payload(args.trace.parent, geometry)
    values = array("f")
    values.frombytes(payload)
    if sys.byteorder != "little":
        values.byteswap()
    # Pick a real 9 cm raised face, away from its corner, for the preservation control.
    candidates = []
    for start in range(0, len(values), 9):
        a, b, c = (values[start + lane:start + lane + 3].tolist() for lane in (0, 3, 6))
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        normal = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
        length = math.sqrt(sum(x*x for x in normal))
        if length == 0:
            continue
        normal = [x/length for x in normal]
        if abs(normal[1]) > .2 or not .06 < max(a[1], b[1], c[1]) < .14 or min(a[1], b[1], c[1]) > .02:
            continue
        centre = [(a[i]+b[i]+c[i])/3 for i in range(3)]
        candidates.append((math.hypot(centre[0]-before["root"][12], centre[2]-before["root"][14]), centre, normal, [a,b,c]))
    _, centre, normal, curb = min(candidates)
    velocity = before["velocity"]
    magnitude = speed(before)
    upstream = [before["root"][12] - velocity[0]/magnitude, 0, before["root"][14] - velocity[2]/magnitude]
    curb_spawn = [centre[0]+normal[0]*1.5, 0, centre[2]+normal[2]*1.5]
    curb_velocity = [-normal[0]*8, 0, -normal[2]*8]
    seed_index = next(i for i, event in enumerate(events) if event["request"]["op"] == "step" and event["expected"]["tick"] >= args.probe_tick - 60)
    seed = events[seed_index]["expected"]
    recorded_spawn = [seed["root"][12], 0, seed["root"][14]]
    recorded_inputs = [event for event in events[seed_index+1:] if event["request"]["op"] == "step" and event["expected"]["tick"] <= args.probe_tick + 20]
    with args.host.open("rb") as binary:
        digest = hashlib.file_digest(binary, "sha256").hexdigest()
    report = {"host": str(args.host), "host_sha256": digest, "trace": str(args.trace), "probe_tick": args.probe_tick,
              "geometry_file": geometry["geometry_file"], "real_curb_triangle": curb, "cases": {},
              "limits": ["Controlled roadY0 coasting probes; source data confirms this street height.",
                         "Recorded rootXZ and velocity are preserved but skeleton/contact history cannot be restored.",
                         "A real raised face from the same unchanged capture provides the collision-preservation control."]}
    with engine(args.host, args.out.with_suffix(".log")) as (control, _):
        control.send({"op":"suspend"})
        for name, spawn, motion in (("buried_seam", upstream, velocity), ("recorded_approach", recorded_spawn, seed["velocity"]), ("real_curb", curb_spawn, curb_velocity)):
            heading = math.atan2(motion[0], motion[2])
            if name == "recorded_approach" and speed(seed) < .1:
                heading = math.atan2(seed["root"][8], seed["root"][10])
            header = {"op":"activate_packed", "count":len(payload)//36, "spawn":spawn,"heading":heading,"aspect":16/9}
            control.send(header,payload)
            frame, _ = control.send({"op":"resume","spawn":spawn,"heading":heading,"aspect":16/9,"velocity":motion})
            case = {"spawn":spawn,"velocity":motion,"frames":[]}
            report["cases"][name] = case
            previous_speed = speed(frame)
            drops_mps = []
            inputs = [event["request"] for event in recorded_inputs] if name == "recorded_approach" else [{"op":"step","buttons":0,"triggers":[0,0],"left":[0,0],"right":[0,0],"ticks":1,"aspect":16/9}] * 40
            for request in inputs:
                frame, timing = control.send(request)
                current_speed = speed(frame)
                drops_mps.append(previous_speed-current_speed)
                previous_speed = current_speed
                case["frames"].append({**{key:frame[key] for key in ("tick","root","velocity","state")}, "request":request})
            case["largest_tick_drop_mps"] = max(drops_mps)
            case["initial_speed_mps"] = math.hypot(motion[0],motion[2])
            case["final_speed_mps"] = speed(frame)
            case["final_position"] = frame["root"][12:15]
    report["checks"] = {"buried_seam_keeps_momentum": report["cases"]["recorded_approach"]["largest_tick_drop_mps"] < 1.5,
                        "real_curb_remains_solid": report["cases"]["real_curb"]["largest_tick_drop_mps"] > 1.5 and report["cases"]["real_curb"]["final_speed_mps"] < 4}
    report["ok"] = all(report["checks"].values())
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({name:{key:value for key,value in case.items() if key!="frames"} for name,case in report["cases"].items()},indent=2))
    print(f"Report: {args.out}")
    return 0 if report["ok"] else 1


def run(args):
    rows = [json.loads(line) for line in args.trace.read_text().splitlines() if line.strip()]
    events = requests(rows)
    first = events[0]["at_ms"]
    if args.seconds is not None:
        events = [event for event in events if event["at_ms"] - first <= args.seconds * 1000]
    report = {"trace": str(args.trace), "speed_multiplier": args.speed, "ok": False, "errors": [],
              "limits": ["Exact logged input packets and geometry bytes; replacement installation tick was not captured.",
                         "Requests are ordered by inferred send time (reply timestamp minus duration).",
                         "Unity origin is absent; existing common-origin engine coordinates and world-frame observations are preserved.",
                         "Different native worker duration can change installation timing and later simulation output.",
                         "No Unity process, save, input injection or game lifecycle is exercised."],
              "observed_speed_drops": drops(events), "replay": [],
              "world_observations": [row for row in rows if row["record"]["kind"] in ("native_dismount", "state_change")],
              "frame_ground_deviations": [row for row in rows if row["record"]["kind"] == "frame" and
                  row["record"].get("ground_y") is not None and
                  row["record"]["ground_y"] - row["record"]["board"][1] > .2]}
    try:
        rejected_request = json.loads(args.rejected_request.read_text()) if args.rejected_request else None
        if rejected_request is not None and (not isinstance(rejected_request, dict) or rejected_request.get('op') != 'step'):
            raise ValueError('Rejected launch request must be a step packet')
        # Pre-read payloads so replay filesystem work cannot pause controller scheduling.
        payloads = {event["geometry_file"]: load_payload(args.trace.parent, event) for event in events if "geometry_file" in event}
        with engine(args.host, args.out.with_suffix(".log")) as (control, collision):
            _, report["warmup"] = control.send({"op": "suspend"})
            started = time.monotonic()
            with ThreadPoolExecutor(max_workers=1) as controls, ThreadPoolExecutor(max_workers=1) as worlds:
                futures = []

                def send(event):
                    actual_start = (time.monotonic() - started) * 1000
                    payload = payloads.get(event.get("geometry_file"))
                    channel = collision if event["request"]["op"] == "collision_packed" else control
                    frame, timing = channel.send(event["request"], payload)
                    expected = event["expected"]
                    return {"recorded_tick": expected["tick"], "tick": frame.get("tick"),
                            "request": event["request"], "geometry_file": event.get("geometry_file"),
                            "recorded_at_ms": event["at_ms"] - first, "actual_at_ms": actual_start,
                            "scheduled_at_ms": (event["at_ms"] - first) / args.speed,
                            "state": frame.get("state"), "expected_state": expected.get("state"),
                            "root": frame.get("root"), "velocity": frame.get("velocity"),
                            "root_error_m": math.dist(frame["root"][12:15], expected["root"][12:15]) if len(frame.get("root") or []) >= 15 and len(expected.get("root") or []) >= 15 else None,
                            "velocity_error_mps": math.dist(frame["velocity"], expected["velocity"]) if len(frame.get("velocity") or []) == 3 and len(expected.get("velocity") or []) == 3 else None,
                            "timing": timing}

                for event in events:
                    due = started + (event["at_ms"] - first) / (1000 * args.speed)
                    time.sleep(max(0, due - time.monotonic()))
                    executor = worlds if event["request"]["op"] == "collision_packed" else controls
                    futures.append(executor.submit(send, event))
                for future in futures:
                    try:
                        report["replay"].append(future.result())
                    except Exception as error:
                        report["errors"].append(str(error))
            if rejected_request is not None:
                report['rejected_request_context_matches'] = launch_context_matches(report['replay'][-1])
                if not report['rejected_request_context_matches']:
                    report['errors'].append('Rejected launch context diverged from the recorded on-foot state/position')
                frame, timing = control.send(rejected_request)
                report['rejected_request_replay'] = {'request':rejected_request,'tick':frame['tick'],
                                                     'state':frame['state'],'root':frame['root'],
                                                     'velocity':frame['velocity'],'timing':timing}
        report["schedule_delay_ms"] = summary([max(0, event["actual_at_ms"] - event["scheduled_at_ms"]) for event in report["replay"]])
        report["state_mismatches"] = sum(event["state"] != event["expected_state"] for event in report["replay"])
        report["tick_mismatches"] = sum(event["tick"] != event["recorded_tick"] for event in report["replay"])
        report["max_root_error_m"] = max(event["root_error_m"] or 0 for event in report["replay"])
        report["max_velocity_error_mps"] = max(event["velocity_error_mps"] or 0 for event in report["replay"])
    except Exception as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    for drop in report["observed_speed_drops"][:12]:
        if drop["geometry_file"]:
            drop["geometry_context"] = nearby_geometry(args.trace.parent, drop)
    report["ok"] = not report["errors"]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key not in
                     ("replay", "observed_speed_drops", "world_observations", "frame_ground_deviations")}, indent=2))
    print(f"Report: {args.out}")
    return 0 if report["ok"] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, default=PROJECT / "data/diagnostics/v0111/runtime.jsonl")
    parser.add_argument("--host", type=Path, default=PROJECT / "target/debug/skate-schedule")
    parser.add_argument("--out", type=Path, default=PROJECT / "data/runtime-replay-report.json")
    parser.add_argument("--speed", type=float, default=1, help="Recorded clock multiplier; faster replay alters collision-build alignment")
    parser.add_argument("--seconds", type=float, help="Replay only this prefix of the recorded session")
    parser.add_argument("--probe-tick", type=int, help="Isolate a recorded street seam, with an actual raised-curb control")
    parser.add_argument("--approach-tick", type=int, help="Replay a local captured approach with a neutral-right-stick control")
    parser.add_argument("--rejected-request", type=Path, help="Append a frozen on-foot launch step; require matching pre-launch state/position")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()
    self_test()
    if args.self_test:
        print("Trace pairing, inferred request order and missing geometry checks passed.")
        return 0
    if not math.isfinite(args.speed) or args.speed <= 0 or args.seconds is not None and (not math.isfinite(args.seconds) or args.seconds <= 0):
        parser.error("speed and seconds must be positive finite numbers")
    if args.probe_tick is not None and args.approach_tick is not None: parser.error('choose one local probe')
    if args.rejected_request and (args.probe_tick is not None or args.approach_tick is not None or args.seconds is not None):
        parser.error('rejected request requires a full session replay')
    return approach(args) if args.approach_tick is not None else probe(args) if args.probe_tick is not None else run(args)


if __name__ == "__main__":
    raise SystemExit(main())
