<!-- Copyright © 2026 Ömer Göktaş. All Rights Reserved. -->

# llvm-minimal

LLVM's own release builds for Linux and Windows, on AMD64 and ARM64, cut down to the files a C++ build needs. LLVM's archives hold the whole project, about 1.1 GB to download and 12 GB once unpacked on Linux; these hold the compiler, the linker, the archivers and Clang's resource folder alone.

Every file is byte for byte as LLVM built it: nothing is stripped, rebuilt or patched, so each one can be checked against LLVM's own archive of the same version.

## What Each Archive Holds

Each release has one archive a host, `llvm-minimal-<version>-<host>.tar.xz`, with the hosts `linux-amd64`, `linux-arm64`, `windows-amd64` and `windows-arm64`, and a `SHA256SUMS` file. Each archive has one top folder of its own name, which holds:

- **Linux:** `bin/clang` with its other names (`clang++`, `clang-cl`, `clang-cpp`), LLD as `lld` with `ld.lld` and `lld-link`, and `llvm-ar` with `llvm-ranlib` and `llvm-lib`; the other names are symbolic links, as in LLVM's archive.
- **Windows:** `clang-cl.exe`, `lld-link.exe`, `llvm-lib.exe`, `llvm-ar.exe`, `llvm-ranlib.exe`, `llvm-rc.exe` and `llvm-objdump.exe`.
- **Both:** Clang's resource folder, `lib/clang/<major>`, with its headers, its runtime libraries, which the sanitizers need, and the sanitizers' lists; and LLVM's license, `LICENSE.TXT`.

The list itself is `KEEP` in [`trim.py`](trim.py).

## How a Release Is Made

The `Release` workflow, started by hand with LLVM's version:

1. downloads LLVM's archive of each host from [LLVM's GitHub release](https://github.com/llvm/llvm-project/releases) and checks its SHA-256 against the digest GitHub lists for it;
2. keeps only the files `trim.py` names, and packs them;
3. tests each archive on its own host with [`test.py`](test.py), by building and running small programs with it: on Linux with ThinLTO through LLD, a static library and the sanitizers, on Windows with clang-cl, lld-link, llvm-lib and llvm-rc;
4. publishes the archives under the tag `v<version>`, with their checksums and GitHub's attestation of the run that built them, which `gh attestation verify <archive> --repo omergoktas/llvm-minimal` checks.

## License

The archives hold LLVM's work, under the Apache License 2.0 with LLVM Exceptions, whose text each archive carries as `LICENSE.TXT`.
