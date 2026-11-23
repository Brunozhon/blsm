from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal

MAX_EDIT_BYTES = 2 * 1024 * 1024
MAX_UPLOAD_BYTES = 256 * 1024 * 1024
CONFIG_SUFFIXES = {".cfg", ".conf", ".json", ".json5", ".properties", ".toml", ".yaml", ".yml"}
BINARY_SUFFIXES = {
    ".7z",
    ".class",
    ".dat",
    ".db",
    ".gif",
    ".gz",
    ".ico",
    ".jar",
    ".jpeg",
    ".jpg",
    ".mca",
    ".mp3",
    ".mp4",
    ".nbt",
    ".ogg",
    ".png",
    ".sqlite",
    ".tar",
    ".webp",
    ".zip",
}
LANGUAGES = {
    ".json": "json",
    ".json5": "json5",
    ".properties": "properties",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
}


class FileManagerError(RuntimeError):
    pass


class FileConflict(FileManagerError):
    pass


class FileTooLarge(FileManagerError):
    pass


class ServerFileManager:
    """Confined filesystem operations for one managed server root."""

    def _root(self, root: Path) -> Path:
        if root.is_symlink() or not root.is_dir():
            raise FileManagerError("The managed server directory is unavailable")
        return root.resolve()

    def _parts(self, relative: str, *, allow_root: bool = True) -> tuple[str, ...]:
        if not relative or relative == ".":
            if allow_root:
                return ()
            raise FileManagerError("Select a file or directory")
        if len(relative) > 1024 or "\x00" in relative or "\\" in relative:
            raise FileManagerError("Invalid server-relative path")
        path = PurePosixPath(relative)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
            raise FileManagerError("Invalid server-relative path")
        return path.parts

    def _path(
        self,
        root: Path,
        relative: str,
        *,
        exists: bool = True,
        allow_root: bool = True,
        allow_final_symlink: bool = False,
    ) -> Path:
        resolved_root = self._root(root)
        parts = self._parts(relative, allow_root=allow_root)
        current = resolved_root
        for index, part in enumerate(parts):
            current /= part
            final = index == len(parts) - 1
            if current.is_symlink():
                if final and allow_final_symlink:
                    break
                raise FileManagerError("Symbolic links cannot be opened in the file manager")
            if not current.exists():
                if exists or not final:
                    raise FileManagerError("File or directory not found")
                break
            resolved = current.resolve()
            if resolved_root != resolved and resolved_root not in resolved.parents:
                raise FileManagerError("Path is outside the managed server directory")
        if exists and not current.exists() and not current.is_symlink():
            raise FileManagerError("File or directory not found")
        return current

    def _relative(self, root: Path, path: Path) -> str:
        value = path.relative_to(self._root(root)).as_posix()
        return value or "."

    def list_directory(self, root: Path, relative: str) -> dict[str, object]:
        directory = self._path(root, relative)
        if not directory.is_dir():
            raise FileManagerError("The requested path is not a directory")
        entries: list[dict[str, object]] = []
        try:
            children = sorted(
                directory.iterdir(),
                key=lambda item: (
                    not stat.S_ISDIR(item.lstat().st_mode),
                    item.name.casefold(),
                ),
            )
            for child in children:
                if child.name.startswith(".blsm-"):
                    continue
                stat_result = child.lstat()
                symlink = stat.S_ISLNK(stat_result.st_mode)
                kind: Literal["directory", "file", "symlink"] = (
                    "symlink"
                    if symlink
                    else "directory"
                    if stat.S_ISDIR(stat_result.st_mode)
                    else "file"
                )
                suffix = child.suffix.lower()
                entries.append(
                    {
                        "name": child.name,
                        "path": self._relative(root, child),
                        "kind": kind,
                        "size": stat_result.st_size if kind == "file" else None,
                        "modified_at": datetime.fromtimestamp(
                            stat_result.st_mtime, timezone.utc
                        ).isoformat(),
                        "editable": kind == "file"
                        and stat_result.st_size <= MAX_EDIT_BYTES
                        and suffix not in BINARY_SUFFIXES,
                        "language": LANGUAGES.get(suffix, "text"),
                        "configuration": kind == "file" and suffix in CONFIG_SUFFIXES,
                    }
                )
        except OSError as exc:
            raise FileManagerError(f"Could not read directory: {exc}") from exc
        return {"path": self._relative(root, directory), "entries": entries}

    def read_text(self, root: Path, relative: str) -> dict[str, object]:
        path = self._path(root, relative, allow_root=False)
        if not path.is_file():
            raise FileManagerError("The requested path is not a file")
        try:
            size = path.stat().st_size
            if size > MAX_EDIT_BYTES:
                raise FileTooLarge("Files larger than 2 MiB cannot be edited in the browser")
            content = path.read_bytes()
        except OSError as exc:
            raise FileManagerError(f"Could not read file: {exc}") from exc
        if b"\x00" in content:
            raise FileManagerError("Binary files cannot be edited in the browser")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise FileManagerError("Only UTF-8 text files can be edited in the browser") from exc
        suffix = path.suffix.lower()
        return {
            "path": self._relative(root, path),
            "content": text,
            "revision": hashlib.sha256(content).hexdigest(),
            "language": LANGUAGES.get(suffix, "text"),
            "configuration": suffix in CONFIG_SUFFIXES,
        }

    def write_text(
        self, root: Path, relative: str, content: str, expected_revision: str
    ) -> dict[str, object]:
        path = self._path(root, relative, allow_root=False)
        if not path.is_file():
            raise FileManagerError("The requested path is not a file")
        encoded = content.encode("utf-8")
        if len(encoded) > MAX_EDIT_BYTES:
            raise FileTooLarge("Files larger than 2 MiB cannot be edited in the browser")
        try:
            current = path.read_bytes()
            if hashlib.sha256(current).hexdigest() != expected_revision:
                raise FileConflict("This file changed on disk. Reload it before saving.")
            mode = path.stat().st_mode & 0o777
            descriptor, temporary = tempfile.mkstemp(prefix=".blsm-edit-", dir=path.parent)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(encoded)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, mode)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        except FileConflict:
            raise
        except OSError as exc:
            raise FileManagerError(f"Could not save file: {exc}") from exc
        return self.read_text(root, relative)

    def create(
        self, root: Path, relative: str, kind: Literal["file", "directory"]
    ) -> dict[str, object]:
        path = self._path(root, relative, exists=False, allow_root=False)
        if path.exists() or path.is_symlink():
            raise FileConflict("A file or directory with that name already exists")
        try:
            if kind == "directory":
                path.mkdir(mode=0o700)
            else:
                path.touch(mode=0o600, exist_ok=False)
        except OSError as exc:
            raise FileManagerError(f"Could not create {kind}: {exc}") from exc
        return {"path": self._relative(root, path), "kind": kind}

    def rename(self, root: Path, relative: str, new_name: str) -> dict[str, str]:
        source = self._path(
            root, relative, allow_root=False, allow_final_symlink=True
        )
        parts = self._parts(new_name, allow_root=False)
        if len(parts) != 1:
            raise FileManagerError("Enter a name, not a path")
        target = self._path(
            root,
            self._relative(root, source.parent / parts[0]),
            exists=False,
            allow_root=False,
        )
        if target.exists() or target.is_symlink():
            raise FileConflict("A file or directory with that name already exists")
        try:
            source.rename(target)
        except OSError as exc:
            raise FileManagerError(f"Could not rename item: {exc}") from exc
        return {"path": self._relative(root, target)}

    def delete(self, root: Path, relative: str) -> None:
        path = self._path(
            root, relative, allow_root=False, allow_final_symlink=True
        )
        try:
            if path.is_symlink() or path.is_file():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
            else:
                raise FileManagerError("Unsupported filesystem entry")
        except FileManagerError:
            raise
        except OSError as exc:
            raise FileManagerError(f"Could not delete item: {exc}") from exc

    def upload(
        self,
        root: Path,
        directory: str,
        filename: str,
        source: BinaryIO,
        overwrite: bool = False,
    ) -> dict[str, object]:
        target_directory = self._path(root, directory)
        if not target_directory.is_dir():
            raise FileManagerError("The upload destination is not a directory")
        parts = self._parts(filename, allow_root=False)
        if len(parts) != 1:
            raise FileManagerError("Invalid upload filename")
        target = self._path(
            root,
            self._relative(root, target_directory / parts[0]),
            exists=False,
            allow_root=False,
        )
        if (target.exists() or target.is_symlink()) and not overwrite:
            raise FileConflict("A file with that name already exists")
        if target.is_dir() or target.is_symlink():
            raise FileConflict("The upload cannot replace this filesystem entry")
        descriptor, temporary = tempfile.mkstemp(prefix=".blsm-upload-", dir=target_directory)
        total = 0
        try:
            with os.fdopen(descriptor, "wb") as handle:
                while chunk := source.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise FileTooLarge("Uploads are limited to 256 MiB")
                    handle.write(chunk)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
        except FileManagerError:
            raise
        except OSError as exc:
            raise FileManagerError(f"Could not upload file: {exc}") from exc
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return {"path": self._relative(root, target), "size": total}

    def download_path(self, root: Path, relative: str) -> Path:
        path = self._path(root, relative, allow_root=False)
        if not path.is_file():
            raise FileManagerError("The requested path is not a file")
        return path
