"""Install the prototype without replacing existing loader files; track copied files for removal."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil

PROJECT = Path(__file__).resolve().parents[1]
DEFAULT_GAME = Path.home()/".local/share/Steam/steamapps/common/Schedule I"

def require_game_closed():
    for process in Path("/proc").glob("[0-9]*/comm"):
        try:
            if process.read_text().strip()=="Schedule I.exe":
                raise RuntimeError("Quit Schedule I normally before installing or launching this prototype.")
        except (FileNotFoundError,PermissionError,ProcessLookupError):
            pass

def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()

def install(game, dry_run):
    require_game_closed()
    game=game.resolve()
    if not (game/"Schedule I.exe").is_file():
        raise ValueError("Select the Schedule I installation directory.")
    manifest=PROJECT/"data/install-manifest.json"
    if manifest.exists():
        raise FileExistsError("This prototype is already installed; uninstall it before reinstalling.")
    copies=[]
    for source in (PROJECT/"vendor/melonloader").rglob("*"):
        if source.is_file():
            copies.append((source,game/source.relative_to(PROJECT/"vendor/melonloader")))
    runtime=PROJECT/"vendor/windows-dotnet6"
    if not (runtime/"host/fxr/6.0.36/hostfxr.dll").is_file():
        raise FileNotFoundError("Run scripts/setup_dev.py to download the Windows runtime.")
    copies.extend((source,game/"MelonLoader/Dependencies/dotnet"/source.relative_to(runtime))
                  for source in runtime.rglob("*") if source.is_file())
    mod=PROJECT/"mod/bin/Debug/net6.0/SkateSchedule.dll"
    if not mod.is_file():
        raise FileNotFoundError("Build mod/SkateSchedule.csproj first.")
    copies.append((mod,game/"Mods/SkateSchedule.dll"))
    for _,destination in copies:
        if destination.exists():
            raise FileExistsError(f"Refusing to replace existing file: {destination}")
    print(f"Copy {len(copies)} loader/runtime files and the compiled mod into {game}")
    print("Skate 3 mode starts enabled and connects when you mount a skateboard; F8 toggles it.")
    if dry_run:
        return
    installed=[]
    manifest.parent.mkdir(parents=True,exist_ok=True)
    # Record each completed copy so an interrupted installation can be removed.
    manifest.write_text(json.dumps({"game":str(game),"files":installed},indent=2)+"\n")
    for source,destination in copies:
        destination.parent.mkdir(parents=True,exist_ok=True)
        with source.open("rb") as source_file,destination.open("xb") as output:
            shutil.copyfileobj(source_file,output)
        installed.append({"path":str(destination.relative_to(game)),"sha256":digest(destination)})
        manifest.write_text(json.dumps({"game":str(game),"files":installed},indent=2)+"\n")
    print(f"Installed. Ownership manifest: {manifest}")

def uninstall(dry_run):
    require_game_closed()
    manifest=PROJECT/"data/install-manifest.json"
    document=json.loads(manifest.read_text())
    game=Path(document["game"]).resolve()
    paths=[]
    for entry in document["files"]:
        path=(game/entry["path"]).resolve()
        if not path.is_relative_to(game):
            raise ValueError("Unsafe path in install manifest.")
        if path.exists() and digest(path)!=entry["sha256"]:
            raise ValueError(f"Installed file changed; preserve it: {path}")
        paths.append(path)
    print(f"Remove {len(paths)} tracked files; generated logs, settings and saves are preserved.")
    if dry_run:
        return
    for path in paths:
        path.unlink(missing_ok=True)
    for parent in sorted({p for path in paths for p in path.parents if p.is_relative_to(game) and p!=game},
                         key=lambda p:len(p.parts),reverse=True):
        try:
            parent.rmdir()
        except OSError:
            pass
    manifest.unlink()

def prepare_interop():
    require_game_closed()
    manifest=PROJECT/"data/install-manifest.json"
    document=json.loads(manifest.read_text())
    game=Path(document["game"])
    generation=json.loads((PROJECT/"data/interop/generation.json").read_text())
    with (game/"GameAssembly.dll").open("rb") as stream:
        current_hash=hashlib.file_digest(stream,"sha512").hexdigest().upper()
    if generation["GameAssemblyHash"]!=current_hash:
        raise ValueError("Schedule I changed. Regenerate data/interop against the new game build.")
    generator=game/"MelonLoader/Dependencies/Il2CppAssemblyGenerator"
    config=generator/"Config.cfg"
    if config.exists():
        (PROJECT/"data/loader-generation-config-before.cfg").write_bytes(config.read_bytes())
    copies=[
        (PROJECT/"vendor/cpp2il/Cpp2IL.exe",generator/"Cpp2IL/Cpp2IL.exe"),
        (PROJECT/"vendor/cpp2il/Plugins/Cpp2IL.Plugin.StrippedCodeRegSupport.dll",
         generator/"Cpp2IL/Plugins/Cpp2IL.Plugin.StrippedCodeRegSupport.dll"),
    ]
    copies.extend((source,generator/"UnityDependencies"/source.name)
                  for source in (PROJECT/"vendor/unity-2022.3.62").glob("*.dll"))
    copies.extend((source,game/"MelonLoader/Il2CppAssemblies"/source.name)
                  for source in (PROJECT/"data/interop").iterdir() if source.suffix in (".dll",".db"))
    existing={entry["path"]:entry for entry in document["files"]}
    for source,destination in copies:
        if not source.is_file():
            raise FileNotFoundError(source)
        if destination.exists() and digest(source)!=digest(destination):
            raise FileExistsError(f"Refusing to replace different cache file: {destination}")
    for source,destination in copies:
        destination.parent.mkdir(parents=True,exist_ok=True)
        if not destination.exists():
            shutil.copyfile(source,destination)
        relative=str(destination.relative_to(game))
        existing[relative]={"path":relative,"sha256":digest(destination)}
    version="2022.1.0-pre-release.21"
    names=sorted(source.name for source in (PROJECT/"data/interop").iterdir() if source.suffix in (".dll",".db"))
    config.write_text('[Il2CppAssemblyGenerator]\n'+
        f'GameAssemblyHash = "{current_hash}"\nUnityVersion = "2022.3.62"\n'+
        f'DumperVersion = "{version}"\nDumperSCRSVersion = "{version}"\n'+
        f'OldFiles = {json.dumps(names)}\n')
    # Loader settings are regenerated/reformatted during play and are preserved on removal.
    existing.pop(str(config.relative_to(game)),None)
    document["files"]=list(existing.values())
    manifest.write_text(json.dumps(document,indent=2)+"\n")
    print(f"Prepared offline references for GameAssembly SHA512 {current_hash[:16]}...")

def copy_collision(game,document):
    source=PROJECT/"data/schedule-collision"
    if not (source/"index.json").is_file():
        raise FileNotFoundError("Run vendor/python/bin/python scripts/export_collision.py first.")
    cache=json.loads((source/"index.json").read_text())
    with (game/"GameAssembly.dll").open("rb") as stream:
        if cache["GameAssemblyHash"]!=hashlib.file_digest(stream,"sha512").hexdigest().upper():
            raise ValueError("Collision cache is for a different game build; export it again.")
    tracked={entry["path"]:entry for entry in document["files"]}
    copies=[(path,game/"Mods/SkateScheduleAssets"/path.name) for path in source.iterdir() if path.is_file()]
    for original,destination in copies:
        relative=str(destination.relative_to(game))
        if destination.exists() and digest(destination)!=digest(original):
            if relative not in tracked or digest(destination)!=tracked[relative]["sha256"]:
                raise ValueError(f"Collision cache file changed externally: {destination}")
    for original,destination in copies:
        destination.parent.mkdir(parents=True,exist_ok=True)
        if not destination.exists() or digest(destination)!=digest(original):
            temporary=destination.with_suffix(destination.suffix+".new")
            with original.open("rb") as input_file,temporary.open("xb") as output:
                shutil.copyfileobj(input_file,output)
            temporary.replace(destination)
        relative=str(destination.relative_to(game))
        tracked[relative]={"path":relative,"sha256":digest(destination)}
    document["files"]=list(tracked.values())
    (PROJECT/"data/install-manifest.json").write_text(json.dumps(document,indent=2)+"\n")
    print(f"Installed {len(cache['meshes'])} cached collision meshes.")

def update_mod():
    require_game_closed()
    manifest=PROJECT/"data/install-manifest.json"
    document=json.loads(manifest.read_text())
    entry=next(e for e in document["files"] if e["path"]=="Mods/SkateSchedule.dll")
    destination=Path(document["game"])/entry["path"]
    if digest(destination)!=entry["sha256"]:
        raise ValueError("The installed mod was changed externally; refusing to replace it.")
    copy_collision(Path(document["game"]),document)
    shutil.copyfile(PROJECT/"mod/bin/Debug/net6.0/SkateSchedule.dll",destination)
    entry=next(e for e in document["files"] if e["path"]=="Mods/SkateSchedule.dll")
    entry["sha256"]=digest(destination)
    manifest.write_text(json.dumps(document,indent=2)+"\n")
    print("Updated the tracked SkateSchedule.dll.")

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--game",type=Path,default=DEFAULT_GAME)
    parser.add_argument("--dry-run",action="store_true")
    actions=parser.add_mutually_exclusive_group()
    actions.add_argument("--uninstall",action="store_true")
    actions.add_argument("--update-mod",action="store_true")
    actions.add_argument("--prepare-interop",action="store_true")
    args=parser.parse_args()
    try:
        if args.prepare_interop:
            if args.dry_run:
                raise ValueError("--dry-run is only supported for install and uninstall.")
            prepare_interop()
        elif args.update_mod:
            if args.dry_run:
                raise ValueError("--dry-run is only supported for install and uninstall.")
            update_mod()
        elif args.uninstall:
            uninstall(args.dry_run)
        else:
            install(args.game,args.dry_run)
            if not args.dry_run:
                prepare_interop()
                state=json.loads((PROJECT/"data/install-manifest.json").read_text())
                copy_collision(Path(state["game"]),state)
    except (OSError,ValueError,RuntimeError) as error:
        parser.exit(1,f"Installation stopped: {error}\n")

if __name__=="__main__":
    main()
