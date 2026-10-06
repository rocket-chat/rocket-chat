"""Filesystem and resource conversion utilities for DockerSandboxDriver."""

import io
import os
import re
import tarfile
import time


def normalize_workspace_path(path: str, default_root: str = "/workspace") -> str:
    """Canonicalize target path within the container workspace."""
    cleaned = path.strip()
    if not cleaned.startswith("/"):
        cleaned = os.path.join(default_root, cleaned)
    return os.path.normpath(cleaned)


def parse_memory_to_bytes(mem_str: str) -> int:
    """
    Parse human-readable memory string into integer byte count.

    Docker API requires IEC/SI single-letter suffixes ('m', 'g') or raw byte counts;
    standard Kubernetes style suffixes like 'Gi' or 'Mi' cause daemon parse errors.
    """
    cleaned = mem_str.strip()
    match = re.match(r"^(\d+(?:\.\d+)?)\s*([a-zA-Z]*)$", cleaned)
    if not match:
        return 4 * 1024 * 1024 * 1024

    val_str, unit_str = match.groups()
    val = float(val_str)
    units: dict[str, int] = {
        "b": 1,
        "k": 1024,
        "kb": 1000,
        "kib": 1024,
        "m": 1024 * 1024,
        "mb": 1000 * 1000,
        "mi": 1024 * 1024,
        "mib": 1024 * 1024,
        "g": 1024 * 1024 * 1024,
        "gb": 1000 * 1000 * 1000,
        "gi": 1024 * 1024 * 1024,
        "gib": 1024 * 1024 * 1024,
    }
    multiplier = units.get(unit_str.lower(), 1024 * 1024 * 1024)
    return int(val * multiplier)


def create_tar_archive(filename: str, content: bytes) -> bytes:
    """Pack file content into a tar archive bytes buffer for container put_archive."""
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode="w") as tar:
        tarinfo = tarfile.TarInfo(name=filename)
        tarinfo.size = len(content)
        tarinfo.mtime = int(time.time())
        tarinfo.mode = 0o644
        tar.addfile(tarinfo, io.BytesIO(content))
    return tar_stream.getvalue()


def extract_file_from_tar(tar_bytes: bytes, target_filename: str) -> str:
    """Extract and decode UTF-8 file content from a container get_archive tar archive."""
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as tar:
        for member in tar.getmembers():
            if os.path.basename(member.name) == os.path.basename(target_filename):
                extracted = tar.extractfile(member)
                if extracted is not None:
                    return extracted.read().decode("utf-8", errors="replace")
    raise FileNotFoundError(f"Target file {target_filename} not found in archive")


def build_list_files_command(target_dir: str, max_depth: int) -> str:
    """Build a standalone python command to list directory tree with metadata."""
    import base64
    import json

    script = (
        "import json, os, sys\n"
        f"root = {json.dumps(target_dir)}\n"
        f"max_depth = {max_depth}\n"
        "results = []\n"
        "if os.path.exists(root):\n"
        "    if not os.path.isdir(root):\n"
        "        st = os.stat(root)\n"
        "        results.append({'path': root, 'is_dir': False, 'size_bytes': st.st_size, 'modified_at': int(st.st_mtime)})\n"
        "    else:\n"
        "        for dirpath, dirnames, filenames in os.walk(root):\n"
        "            rel = os.path.relpath(dirpath, root)\n"
        "            depth = 0 if rel == '.' else rel.count(os.sep) + 1\n"
        "            if depth >= max_depth:\n"
        "                dirnames.clear()\n"
        "            for d in sorted(dirnames):\n"
        "                full_p = os.path.join(dirpath, d)\n"
        "                try:\n"
        "                    st = os.stat(full_p)\n"
        "                    results.append({'path': full_p, 'is_dir': True, 'size_bytes': st.st_size, 'modified_at': int(st.st_mtime)})\n"
        "                except OSError:\n"
        "                    pass\n"
        "            for f in sorted(filenames):\n"
        "                full_p = os.path.join(dirpath, f)\n"
        "                try:\n"
        "                    st = os.stat(full_p)\n"
        "                    results.append({'path': full_p, 'is_dir': False, 'size_bytes': st.st_size, 'modified_at': int(st.st_mtime)})\n"
        "                except OSError:\n"
        "                    pass\n"
        "print(json.dumps(results))\n"
    )
    b64_script = base64.b64encode(script.encode("utf-8")).decode("ascii")
    return f"python3 -c \"import base64; exec(base64.b64decode('{b64_script}'))\""
