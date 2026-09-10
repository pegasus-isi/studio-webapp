"""Reconcile an SSH snapshot without overwriting edits in the working mirror.

The snapshot is never exposed as an editable workspace. A persistent baseline
records the last content shared by both sides; changes on both sides are a
conflict, not permission for rsync to pick a winner. Only local changes are
uploaded, so a stale copy of a running workflow's database is never sent back.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile


class MirrorState:
    def __init__(self, workspace: str, state_dir: str):
        self.workspace = Path(workspace)
        self.state_dir = Path(state_dir)
        self.snapshot = self.state_dir / "remote"
        self.journal = self.state_dir / "baseline.json"
        self._cache: dict[str, tuple[tuple, str]] = {}
        self.baseline: dict[str, str] = {}
        if self.journal.exists():
            # A corrupt journal must fail closed, not bless unknown local files
            # as copies of the remote and overwrite them.
            self.baseline = json.loads(self.journal.read_text())
            if not isinstance(self.baseline, dict):
                raise ValueError("Invalid mirror baseline")
        self.conflicts: list[str] = []

    def _fingerprint(self, path: Path) -> str:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            return "link:" + os.readlink(path)
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(f"Unsupported workspace file: {path.name}")
        stamp = (info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_mode)
        cached = self._cache.get(str(path))
        if cached and cached[0] == stamp:
            return cached[1]
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        value = f"file:{stat.S_IMODE(info.st_mode)}:{digest}"
        self._cache[str(path)] = (stamp, value)
        return value

    def inventory(self, root: Path) -> dict[str, str]:
        entries = {}
        for parent, dirs, files in os.walk(root, followlinks=False):
            for name in list(dirs):
                if (Path(parent) / name).is_symlink():
                    dirs.remove(name)
                    files.append(name)
            for name in files:
                if name.endswith(".sif"):
                    continue
                path = Path(parent) / name
                try:
                    entries[path.relative_to(root).as_posix()] = self._fingerprint(path)
                except FileNotFoundError:
                    continue  # editor's atomic save raced the walk; next sync sees it
        return entries

    def save(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temp = self.journal.with_suffix(".tmp")
        temp.write_text(json.dumps(self.baseline, sort_keys=True))
        os.replace(temp, self.journal)

    def _safe_parent(self, relative: str) -> bool:
        parent = self.workspace
        for part in Path(relative).parts[:-1]:
            parent /= part
            if parent.is_symlink() or (parent.exists() and not parent.is_dir()):
                return False
        return True

    def reconcile(self) -> list[str]:
        local, remote = self.inventory(self.workspace), self.inventory(self.snapshot)
        conflicts = []
        for name in sorted(local.keys() | remote.keys() | self.baseline.keys()):
            here, there, before = local.get(name), remote.get(name), self.baseline.get(name)
            if here == there:
                if there is None:
                    self.baseline.pop(name, None)
                else:
                    self.baseline[name] = there
                continue
            if there == before:
                continue  # only the working copy changed, including local deletions
            if here != before or not self._safe_parent(name):
                conflicts.append(name)
                continue
            target = self.workspace / name
            if target.is_dir() and not target.is_symlink():
                conflicts.append(name)
                continue
            if there is None:
                current = self._fingerprint(target) if target.exists() or target.is_symlink() else None
                if current != here:
                    conflicts.append(name)
                    continue
                target.unlink(missing_ok=True)
                self.baseline.pop(name, None)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = self.snapshot / name
            fd, temp = tempfile.mkstemp(prefix=".studio-sync-", dir=target.parent)
            os.close(fd)
            try:
                if source.is_symlink():
                    os.unlink(temp)
                    os.symlink(os.readlink(source), temp)
                else:
                    shutil.copy2(source, temp)
                # Do not replace an editor save that happened during the copy.
                current = self._fingerprint(target) if target.exists() or target.is_symlink() else None
                if current != here:
                    conflicts.append(name)
                    continue
                os.replace(temp, target)
                self.baseline[name] = there
            finally:
                if os.path.lexists(temp):
                    os.unlink(temp)
        self.conflicts = conflicts
        self.save()
        return conflicts

    def changes(self, project: str) -> tuple[list[str], list[str]]:
        relative = Path(project).relative_to(self.workspace)
        prefix = "" if relative == Path(".") else relative.as_posix().rstrip("/") + "/"
        conflicts = [p for p in self.conflicts if p.startswith(prefix)]
        if conflicts:
            raise RuntimeError("Resolve local/remote file conflicts before running: " + ", ".join(conflicts[:5]))
        local = self.inventory(self.workspace)
        changed = [p for p, value in local.items() if p.startswith(prefix) and value != self.baseline.get(p)]
        deleted = [p for p in self.baseline if p.startswith(prefix) and p not in local]
        return sorted(changed), sorted(deleted)

    def acknowledge(self, uploaded: dict[str, str], deleted: list[str]):
        # Record the uploaded snapshot, not the possibly edited working copy.
        self.baseline.update(uploaded)
        for name in deleted:
            self.baseline.pop(name, None)
        self.save()

    def forget(self, path: str):
        prefix = Path(path).relative_to(self.workspace).as_posix().rstrip("/") + "/"
        self.baseline = {k: v for k, v in self.baseline.items() if not k.startswith(prefix)}
        self.save()
