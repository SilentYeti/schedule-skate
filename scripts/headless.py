"""Replay dense collision and controller input through the real engine without Unity or a display."""
from pathlib import Path
import argparse
import json
import math
import socket
import statistics
import subprocess
import time

PROJECT=Path(__file__).resolve().parents[1]

def floor(cells):
    triangles=[]
    for z in range(cells):
        for x in range(cells):
            x0=-24+48*x/cells; x1=-24+48*(x+1)/cells
            z0=-24+48*z/cells; z1=-24+48*(z+1)/cells
            triangles.extend([[[x0,0,z0],[x0,0,z1],[x1,0,z1]],
                              [[x0,0,z0],[x1,0,z1],[x1,0,z0]]])
    return triangles

def summary(values):
    values=sorted(values)
    return {"count":len(values),"median_ms":statistics.median(values),
            "p95_ms":values[min(len(values)-1,int(len(values)*.95))],"max_ms":max(values)}

def camera_metrics(frame):
    camera=frame["camera"]
    # Same +Z spatial column and X reflection used by the adapter.
    forward=[-camera["basis"][6],camera["basis"][7],camera["basis"][8]]
    position=[-camera["position"][0],camera["position"][1],camera["position"][2]]
    target=[-frame["root"][12],frame["root"][13]+1,frame["root"][14]]
    delta=[b-a for a,b in zip(position,target)]
    length=lambda v:math.sqrt(sum(x*x for x in v))
    dot=sum(a*b for a,b in zip(forward,delta))/(length(forward)*length(delta))
    return {"towards_skater_dot":dot,"distance_m":length(delta),"fov":camera["fov"]}

def run(host,cells,refreshes,replay=None):
    log_path=PROJECT/"data/headless-host.log"
    log_path.parent.mkdir(exist_ok=True)
    with log_path.open("w") as log:
        process=subprocess.Popen([str(host),"--assets",str(PROJECT/"data/ps3-assets"),"--listen","127.0.0.1:0"],
                                 stdout=log,stderr=subprocess.PIPE,text=True)
        startup=process.stderr.readline().strip()
        if not startup.startswith("Skate bridge listening on "):
            process.terminate(); process.wait(); raise RuntimeError(startup)
        # Drain stderr to disk so rail/load diagnostics cannot block the engine.
        import threading
        def drain_log():
            for line in process.stderr: log.write(line)
        drain=threading.Thread(target=drain_log,daemon=True)
        drain.start()
        connection=socket.create_connection(("127.0.0.1",int(startup.rsplit(":",1)[1])),timeout=60)
        stream=connection.makefile("rwb")
        phases={}
        def send(request,packed=None):
            start=time.perf_counter()
            payload=json.dumps(request,separators=(",",":"),allow_nan=False).encode()+b"\n"
            encoded=time.perf_counter()
            stream.write(payload); stream.flush()
            if packed is not None: stream.write(packed); stream.flush()
            written=time.perf_counter()
            line=stream.readline(1024*1024+1)
            if not line or len(line)>1024*1024: raise RuntimeError("Missing/oversized response")
            reply=json.loads(line)
            if not reply.get("ok"): raise RuntimeError(reply)
            finished=time.perf_counter()
            phase=phases.setdefault(request["op"],{"encode_ms":[],"write_ms":[],"response_wait_ms":[]})
            for name,value in (("encode_ms",encoded-start),("write_ms",written-encoded),("response_wait_ms",finished-written)):
                phase[name].append(value*1000)
            return reply,(finished-start)*1000
        try:
            small=floor(1)
            headings=[]; frames=[]; rest=None
            step={"op":"step","buttons":4096,"triggers":[0,0],"left":[0,0],"right":[0,0],"ticks":8}
            for heading in (0,math.pi/2,math.pi,-math.pi/2):
                reply,_=send({"op":"activate","triangles":small,"rails":[],"spawn":[0,.3,0],"heading":heading})
                rest=reply.get("rest")
                for _ in range(8): reply,_=send(step)
                metrics=camera_metrics(reply)
                headings.append({"heading":heading,**metrics}); frames.append(reply)
                assert metrics["towards_skater_dot"]>.9,metrics
            packed_activation=None
            if replay and replay.suffix==".skb":
                # Import lazily: riding_checks reuses this module's small floor/timing helpers.
                from riding_checks import read_capture
                activation_request,packed_activation=read_capture(replay)
            else:
                activation_request=json.loads(replay.read_text()) if replay else {"op":"activate","triangles":floor(cells),"rails":[],"spawn":[0,.3,0],"heading":0}
            if activation_request.get("op") not in ("activate","activate_packed"): raise ValueError("Replay must contain an activation request")
            dense=activation_request["triangles"] if packed_activation is None else range(activation_request["count"])
            # Retain the captured camera framing during subsequent steps, including non-16:9 views.
            step["aspect"]=activation_request.get("aspect",16/9)
            reply,activation=send(activation_request,packed_activation)
            timings=[]; updates=[]; completed=[]
            for refresh in range(refreshes):
                request={"op":"collision","triangles":dense,"rails":[]} if packed_activation is None else {"op":"collision_packed","count":len(dense)}
                reply,elapsed=send(request,packed_activation)
                updates.append(elapsed)
                waiting=reply.get("collision_pending",False)
                start=time.perf_counter(); ticks=reply["tick"]; first_step=len(timings)
                # Keep real simulation steps running while a refresh is in flight.
                while True:
                    reply,elapsed=send({**step,"buttons":0})
                    timings.append(elapsed)
                    assert reply["tick"]>ticks
                    ticks=reply["tick"]
                    if not reply.get("collision_pending",False): break
                    if time.perf_counter()-start>30: raise RuntimeError("Collision update never finished")
                    time.sleep(.005)
                completed.append({"queued":waiting,"steps":len(timings)-first_step,"completion_ms":(time.perf_counter()-start)*1000})
            return {"fixture":str(replay) if replay else "deterministic dense floor, not a captured Unity scene","triangles":len(dense),
                    "activation_ms":activation,"collision_reply":summary(updates),"step_reply":summary(timings),
                    "request_phases":{op:{name:summary(values) for name,values in phase.items()} for op,phase in phases.items()},
                    "updates":completed,"camera_headings":headings,"camera_frames":frames,
                    "source_rest":rest,
                    "final_state":reply["state"],"final_position":reply["root"][12:15],
                    "final_camera":camera_metrics(reply)}
        finally:
            stream.close(); connection.close(); process.terminate(); process.wait(timeout=10)
            drain.join(timeout=2); process.stderr.close()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host",type=Path,default=PROJECT/"target/debug/skate-schedule")
    parser.add_argument("--cells",type=int,default=224)
    parser.add_argument("--refreshes",type=int,default=3)
    parser.add_argument("--out",type=Path,default=PROJECT/"data/headless-report.json")
    parser.add_argument("--replay",type=Path,help="Exact captured activation.json or a cropper-generated baked scene request")
    args=parser.parse_args()
    if not 1<=args.cells<=300 or not 1<=args.refreshes<=10: parser.error("cells: 1..300, refreshes: 1..10")
    report=run(args.host.resolve(),args.cells,args.refreshes,args.replay)
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(report,indent=2)+"\n")
    print(json.dumps({k:v for k,v in report.items() if k not in ("camera_frames","source_rest")},indent=2))
    print(f"Report: {args.out}")

if __name__=="__main__": main()
