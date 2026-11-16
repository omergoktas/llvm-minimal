# Copyright © 2026 Ömer Göktaş. All Rights Reserved.

"""Checks that a trimmed LLVM compiles, links and packs on its own host.

The script unpacks one archive that trim.py made into a temporary folder and
builds small programs with its files alone, on the system the archive is for:

- on Linux, a C++23 program with ThinLTO through LLD, a static library packed
  by llvm-ar, and a program with AddressSanitizer and UBSan, each of which
  must run and print what it should;
- on Windows, a program that needs no C runtime, compiled by clang-cl with
  ThinLTO and linked by lld-link, which must run and return 0, a static
  library packed by llvm-lib, a resource file compiled by llvm-rc, and the
  program's DLL list read by llvm-objdump. The program avoids the C runtime
  because MSVC's headers and libraries are not part of the archive.

    python3 test.py dist/llvm-minimal-23.1.5-linux-arm64.tar.xz
"""

import platform
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path


def run(*command, cwd):
    print("$", " ".join(str(part) for part in command), flush=True)
    result = subprocess.run([str(part) for part in command], cwd=cwd, capture_output=True, text=True, check=False)
    sys.stdout.write(result.stdout)
    sys.stdout.write(result.stderr)
    if result.returncode != 0:
        sys.exit(f"{Path(command[0]).name} ended with {result.returncode}.")
    return result.stdout


def expect(output, text, what):
    if text not in output:
        sys.exit(f"{what} printed no {text!r}.")


def test_linux(bin, work):
    run(bin / "clang", "--version", cwd=work)
    (work / "hello.cpp").write_text(
        "#include <cstdio>\n#include <string>\n"
        "int answer();\n"
        'int main() { std::string s = "hello " + std::to_string(answer()); std::puts(s.c_str()); }\n'
    )
    (work / "answer.cpp").write_text("int answer() { return 42; }\n")
    run(bin / "clang++", "-std=c++23", "-O2", "-flto=thin", "-c", "answer.cpp", cwd=work)
    run(bin / "llvm-ar", "rcs", "libanswer.a", "answer.o", cwd=work)
    run(bin / "clang++", "-std=c++23", "-O2", "-flto=thin", "-fuse-ld=lld", "hello.cpp", "-L.", "-lanswer", "-o", "hello", cwd=work)
    expect(run(work / "hello", cwd=work), "hello 42", "The ThinLTO program")

    (work / "checked.cpp").write_text(
        "#include <cstdio>\n#include <vector>\n"
        'int main() { std::vector<int> v(4, 1); int s = 0; for (int x : v) s += x; std::printf("sum %d\\n", s); }\n'
    )
    run(bin / "clang++", "-std=c++23", "-g", "-fsanitize=address,undefined", "-fuse-ld=lld", "checked.cpp", "-o", "checked", cwd=work)
    expect(run(work / "checked", cwd=work), "sum 4", "The sanitized program")


def test_windows(bin, work):
    run(bin / "clang-cl.exe", "--version", cwd=work)
    (work / "start.c").write_text("int answer(void);\nint start(void) { return answer() - 42; }\n")
    (work / "answer.c").write_text("int answer(void) { return 42; }\n")
    run(bin / "clang-cl.exe", "/nologo", "/c", "/O2", "/GS-", "-flto=thin", "answer.c", cwd=work)
    run(bin / "llvm-lib.exe", "/nologo", "/out:answer.lib", "answer.obj", cwd=work)
    run(bin / "clang-cl.exe", "/nologo", "/c", "/O2", "/GS-", "-flto=thin", "start.c", cwd=work)
    (work / "strings.rc").write_text('STRINGTABLE\nBEGIN\n  1 "llvm-minimal"\nEND\n')
    run(bin / "llvm-rc.exe", "/fo", "strings.res", "strings.rc", cwd=work)
    run(
        bin / "lld-link.exe",
        "/nologo",
        "/nodefaultlib",
        "/entry:start",
        "/subsystem:console",
        "/out:start.exe",
        "start.obj",
        "answer.lib",
        "strings.res",
        cwd=work,
    )
    run(work / "start.exe", cwd=work)
    run(bin / "llvm-objdump.exe", "-p", "start.exe", cwd=work)


def main():
    archive = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as temp:
        temp = Path(temp)
        with tarfile.open(archive) as tar:
            tar.extractall(temp, filter="tar")
        (top,) = temp.iterdir()
        work = temp / "work"
        work.mkdir()
        if platform.system() == "Windows":
            test_windows(top / "bin", work)
        else:
            test_linux(top / "bin", work)
    print(f"{archive.name} passed.")


if __name__ == "__main__":
    main()
