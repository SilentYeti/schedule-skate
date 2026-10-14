"""Launch the native bridge and Schedule I with the version.dll override for Proton."""
from pathlib import Path
import argparse
import os
import socket
import subprocess
import sys
import time

PROJECT=Path(__file__).resolve().parents[1]
STEAM=Path.home()/".local/share/Steam"
LOADER_FLAGS=["--melonloader.disablestartscreen","--melonloader.hideconsole","--melonloader.agfoffline"]

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
    parser.add_argument("--steam-command",action="store_true",
                        help="Wrap Steam's %%command%%; put this option last in Steam launch options.")
    argv=sys.argv[1:]
    steam_command=None
    if "--steam-command" in argv:
        at=argv.index("--steam-command")
        # Steam's runtime command contains its own -- separator; argparse must not consume it.
        steam_command=argv[at+1:]
        argv=argv[:at+1]
    args,game_args=parser.parse_known_args(argv)
    if args.steam_command:
        if not steam_command or args.bridge_only:
            parser.error("--steam-command requires Steam's full command and cannot be combined with --bridge-only.")
        game_exe=next((Path(part) for part in steam_command if Path(part).name=="Schedule I.exe"),None)
        if game_exe is not None:
            args.game=game_exe.parent
    executable=PROJECT/"target/debug/skate-schedule"
    assets=PROJECT/"data/ps3-assets"
    if not executable.is_file():
        parser.error("Native bridge is missing. Run cargo build --locked in the checkout first.")
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
            environment["WINEDLLOVERRIDES"]=environment.get("WINEDLLOVERRIDES","")+";version=n,b"
            if args.steam_command:
                command=[*steam_command,*LOADER_FLAGS,*game_args]
            else:
                environment.update({
                    "STEAM_COMPAT_CLIENT_INSTALL_PATH":str(STEAM),
                    "STEAM_COMPAT_DATA_PATH":str(STEAM/"steamapps/compatdata/3164500"),
                    "SteamAppId":"3164500","SteamGameId":"3164500",
                })
                command=[str(args.proton),"run",str(args.game/"Schedule I.exe"),*LOADER_FLAGS,*game_args]
                runtime=STEAM/"steamapps/common/SteamLinuxRuntime_sniper/run"
                if runtime.is_file():
                    command=[str(runtime),"--",*command]
            print("Launching Schedule I. Skate 3 connects automatically when you mount a skateboard; F8 toggles it.",flush=True)
            subprocess.run(command,cwd=args.game,env=environment,check=True)
        finally:
            bridge.terminate()
            bridge.wait(timeout=10)

if __name__=="__main__":
    main()
