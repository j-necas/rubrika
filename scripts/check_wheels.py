#!/usr/bin/env python3
"""Check that the plugin's locked dependencies install from ready-made wheels on every supported platform.

A user's machine must never have to compile a package (Rust, a C compiler): uv would try and fail.
Needs network access and uv 0.9+ (set UV to its path if another uv is first on PATH)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / 'plugin' / 'server'
PYTHON = (SERVER / '.python-version').read_text(encoding='utf-8').strip()
PLATFORMS = ('aarch64-apple-darwin', 'x86_64-apple-darwin', 'x86_64-pc-windows-msvc', 'aarch64-pc-windows-msvc')


def main():
    uv = os.environ.get('UV', 'uv')
    with tempfile.TemporaryDirectory() as tmp:
        requirements = Path(tmp) / 'requirements.txt'
        subprocess.run([uv, 'export', '--frozen', '--no-hashes', '--no-dev', '--no-emit-project', '--quiet',
                        '-o', str(requirements)], cwd=SERVER, check=True)
        failed = []
        for platform in PLATFORMS:
            result = subprocess.run([uv, 'pip', 'install', '--quiet', '--python-platform', platform,
                                     '--python-version', PYTHON, '--only-binary', ':all:',
                                     '--target', str(Path(tmp) / platform), '-r', str(requirements)],
                                    capture_output=True, text=True)
            print(f"{platform}: {'ok' if result.returncode == 0 else 'FAILED'}")
            if result.returncode:
                failed.append(platform)
                print(result.stderr.strip(), file=sys.stderr)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
