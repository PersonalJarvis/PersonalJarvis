"""Filesystem sharing failures surrounding a real atomic snapshot rename."""

import os


class DelayedRename:
    def __init__(self, failures, *, competing_target=False, error_type=PermissionError):
        self.rename = os.replace
        self.failures = failures
        self.competing_target = competing_target
        self.error_type = error_type
        self.calls = 0

    def __call__(self, staging, target):
        self.calls += 1
        assert (staging / "object").read_bytes() == b"complete snapshot"
        assert not target.exists()
        if self.calls <= self.failures:
            if self.competing_target:
                target.mkdir()
                (target / "keep").write_bytes(b"other export")
            raise self.error_type("Synthetic filesystem lock")
        self.rename(staging, target)
