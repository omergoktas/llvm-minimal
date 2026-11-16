# Copyright © 2026 Ömer Göktaş. All Rights Reserved.

"""Cuts one host's LLVM release down to the files a C++ build needs.

The script downloads LLVM's own archive for one host from LLVM's GitHub
release and checks its SHA-256 against the digest GitHub lists for that file.
It then unpacks only the files that KEEP names, adds LLVM's license, and packs
them as llvm-minimal-<version>-<host>.tar.xz in the output folder, under one
top folder of the same name. Every kept file stays byte for byte as LLVM built
it: nothing is stripped, rebuilt or patched.

    python3 trim.py --version 23.1.5 --host linux-arm64 --out dist

It needs the zstd, tar and xz programs, as an Ubuntu runner of GitHub has
them. GH_TOKEN, when set, is sent to GitHub's API, which then allows more
requests an hour.
"""

import argparse
import fnmatch
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

RELEASE_API = "https://api.github.com/repos/llvm/llvm-project/releases/tags/llvmorg-{version}"
LICENSE_URL = "https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-{version}/llvm/LICENSE.TXT"

# LLVM's archive for each host, as its release names it.
ARCHIVES = {
    "linux-amd64": "LLVM-{version}-Linux-X64.tar.zst",
    "linux-arm64": "LLVM-{version}-Linux-ARM64.tar.zst",
    "windows-amd64": "clang+llvm-{version}-x86_64-pc-windows-msvc.tar.zst",
    "windows-arm64": "clang+llvm-{version}-aarch64-pc-windows-msvc.tar.zst",
}

# Clang's resource folder, lib/clang/<major>: its headers, which every
# compile reads, its runtime libraries, which sanitizers and some built-in
# functions link, and the lists the sanitizers read. Its Fortran headers
# (finclude) and its bin folder are left out.
RESOURCES = ["lib/clang/*/include/*", "lib/clang/*/lib/*", "lib/clang/*/share/*"]

# The files kept for each system, as patterns relative to the archive's top
# folder, where * also matches a slash. A pattern in OPTIONAL may match
# nothing; every other one must match a file, or the script stops, so a
# change in LLVM's layout cannot pass unseen.
KEEP = {
    # On Linux LLVM's programs are single files with symbolic links for their
    # other names, which cost nothing to keep. clang drives every compile, LLD
    # links (ld.lld; LTO needs it, since GNU ld cannot read Clang's LTO
    # objects), and llvm-ar packs static libraries, LTO's included.
    "linux": [
        "bin/clang",
        "bin/clang-[0-9]*",
        "bin/clang++",
        "bin/clang-cl",
        "bin/clang-cpp",
        "bin/lld",
        "bin/ld.lld",
        "bin/lld-link",
        "bin/llvm-ar",
        "bin/llvm-ranlib",
        "bin/llvm-lib",
        *RESOURCES,
    ],
    # On Windows every name is a copy of its program, so only the names an
    # MSVC-style build calls are kept: clang-cl compiles, lld-link links,
    # llvm-lib packs static libraries, llvm-ar and llvm-ranlib are the same
    # program under the names CMake also looks for, llvm-rc compiles resource
    # files where a build chooses it over the SDK's rc, and llvm-objdump lists
    # a binary's DLLs where MSVC's dumpbin is not at hand.
    "windows": [
        "bin/clang-cl.exe",
        "bin/lld-link.exe",
        "bin/llvm-lib.exe",
        "bin/llvm-ar.exe",
        "bin/llvm-ranlib.exe",
        "bin/llvm-rc.exe",
        "bin/llvm-objdump.exe",
        *RESOURCES,
    ],
}
# LLVM's Linux archives have no clang-<major> and its Windows archives no
# share folder, whose lists only the Linux sanitizers read.
OPTIONAL = {"bin/clang-[0-9]*", "lib/clang/*/share/*"}


def request(url):
    headers = {"User-Agent": "llvm-minimal"}
    token = os.environ.get("GH_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = f"Bearer {token}"
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=60)


def release_asset(version, name):
    """Returns the download address of a file of LLVM's release and the
    SHA-256 GitHub lists for it."""
    with request(RELEASE_API.format(version=version)) as answer:
        release = json.load(answer)
    for asset in release["assets"]:
        if asset["name"] == name:
            digest = asset.get("digest") or ""
            if not digest.startswith("sha256:"):
                sys.exit(f"GitHub lists no SHA-256 for {name}.")
            return asset["browser_download_url"], digest.removeprefix("sha256:")
    sys.exit(f"LLVM's {version} release has no file named {name}.")


