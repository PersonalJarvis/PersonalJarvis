"""In-memory SFTP boundary for testing visual work-order transfers."""

from __future__ import annotations

import stat
import time
from types import SimpleNamespace


class FakeSftp:
    def __init__(self):
        self.files = {}
        self.modes = {}
        self.times = {}
        self.corrupt = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def exists(self, path):
        return path in self.modes

    lexists = exists

    async def lstat(self, path):
        return SimpleNamespace(
            permissions=self.modes[path], mtime=self.times.get(path, time.time())
        )

    async def mkdir(self, path, *, attrs):
        self.modes[path] = stat.S_IFDIR | attrs.permissions

    async def chmod(self, path, mode):
        self.modes[path] = stat.S_IFREG | mode

    async def remove(self, path):
        del self.files[path]
        del self.modes[path]

    async def scandir(self, path):
        for name in tuple(self.modes):
            if name.startswith(path + "/"):
                yield SimpleNamespace(
                    filename=name.rsplit("/", 1)[-1], attrs=await self.lstat(name)
                )

    def open(self, path, mode, attrs=None):
        return FakeFile(self, path, mode, attrs)


class FakeFile:
    def __init__(self, sftp, path, mode, attrs):
        self.sftp, self.path = sftp, path
        if mode in {"xb", "wb"}:
            if mode == "xb" and path in sftp.files:
                raise FileExistsError(path)
            sftp.files[path] = b""
            sftp.modes[path] = stat.S_IFREG | attrs.permissions

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def write(self, data):
        self.sftp.files[self.path] += data

    async def read(self):
        return b"corrupt" if self.sftp.corrupt else self.sftp.files[self.path]


class FakeImagePool:
    def __init__(self, sftp):
        self.sftp = sftp

    async def sftp_path(self, path):
        return "/sftp" + path

    async def connection(self):
        return SimpleNamespace(conn=SimpleNamespace(start_sftp_client=lambda: self.sftp))
