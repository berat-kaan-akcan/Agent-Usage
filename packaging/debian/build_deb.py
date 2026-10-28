#!/usr/bin/env python3
"""Zero-dependency Debian (.deb) package builder for Agent Usage Widget.

Constructs a standard Debian binary archive (`ar` format with `debian-binary`,
`control.tar.gz`, and `data.tar.gz`) without requiring `dpkg-deb` to be installed.
"""

from __future__ import annotations

import hashlib
import io
import os
import shutil
import stat
import subprocess
import tarfile
import time
from pathlib import Path


def make_ar_entry(name: str, data: bytes, mtime: int) -> bytes:
    """Create a standard 60-byte UNIX `ar` header followed by 2-byte aligned payload."""
    ident = f"{name:<16}".encode("ascii")[:16]
    timestamp = f"{mtime:<12}".encode("ascii")[:12]
    owner = f"{0:<6}".encode("ascii")[:6]
    group = f"{0:<6}".encode("ascii")[:6]
    mode = f"{100644:<8}".encode("ascii")[:8]
    size = f"{len(data):<10}".encode("ascii")[:10]
    magic = b"`\n"
    header = ident + timestamp + owner + group + mode + size + magic
    pad = b"\n" if (len(data) % 2 == 1) else b""
    return header + data + pad


def build_deb(repo_root: Path, out_dir: Path, version: str = "1.0.0") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    native_bin = repo_root / "build" / "agent-usage-qt"
    if not native_bin.is_file():
        subprocess.run(
            ["cmake", "-B", str(repo_root / "build"), "-S", str(repo_root / "native-qt"), "-GNinja"],
            check=True,
        )
        subprocess.run(["cmake", "--build", str(repo_root / "build")], check=True)

    mtime = int(time.time())
    data_buf = io.BytesIO()
    md5_lines = []
    installed_bytes = 0

    def add_file_to_tar(tf: tarfile.TarFile, arcPath: str, content: bytes, executable: bool = False) -> None:
        nonlocal installed_bytes
        installed_bytes += len(content)
        info = tarfile.TarInfo(name=f"./{arcPath.lstrip('/')}")
        info.size = len(content)
        info.mtime = mtime
        info.uid = 0
        info.gid = 0
        info.uname = "root"
        info.gname = "root"
        info.mode = 0o755 if executable else 0o644
        tf.addfile(info, io.BytesIO(content))
        digest = hashlib.md5(content).hexdigest()
        md5_lines.append(f"{digest}  {arcPath.lstrip('/')}")

    with tarfile.open(fileobj=data_buf, mode="w:gz") as data_tar:
        # 1. Native Qt6 binary
        add_file_to_tar(
            data_tar,
            "usr/bin/agent-usage-qt",
            native_bin.read_bytes(),
            executable=True,
        )
        # 2. CLI and Widget wrapper scripts
        cli_wrapper = (
            "#!/usr/bin/env bash\n"
            'export PYTHONPATH="/usr/lib/agent-usage-widget/src:${PYTHONPATH}"\n'
            'exec python3 -m agent_usage.cli "$@"\n'
        ).encode("utf-8")
        add_file_to_tar(data_tar, "usr/bin/agent-usage", cli_wrapper, executable=True)

        widget_wrapper = (
            "#!/usr/bin/env bash\n"
            'export PYTHONPATH="/usr/lib/agent-usage-widget/src:${PYTHONPATH}"\n'
            'exec python3 -m agent_usage.main "$@"\n'
        ).encode("utf-8")
        add_file_to_tar(data_tar, "usr/bin/agent-usage-widget", widget_wrapper, executable=True)

        # 3. Python package files
        src_pkg = repo_root / "src" / "agent_usage"
        for pyfile in sorted(src_pkg.rglob("*.py")):
            rel = pyfile.relative_to(repo_root / "src")
            add_file_to_tar(
                data_tar,
                f"usr/lib/agent-usage-widget/src/{rel.as_posix()}",
                pyfile.read_bytes(),
                executable=False,
            )

        # 4. Desktop entry, icon, license
        add_file_to_tar(
            data_tar,
            "usr/share/applications/agent-usage-widget.desktop",
            (repo_root / "packaging" / "linux" / "agent-usage-widget.desktop").read_bytes(),
        )
        add_file_to_tar(
            data_tar,
            "usr/share/icons/hicolor/scalable/apps/agent-usage-widget.svg",
            (repo_root / "packaging" / "linux" / "agent-usage-widget.svg").read_bytes(),
        )
        add_file_to_tar(
            data_tar,
            "usr/share/doc/agent-usage-widget/copyright",
            (repo_root / "LICENSE").read_bytes(),
        )

    control_content = (
        "Package: agent-usage-widget\n"
        f"Version: {version}-1\n"
        "Section: utils\n"
        "Priority: optional\n"
        "Architecture: amd64\n"
        f"Installed-Size: {max(1, installed_bytes // 1024)}\n"
        "Depends: python3 (>= 3.10), libqt6widgets6 | qt6-base-dev\n"
        "Recommends: python3-gi, gir1.2-ayatanaappindicator3-0.1\n"
        "Maintainer: Agent Usage Contributors <noreply@example.com>\n"
        "Homepage: https://github.com/berat-kaan-akcan/Agent-Usage\n"
        "Description: Cross-platform AI Coding Usage Desktop Widget\n"
        " Tracks Claude Code, OpenAI Codex, and Google Antigravity local token usage\n"
        " and verified account/model quotas in one compact desktop widget.\n"
    ).encode("utf-8")

    md5sums_content = ("\n".join(md5_lines) + "\n").encode("utf-8")

    control_buf = io.BytesIO()
    with tarfile.open(fileobj=control_buf, mode="w:gz") as ctrl_tar:
        for cname, cbytes in [("control", control_content), ("md5sums", md5sums_content)]:
            cinfo = tarfile.TarInfo(name=f"./{cname}")
            cinfo.size = len(cbytes)
            cinfo.mtime = mtime
            cinfo.uid = 0
            cinfo.gid = 0
            cinfo.uname = "root"
            cinfo.gname = "root"
            cinfo.mode = 0o644
            ctrl_tar.addfile(cinfo, io.BytesIO(cbytes))

    deb_path = out_dir / f"agent-usage-widget_{version}-1_amd64.deb"
    with deb_path.open("wb") as f:
        f.write(b"!<arch>\n")
        f.write(make_ar_entry("debian-binary", b"2.0\n", mtime))
        f.write(make_ar_entry("control.tar.gz", control_buf.getvalue(), mtime))
        f.write(make_ar_entry("data.tar.gz", data_buf.getvalue(), mtime))

    return deb_path


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    out = root / "dist"
    deb_file = build_deb(root, out)
    print(f"Built Debian package: {deb_file} ({deb_file.stat().st_size} bytes)")
