"""Download development tools locally; no game or system installation is changed."""
from pathlib import Path
import hashlib
import json
import tarfile
import subprocess
import sys
import urllib.request
import zipfile

PROJECT = Path(__file__).resolve().parents[1]
VENDOR = PROJECT / "vendor"
HEADERS = {"User-Agent": "skate-schedule/0.1"}


def download(url, target, sha512=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if sha512 and hashlib.file_digest(target.open("rb"), "sha512").hexdigest() != sha512:
            raise ValueError(f"Existing download has wrong hash: {target}")
        return
    temporary = target.with_suffix(target.suffix + ".partial")
    request = urllib.request.Request(url, headers=HEADERS)
    digest = hashlib.sha512()
    with urllib.request.urlopen(request, timeout=60) as response, temporary.open("xb") as output:
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            output.write(chunk)
    if sha512 and digest.hexdigest() != sha512:
        raise ValueError(f"Download hash mismatch: {target}")
    temporary.rename(target)


def apply_floor_seam_patch(destination):
    patch=PROJECT/"patches/skate-core-floor-seams.patch"
    command=["git","-C",str(destination),"apply"]
    if subprocess.run([*command,"--reverse","--check",str(patch)],capture_output=True).returncode==0:
        return
    subprocess.run([*command,"--check",str(patch)],check=True)
    subprocess.run([*command,str(patch)],check=True)


def main():
    repositories = (
        ("mashup", "https://github.com/Kimmo3223/2010-rust-rewrite-mashup.git",
         "099b1948e9c08394b20c760ef123da38ef347278", ["skate"]),
        ("engine", "https://github.com/SK8-ENGINE/skate-3-rust-engine.git",
         "cb7968930f14dad38457e98720d1a274e469eec2", ["tools/asset_pipeline", "tools/owned_game"]),
    )
    for name, url, revision, sparse in repositories:
        destination = VENDOR / name
        if not destination.exists():
            subprocess.run(["git", "clone", "--filter=blob:none", "--no-checkout", url, str(destination)], check=True)
            subprocess.run(["git", "-C", str(destination), "sparse-checkout", "set", *sparse], check=True)
            subprocess.run(["git", "-C", str(destination), "checkout", revision], check=True)
        actual = subprocess.check_output(["git", "-C", str(destination), "rev-parse", "HEAD"], text=True).strip()
        if actual != revision:
            raise ValueError(f"Unexpected vendor revision in {destination}: {actual}")
    apply_floor_seam_patch(VENDOR/"mashup")
    sdk = VENDOR / "dotnet"
    if not (sdk / "dotnet").is_file():
        with urllib.request.urlopen("https://builds.dotnet.microsoft.com/dotnet/release-metadata/10.0/releases.json", timeout=30) as response:
            metadata = json.load(response)
        release = next(r for r in metadata["releases"] if r["sdk"]["version"] == metadata["latest-sdk"])
        package = next(f for f in release["sdk"]["files"] if f["rid"] == "linux-x64")
        archive = VENDOR / "downloads" / Path(package["url"]).name
        print(f"Downloading .NET SDK {release['sdk']['version']}", flush=True)
        download(package["url"], archive, package["hash"])
        sdk.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(sdk, filter="data")
    loader = VENDOR / "melonloader"
    if not (loader / "version.dll").is_file():
        archive = VENDOR / "downloads/MelonLoader.v0.7.3.x64.zip"
        download("https://github.com/LavaGang/MelonLoader/releases/download/v0.7.3/MelonLoader.x64.zip", archive)
        loader.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                path = (loader / entry.filename).resolve()
                if not path.is_relative_to(loader.resolve()) or (entry.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Unsafe loader archive path")
            z.extractall(loader)
    cpp2il = VENDOR / "cpp2il/Cpp2IL"
    download("https://github.com/SamboyCoding/Cpp2IL/releases/download/2022.1.0-pre-release.21/Cpp2IL-2022.1.0-pre-release.21-Linux", cpp2il)
    cpp2il.chmod(0o755)
    release_url = "https://github.com/SamboyCoding/Cpp2IL/releases/download/2022.1.0-pre-release.21/"
    download(release_url+"Cpp2IL-2022.1.0-pre-release.21-Windows.exe", VENDOR/"cpp2il/Cpp2IL.exe")
    download(release_url+"Cpp2IL.Plugin.StrippedCodeRegSupport.dll",
             VENDOR/"cpp2il/Plugins/Cpp2IL.Plugin.StrippedCodeRegSupport.dll")
    unity = VENDOR / "unity-2022.3.62"
    if not unity.is_dir():
        archive = VENDOR / "downloads/Unity.2022.3.62.Managed.zip"
        download("https://github.com/LavaGang/MelonLoader.UnityDependencies/releases/download/2022.3.62/Managed.zip", archive)
        unity.mkdir()
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                if not (unity / entry.filename).resolve().is_relative_to(unity.resolve()):
                    raise ValueError("Unsafe Unity library path")
            z.extractall(unity)
    runtime = VENDOR / "windows-dotnet6"
    if not (runtime / "host/fxr/6.0.36/hostfxr.dll").is_file():
        with urllib.request.urlopen("https://builds.dotnet.microsoft.com/dotnet/release-metadata/6.0/releases.json", timeout=30) as response:
            metadata = json.load(response)
        release = next(r for r in metadata["releases"] if r["runtime"]["version"] == "6.0.36")
        package = next(f for f in release["runtime"]["files"] if f["rid"] == "win-x64" and f["name"].endswith(".zip"))
        archive = VENDOR / "downloads" / Path(package["url"]).name
        download(package["url"], archive, package["hash"])
        runtime.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                if not (runtime / entry.filename).resolve().is_relative_to(runtime.resolve()):
                    raise ValueError("Unsafe runtime archive path")
            z.extractall(runtime)
    python=VENDOR/"python/bin/python"
    if not python.is_file():
        subprocess.run([sys.executable,"-m","venv","--system-site-packages",str(VENDOR/"python")],check=True)
    subprocess.run([str(python),"-m","pip","install","UnityPy==1.25.4","scipy==1.18.1"],check=True)
    print("Development tools, collision exporter and Unity reference libraries are ready in vendor/.")
    print("MelonLoader has not been installed into Schedule I.")


if __name__ == "__main__":
    main()