def download(url, path, sha256):
    """Downloads a file and stops when its SHA-256 differs from the one given."""
    digest = hashlib.sha256()
    with request(url) as answer, open(path, "wb") as f:
        while piece := answer.read(1 << 20):
            digest.update(piece)
            f.write(piece)
    if digest.hexdigest() != sha256:
        sys.exit(f"{url} has the SHA-256 {digest.hexdigest()}, but GitHub lists {sha256}.")


def unpack_kept(archive, patterns, folder):
    """Unpacks from LLVM's archive only the members the patterns match,
    without its top folder, and returns how many members each pattern
    matched. zstd decompresses the archive into tarfile as one stream, so
    the whole archive is read once and only the kept files are written.
    LLVM compresses its archives with zstd's long mode, whose window is
    larger than zstd allows by default, so --long=31 lets zstd use up to
    2 GB for it."""
    matched = dict.fromkeys(patterns, 0)
    zstd = subprocess.Popen(["zstd", "-dc", "--long=31", str(archive)], stdout=subprocess.PIPE)
    with tarfile.open(fileobj=zstd.stdout, mode="r|") as tar:
        for member in tar:
            parts = member.name.split("/", 1)
            if len(parts) < 2 or member.isdir():
                continue
            hits = [pattern for pattern in patterns if fnmatch.fnmatchcase(parts[1], pattern)]
            if not hits:
                continue
            for pattern in hits:
                matched[pattern] += 1
            member.name = parts[1]
            if member.islnk():
                # A hard link names its target with the top folder too
                member.linkname = member.linkname.split("/", 1)[1]
            tar.extract(member, folder, filter="tar")
    if zstd.wait() != 0:
        sys.exit(f"zstd could not decompress {archive.name}.")
    return matched


def check_links(folder):
    """Stops when a kept symbolic link points at a file that was not kept."""
    for path in folder.rglob("*"):
        if path.is_symlink() and not path.resolve().is_relative_to(folder.resolve()):
            sys.exit(f"{path.relative_to(folder)} points outside the kept files.")
        if path.is_symlink() and not path.exists():
            sys.exit(f"{path.relative_to(folder)} points at {os.readlink(path)}, which was not kept.")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--version", required=True, help="LLVM's version, such as 23.1.5")
    parser.add_argument("--host", required=True, choices=sorted(ARCHIVES), help="the system the toolchain runs on")
    parser.add_argument("--out", required=True, type=Path, help="the folder the archive goes into")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        sys.exit(f"{args.version} is not an LLVM version such as 23.1.5.")

    name = f"llvm-minimal-{args.version}-{args.host}"
    patterns = KEEP[args.host.split("-")[0]]
    args.out.mkdir(parents=True, exist_ok=True)
    # Packing runs in the temporary folder, so the result needs a full path
    args.out = args.out.resolve()
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        source = ARCHIVES[args.host].format(version=args.version)
        url, sha256 = release_asset(args.version, source)
        print(f"Downloading {source}", flush=True)
        download(url, temp / source, sha256)
        print(f"{source} matches the SHA-256 that GitHub lists, {sha256}", flush=True)

        folder = temp / name
        matched = unpack_kept(temp / source, patterns, folder)
        missing = [pattern for pattern, count in matched.items() if not count and pattern not in OPTIONAL]
        if missing:
            sys.exit(f"{source} has no file for {', '.join(missing)}.")
        check_links(folder)
        with request(LICENSE_URL.format(version=args.version)) as answer:
            (folder / "LICENSE.TXT").write_bytes(answer.read())
        (temp / source).unlink()

        # The same files give the same archive: sorted names, no owner, and
        # LLVM's own file times.
        result = args.out / f"{name}.tar.xz"
        packed = subprocess.run(
            f'tar --sort=name --owner=0 --group=0 --numeric-owner -cf - "{name}" | xz -T0 -9e > "{result}"',
            shell=True,
            cwd=temp,
            check=False,
        )
        if packed.returncode != 0:
            sys.exit(f"tar or xz could not pack {name}.")
        size = sum(path.stat().st_size for path in folder.rglob("*") if path.is_file() and not path.is_symlink())
        shutil.rmtree(folder)
    for pattern, count in matched.items():
        print(f"  {count:5} {pattern}")
    print(f"{result.name}: {result.stat().st_size / 1e6:.0f} MB, {size / 1e6:.0f} MB unpacked")


if __name__ == "__main__":
    main()
