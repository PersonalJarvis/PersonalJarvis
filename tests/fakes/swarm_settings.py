"""In-memory write-only credential backend for distributed setup tests."""


class SecretVault:
    def __init__(self):
        self.values = {}
        self.writes = []
        self.fail_key = ""

    def read(self, key):
        return self.values.get(key)

    def write(self, key, value):
        self.writes.append((key, value))
        if self.fail_key and key.endswith(self.fail_key):
            return False
        self.values[key] = value
        return True
