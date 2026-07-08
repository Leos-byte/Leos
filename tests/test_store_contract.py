"""Apply the shared RuntimeStore contract to the local store backends."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from typing import Any

from leos_agent.runtime_store import InMemoryRuntimeStore, JsonlRuntimeStore
from leos_agent.sqlite_store import SQLiteRuntimeStore
from tests.store_contract import RuntimeStoreContract


class InMemoryStoreContractTests(RuntimeStoreContract, unittest.TestCase):
    def make_store(self) -> Any:
        return InMemoryRuntimeStore()


class JsonlStoreContractTests(RuntimeStoreContract, unittest.TestCase):
    def make_store(self) -> Any:
        self._root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self._root, ignore_errors=True)
        return JsonlRuntimeStore(self._root)

    def reopen(self, store: Any) -> Any:
        return JsonlRuntimeStore(self._root)


class SQLiteStoreContractTests(RuntimeStoreContract, unittest.TestCase):
    def make_store(self) -> Any:
        self._dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self._dir, ignore_errors=True)
        self._db = self._dir / "runtime.db"
        return SQLiteRuntimeStore(self._db)

    def reopen(self, store: Any) -> Any:
        store.close()
        return SQLiteRuntimeStore(self._db)


if __name__ == "__main__":
    unittest.main()
