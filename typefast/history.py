"""The last dictations, kept so nothing is ever lost."""

import time

from typefast import config

MAX_ENTRIES = 30


class History:
    def __init__(self, path=None):
        self.path = path or config.HISTORY_FILE
        self.entries: list[dict] = config.read_json(self.path, [])

    def add(self, language: str, inserted: str, clean: str, verbatim: str, live: str) -> dict:
        entry = {
            "time": time.strftime("%Y-%m-%d %H:%M"),
            "language": language,
            "inserted": inserted,
            "clean": clean,
            "verbatim": verbatim,
            "live": live,
        }
        self.entries.insert(0, entry)
        del self.entries[MAX_ENTRIES:]
        self.save()
        return entry

    def save(self) -> None:
        config.write_json(self.path, self.entries, private=True)

    def clear(self) -> None:
        self.entries = []
        config.write_json(self.path, self.entries, private=True)
