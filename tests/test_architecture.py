"""Architectural constraints.

These do not test behaviour — they test SHAPE. The codebase previously had a
database -> failures -> llm -> database import cycle hidden behind four
deferred `import` statements inside functions. That is invisible to normal
tests and only shows up as confusing import errors much later.
"""

from __future__ import annotations

import ast
import pathlib
import subprocess
import sys
import unittest

SERVER = pathlib.Path("server")


def _module_imports(path: pathlib.Path, *, toplevel_only: bool) -> set[str]:
    """Internal `server.*` / `config.*` modules imported by a file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    nodes = tree.body if toplevel_only else ast.walk(tree)
    found: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in ("server", "config"):
                found.add(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in ("server", "config"):
                    found.add(alias.name)
    return found


class TestSqlDialectIsLeaf(unittest.TestCase):
    """The sanitiser must depend on nothing, so it cannot re-form the cycle."""

    def test_no_server_imports_at_all(self):
        found = _module_imports(SERVER / "sql_dialect.py", toplevel_only=False)
        server_deps = {m for m in found if m.startswith("server")}
        self.assertEqual(server_deps, set(),
                         f"sql_dialect must stay a leaf, found: {server_deps}")

    def test_imports_standalone_in_a_clean_interpreter(self):
        """Proves it in a fresh process, not just by reading the AST."""
        code = (
            "from server.sql_dialect import preprocess_sql;"
            "import sys;"
            "mods=[m for m in sys.modules if m.startswith('server.')];"
            "assert mods==['server.sql_dialect'], mods;"
            "print(preprocess_sql('SELECT a FROM t LIMIT 3'))"
        )
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("SELECT TOP 3", out.stdout)


class TestNoImportCycles(unittest.TestCase):

    def test_no_cycle_among_server_modules(self):
        graph = {}
        for path in sorted(SERVER.glob("*.py")):
            if path.name == "__init__.py":
                continue
            name = f"server.{path.stem}"
            graph[name] = {m for m in _module_imports(path, toplevel_only=True)
                           if m.startswith("server.")}

        cycles: list[list[str]] = []

        def walk(node, seen):
            for dep in graph.get(node, ()):
                if dep in seen:
                    cycles.append(seen[seen.index(dep):] + [dep])
                elif dep in graph:
                    walk(dep, seen + [dep])

        for start in graph:
            walk(start, [start])
        self.assertEqual(cycles, [], f"import cycles: {cycles}")


class TestRepairHooks(unittest.TestCase):
    """Hooks are the seam replacing the cycle — they must fail safe."""

    def setUp(self):
        from server import sql_dialect
        self.sd = sql_dialect
        self._saved = list(sql_dialect._REPAIR_HOOKS)
        sql_dialect.clear_repair_hooks()

    def tearDown(self):
        # Restore via the owning module rather than a snapshot: these tests
        # mutate PROCESS-WIDE state, and an incomplete restore silently
        # disabled column repair for every test that ran afterwards.
        self.sd.clear_repair_hooks()
        import server.database
        server.database.install_repair_hooks()

    def test_dialect_works_with_no_hooks(self):
        self.assertEqual(self.sd.preprocess_sql("SELECT a FROM t LIMIT 20"),
                         "SELECT TOP 20 a FROM t")

    def test_hooks_apply_in_order(self):
        self.sd.register_repair_hook(lambda s: s.replace("AAA", "BBB"))
        self.sd.register_repair_hook(lambda s: s.replace("BBB", "CCC"))
        self.assertIn("CCC", self.sd.preprocess_sql("SELECT AAA FROM t"))

    def test_raising_hook_is_skipped(self):
        """A repair failure must never stop SQL that would otherwise run."""
        def boom(_sql):
            raise RuntimeError("repair backend down")

        self.sd.register_repair_hook(boom)
        self.sd.register_repair_hook(lambda s: s.replace("a", "b"))
        self.assertEqual(self.sd.preprocess_sql("SELECT a FROM t"),
                         "SELECT b FROM t")

    def test_database_installs_its_hooks(self):
        import server.database
        self.sd.clear_repair_hooks()
        server.database.install_repair_hooks()
        self.assertGreaterEqual(len(self.sd._REPAIR_HOOKS), 2)

    def test_registration_is_idempotent(self):
        """A double import must not apply the same repair twice."""
        import server.database
        server.database.install_repair_hooks()
        server.database.install_repair_hooks()
        n = len(self.sd._REPAIR_HOOKS)
        server.database.install_repair_hooks()
        self.assertEqual(len(self.sd._REPAIR_HOOKS), n)


if __name__ == "__main__":
    unittest.main(verbosity=2)
