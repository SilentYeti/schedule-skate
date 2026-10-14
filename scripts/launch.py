"""Launch the native bridge and Schedule I with the version.dll override for Proton."""
from pathlib import Path
import argparse
import os
import socket
import subprocess
import time

PROJECT=Path(__file__).resolve().parents[1]
STEAM=Path.home()/".local/share/Steam"

def require_game_closed():
    for process in Path("/proc").glob("[0-9]*/comm"):
        try:
            if process.read_text().strip()=="Schedule I.exe":
                raise RuntimeError("Quit Schedule I normally before installing or launching this prototype.")
        except (FileNotFoundError,PermissionError,ProcessLookupError):
            pass

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-only",action="store_true")
    parser.add_argument("--proton",type=Path,default=STEAM/"steamapps/common/Proton - Experimental/proton")
    parser.add_argument("--game",type=Path,default=STEAM/"steamapps/common/Schedule I")
    args,game_args=parser.parse_known_args()
    executable=PROJECT/"target/debug/skate-schedule"
    assets=PROJECT/"data/ps3-assets"
    command=[str(executable),"--assets",str(assets)]
    if args.bridge_only:
        raise SystemExit(subprocess.call(command))
    try:
        require_game_closed()
    except RuntimeError as error:
        parser.error(str(error))
    if not (args.game/"Mods/SkateSchedule.dll").is_file():
        parser.error("Install the compiled mod with scripts/install_mod.py first.")
    # Never attach to an unknown process already using the bridge port.
    with socket.socket() as probe:
        if probe.connect_ex(("127.0.0.1",31333))==0:
            parser.error("Port 31333 is already in use. Stop the existing bridge before using this launcher.")
    logs=PROJECT/"data/logs"
    logs.mkdir(parents=True,exist_ok=True)
    with (logs/"bridge.log").open("w") as output:
        bridge=subprocess.Popen(command,stdout=output,stderr=subprocess.STDOUT)
        try:
            time.sleep(0.25)
            if bridge.poll() is not None:
                raise RuntimeError(f"The bridge stopped. See {logs/'bridge.log'}")
            environment=os.environ.copy()
            environment.update({
                "STEAM_COMPAT_CLIENT_INSTALL_PATH":str(STEAM),
                "STEAM_COMPAT_DATA_PATH":str(STEAM/"steamapps/compatdata/3164500"),
                "SteamAppId":"3164500","SteamGameId":"3164500",
                "WINEDLLOVERRIDES":environment.get("WINEDLLOVERRIDES","")+";version=n,b",
            })
            command=[str(args.proton),"run",str(args.game/"Schedule I.exe"),
                     "--melonloader.disablestartscreen","--melonloader.hideconsole","--melonloader.agfoffline",*game_args]
            print("Launching Schedule I. Mount a skateboard, then press F8.",flush=True)
            runtime=STEAM/"steamapps/common/SteamLinuxRuntime_sniper/run"
            if runtime.is_file():
                command=[str(runtime),"--",*command]
            subprocess.run(command,cwd=args.game,env=environment,check=True)
        finally:
            bridge.terminate()
            bridge.wait(timeout=10)

if __name__=="__main__":
    main()
