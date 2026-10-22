"""Self-tests for repo_hygiene_check.py. Standard library only.

Run:  python -I -B -m unittest discover -s scripts -p "test_repo_hygiene_check.py" -v

Environment (optional):
  REPO_HYGIENE_CHECKER  path of the checker under test (default: the sibling repo_hygiene_check.py);
                        the mutation harness points this at each mutant.
  REPO_HYGIENE_SCRATCH  directory for fixture repositories (default: a folder next to this file).
Fixture folders are made with os.makedirs and removed with shutil.rmtree(ignore_errors=True).
"""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
CHECKER = os.path.abspath(os.environ.get("REPO_HYGIENE_CHECKER") or os.path.join(HERE, "repo_hygiene_check.py"))
SCRATCH = os.path.abspath(os.environ.get("REPO_HYGIENE_SCRATCH") or os.path.join(HERE, "_hygiene_scratch"))
TOKEN = "[[" + "FILL"

README = """# Demo repository

## Scope and data boundary

This repository is not a clinical tool. It uses synthetic data only and must never receive patient data.

## Purpose

Computes a toy statistic on generated numbers.

## One-command reproduction

```bash
python -I -B scripts/reproduce.py
```

## Limitations

- Tested only on generated data.

## Evidence labels

MEASURED, REPORTED, MODELED, UNKNOWN, CONJECTURE.
"""

CITATION = """cff-version: 1.2.0
message: "If you use this software, please cite it using the metadata from this file."
type: software
title: "Demo repository"
authors:
  - family-names: "Doe"
    given-names: "Jane"
    affiliation: "SZL Holdings"
license: Apache-2.0
version: "0.1.0"
date-released: "2026-10-21"
"""

LICENSE = "Apache License\nVersion 2.0, January 2004\nhttp://www.apache.org/licenses/\n\nTERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION\n"

PYPROJECT = """[project]
name = "demo"
version = "0.1.0"
dependencies = ["requests>=2.31"]
"""


def load_module():
    spec = importlib.util.spec_from_file_location("rhc_under_test", CHECKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Fixture(unittest.TestCase):
    def setUp(self):
        self.root = os.path.join(SCRATCH, f"{os.getpid()}_{self._testMethodName}")
        shutil.rmtree(self.root, ignore_errors=True)
        os.makedirs(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        try:
            os.rmdir(SCRATCH)
        except OSError:
            pass

    def write(self, rel, text):
        p = os.path.join(self.root, *rel.split("/"))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)

    def remove(self, rel):
        os.remove(os.path.join(self.root, *rel.split("/")))

    def complete(self):
        self.write("README.md", README)
        self.write("CITATION.cff", CITATION)
        self.write("LICENSE", LICENSE)
        self.write("pyproject.toml", PYPROJECT)
        self.write("uv.lock", "version = 1\n")
        self.write("requirements.txt", "requests==2.32.3\nurllib3==2.2.3  # pinned\n")

    def run_checker(self, *extra, root=None):
        r = subprocess.run([sys.executable, "-I", "-B", CHECKER, root or self.root, "--json", *extra],
                           capture_output=True, text=True, timeout=120)
        try:
            data = json.loads(r.stdout) if r.stdout.strip() else None
        except ValueError:
            data = None
        return r.returncode, data, r

    def assertGuard(self, guard, status, code=None):
        rc, data, r = self.run_checker()
        self.assertIsNotNone(data, r.stderr)
        self.assertEqual(data["guards"][guard]["status"], status, data["guards"][guard]["detail"])
        if code is not None:
            self.assertEqual(rc, code, data)
        return rc, data


class CompleteFixture(Fixture):
    def test_complete_fixture_passes(self):
        self.complete()
        rc, data, r = self.run_checker()
        self.assertEqual(rc, 0, (r.stdout, r.stderr))
        self.assertEqual(sorted(data["guards"]), sorted(["citation", "placeholders", "limitations", "boundary", "lockfile", "license"]))
        self.assertTrue(all(g["status"] == "PASS" for g in data["guards"].values()))

    def test_text_mode_reports_result(self):
        self.complete()
        r = subprocess.run([sys.executable, "-I", "-B", CHECKER, self.root], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0)
        self.assertIn("RESULT PASS (exit 0)", r.stdout)

    def test_rst_readme_is_supported(self):
        self.complete()
        self.remove("README.md")
        self.write("README.rst", "Demo\n====\n\nScope and data boundary\n-----------------------\n\nThis is a non-clinical tool.\n\n"
                                 "Limitations\n-----------\n\nOnly generated data.\n")
        self.assertGuard("limitations", "PASS")
        self.assertGuard("boundary", "PASS", 0)


class CitationGuard(Fixture):
    def test_missing_citation_fails(self):
        self.complete(); self.remove("CITATION.cff")
        self.assertGuard("citation", "FAIL", 1)

    def test_wrong_case_name_fails(self):
        self.complete(); self.remove("CITATION.cff"); self.write("citation.cff", CITATION)
        rc, data = self.assertGuard("citation", "FAIL", 1)
        self.assertIn("case-sensitive", data["guards"]["citation"]["detail"])

    def test_unparsable_citation_fails(self):
        for bad in ("cff-version: 1.2.0\n\tmessage: tabbed\n", "cff-version: 1.2.0\nmessage: \"unterminated\ntitle: t\n",
                    "cff-version: 1.2.0\nauthors: {name: flow-mapping}\n", "title: a\ntitle: b\n", "cff-version: 1.2.0\n  message: bad indent\n"):
            with self.subTest(bad=bad):
                self.complete(); self.write("CITATION.cff", bad)
                self.assertGuard("citation", "FAIL", 1)

    def test_wrong_version_fails(self):
        self.complete(); self.write("CITATION.cff", CITATION.replace("cff-version: 1.2.0", "cff-version: 1.1.0"))
        self.assertGuard("citation", "FAIL", 1)

    def test_missing_authors_fails(self):
        self.complete()
        text = CITATION.split("authors:")[0] + "license: Apache-2.0\n"
        self.write("CITATION.cff", text)
        self.assertGuard("citation", "FAIL", 1)

    def test_unnamed_author_fails(self):
        self.complete(); self.write("CITATION.cff", CITATION.replace('  - family-names: "Doe"\n    given-names: "Jane"\n', '  - email: "a@example.org"\n'))
        self.assertGuard("citation", "FAIL", 1)

    def test_null_title_fails(self):
        self.complete(); self.write("CITATION.cff", CITATION.replace('title: "Demo repository"', "title: null"))
        self.assertGuard("citation", "FAIL", 1)

    def test_entity_author_and_block_scalar_pass(self):
        self.complete()
        self.write("CITATION.cff", "cff-version: \"1.2.0\"\nmessage: >-\n  If you use this,\n  cite it.\ntitle: Demo\nauthors:\n- name: SZL Holdings\nkeywords: [a, b]\n")
        self.assertGuard("citation", "PASS", 0)


class PlaceholderGuard(Fixture):
    def test_placeholder_in_license_fails(self):
        self.complete(); self.write("LICENSE", LICENSE + f"Copyright {TOKEN}: year] owner\n")
        rc, data = self.assertGuard("placeholders", "FAIL", 1)
        self.assertEqual([g for g, v in data["guards"].items() if v["status"] == "FAIL"], ["placeholders"])

    def test_placeholder_in_readme_fails(self):
        self.complete(); self.write("README.md", README.replace("Computes a toy statistic", f"{TOKEN}: purpose]]"))
        rc, data = self.assertGuard("placeholders", "FAIL", 1)
        self.assertEqual([g for g, v in data["guards"].items() if v["status"] == "FAIL"], ["placeholders"])

    def test_placeholder_in_citation_fails(self):
        self.complete(); self.write("CITATION.cff", CITATION.replace('version: "0.1.0"', f'version: "{TOKEN}: version]]"'))
        rc, data = self.assertGuard("placeholders", "FAIL", 1)
        self.assertEqual([g for g, v in data["guards"].items() if v["status"] == "FAIL"], ["placeholders"])

    def test_placeholder_inside_code_fence_fails(self):
        self.complete(); self.write("README.md", README.replace("python -I -B scripts/reproduce.py", f"{TOKEN}: command]]"))
        self.assertGuard("placeholders", "FAIL", 1)


class LimitationsGuard(Fixture):
    def test_no_limitations_heading_fails(self):
        self.complete(); self.write("README.md", README.replace("## Limitations", "## Notes"))
        rc, data = self.assertGuard("limitations", "FAIL", 1)
        self.assertEqual([g for g, v in data["guards"].items() if v["status"] == "FAIL"], ["limitations"])

    def test_heading_inside_code_fence_not_counted(self):
        self.complete()
        self.write("README.md", README.replace("## Limitations\n\n- Tested only on generated data.\n", "```bash\n# Limitations\necho no\n```\n"))
        self.assertGuard("limitations", "FAIL", 1)

    def test_bold_text_is_not_a_heading(self):
        self.complete(); self.write("README.md", README.replace("## Limitations", "**Limitations**"))
        self.assertGuard("limitations", "FAIL", 1)

    def test_empty_limitations_section_fails(self):
        self.complete(); self.write("README.md", README.replace("- Tested only on generated data.\n", "---\n"))
        self.assertGuard("limitations", "FAIL", 1)

    def test_synonym_heading_passes(self):
        self.complete(); self.write("README.md", README.replace("## Limitations", "## What this does not prove"))
        self.assertGuard("limitations", "PASS", 0)

    def test_setext_heading_passes(self):
        self.complete(); self.write("README.md", README.replace("## Limitations\n", "Known issues\n------------\n"))
        self.assertGuard("limitations", "PASS", 0)

    def test_missing_readme_fails_both_readme_guards(self):
        self.complete(); self.remove("README.md")
        rc, data, r = self.run_checker()
        self.assertEqual(rc, 1)
        self.assertEqual(data["guards"]["limitations"]["status"], "FAIL")
        self.assertEqual(data["guards"]["boundary"]["status"], "FAIL")


class BoundaryGuard(Fixture):
    def test_no_boundary_section_fails(self):
        self.complete(); self.write("README.md", README.replace("## Scope and data boundary\n\nThis repository is not a clinical tool. It uses synthetic data only and must never receive patient data.\n\n", ""))
        rc, data = self.assertGuard("boundary", "FAIL", 1)
        self.assertEqual([g for g, v in data["guards"].items() if v["status"] == "FAIL"], ["boundary"])

    def test_boundary_without_statement_fails(self):
        self.complete(); self.write("README.md", README.replace("This repository is not a clinical tool. It uses synthetic data only and must never receive patient data.", "Data comes from the internet."))
        self.assertGuard("boundary", "FAIL", 1)

    def test_statement_outside_the_section_does_not_count(self):
        self.complete()
        text = README.replace("This repository is not a clinical tool. It uses synthetic data only and must never receive patient data.", "Data comes from the internet.")
        self.write("README.md", text + "\nThis is a non-clinical project.\n")
        self.assertGuard("boundary", "FAIL", 1)

    def test_statement_wrapped_across_lines_passes(self):
        self.complete(); self.write("README.md", README.replace("is not a clinical tool", "is not a\nclinical tool"))
        self.assertGuard("boundary", "PASS", 0)


    def test_metaphor_is_not_a_boundary_statement(self):
        self.complete()
        self.write("README.md", README.replace("This repository is not a clinical tool.", "The module names are a metaphor."))
        with open(os.path.join(self.root, "README.md"), encoding="utf-8") as f:
            self.assertIn("metaphor", f.read())
        self.assertGuard("boundary", "FAIL", 1)


class LockfileGuard(Fixture):
    def test_pyproject_without_lock_fails(self):
        self.complete(); self.remove("uv.lock"); self.remove("requirements.txt")
        rc, data = self.assertGuard("lockfile", "FAIL", 1)
        self.assertEqual([g for g, v in data["guards"].items() if v["status"] == "FAIL"], ["lockfile"])

    def test_lock_in_ancestor_directory_passes(self):
        self.complete(); self.write("packages/a/pyproject.toml", PYPROJECT)
        self.assertGuard("lockfile", "PASS", 0)

    def test_pyproject_covered_by_pinned_requirements_passes(self):
        self.complete(); self.remove("uv.lock")
        self.write("pyproject.toml", PYPROJECT.replace('["requests>=2.31"]', '["requests>=2.31", "urllib3"]'))
        self.assertGuard("lockfile", "PASS", 0)

    def test_pyproject_not_covered_by_requirements_fails(self):
        self.complete(); self.remove("uv.lock")
        self.write("pyproject.toml", PYPROJECT.replace('["requests>=2.31"]', '["requests>=2.31", "numpy"]'))
        self.assertGuard("lockfile", "FAIL", 1)

    def test_exactly_pinned_pyproject_passes(self):
        self.complete(); self.remove("uv.lock"); self.remove("requirements.txt")
        self.write("pyproject.toml", PYPROJECT.replace("requests>=2.31", "requests==2.32.3"))
        self.assertGuard("lockfile", "PASS", 0)

    def test_dynamic_dependencies_without_lock_fail(self):
        self.complete(); self.remove("uv.lock"); self.remove("requirements.txt")
        self.write("pyproject.toml", '[project]\nname = "demo"\nversion = "0.1.0"\ndynamic = ["dependencies"]\n')
        self.assertGuard("lockfile", "FAIL", 1)

    def test_zero_dependency_pyproject_passes(self):
        self.complete(); self.remove("uv.lock"); self.remove("requirements.txt")
        self.write("pyproject.toml", '[project]\nname = "demo"\nversion = "0.1.0"\n\n[build-system]\nrequires = ["setuptools>=68"]\n')
        self.assertGuard("lockfile", "PASS", 0)

    def test_no_manifest_passes(self):
        self.complete(); self.remove("uv.lock"); self.remove("requirements.txt"); self.remove("pyproject.toml")
        rc, data = self.assertGuard("lockfile", "PASS", 0)
        self.assertIn("no dependency manifests", data["guards"]["lockfile"]["detail"])

    def test_unparsable_pyproject_fails(self):
        self.complete(); self.write("pyproject.toml", "[project\nname = \n")
        self.assertGuard("lockfile", "FAIL", 1)

    def test_unpinned_requirements_fail(self):
        for bad in ("requests>=2.31\n", "requests==2.*\n", "requests\n", "requests==2.32.3,<3\n", "-e git+https://example.org/x.git@main#egg=x\n"):
            with self.subTest(bad=bad):
                self.complete(); self.remove("uv.lock"); self.remove("pyproject.toml")
                self.write("requirements.txt", bad)
                self.assertGuard("lockfile", "FAIL", 1)

    def test_pinned_requirements_variants_pass(self):
        good = ("requests==2.32.3 --hash=sha256:" + "a" * 64 + "\n"
                "numpy===1.26.4 ; python_version >= '3.9'\n"
                "-e .\n--index-url https://pypi.org/simple\n# comment\n"
                "x @ https://example.org/x-1.0.whl#sha256=" + "b" * 64 + "\n")
        self.complete(); self.remove("uv.lock"); self.remove("pyproject.toml")
        self.write("requirements.txt", good)
        self.assertGuard("lockfile", "PASS", 0)

    def test_requirements_include_followed(self):
        self.complete(); self.remove("uv.lock"); self.remove("pyproject.toml")
        self.write("requirements.txt", "-r requirements/base.txt\n")
        self.write("requirements/base.txt", "requests>=2\n")
        self.assertGuard("lockfile", "FAIL", 1)
        self.write("requirements/base.txt", "requests==2.32.3\n")
        self.assertGuard("lockfile", "PASS", 0)

    def test_missing_include_fails(self):
        self.complete(); self.remove("uv.lock"); self.remove("pyproject.toml")
        self.write("requirements.txt", "-r missing.txt\n")
        self.assertGuard("lockfile", "FAIL", 1)

    def test_empty_requirements_pass(self):
        self.complete(); self.remove("uv.lock"); self.remove("pyproject.toml")
        self.write("requirements.txt", "# no third-party packages\n")
        self.assertGuard("lockfile", "PASS", 0)

    def test_package_json(self):
        self.complete()
        self.write("web/package.json", json.dumps({"name": "w", "dependencies": {"left-pad": "^1.3.0"}}))
        self.assertGuard("lockfile", "FAIL", 1)
        self.write("web/package-lock.json", "{}")
        self.assertGuard("lockfile", "PASS", 0)
        self.remove("web/package-lock.json")
        self.write("web/package.json", json.dumps({"name": "w", "devDependencies": {"left-pad": "1.3.0"}}))
        self.assertGuard("lockfile", "PASS", 0)
        self.write("web/package.json", "{not json")
        self.assertGuard("lockfile", "FAIL", 1)

    def test_dev_dependency_ranges_need_a_lock(self):
        self.complete(); self.write("web/package.json", json.dumps({"name": "w", "devDependencies": {"left-pad": "~1.3.0"}}))
        self.assertGuard("lockfile", "FAIL", 1)

    def test_node_modules_is_not_searched(self):
        self.complete(); self.write("node_modules/x/package.json", json.dumps({"dependencies": {"y": "*"}}))
        self.assertGuard("lockfile", "PASS", 0)

    def test_cargo(self):
        self.complete(); self.write("rs/Cargo.toml", '[package]\nname = "r"\nversion = "0.1.0"\n\n[dependencies]\nserde = "1"\n')
        self.assertGuard("lockfile", "FAIL", 1)
        self.write("rs/Cargo.lock", "version = 3\n")
        self.assertGuard("lockfile", "PASS", 0)

    def test_go_mod(self):
        self.complete(); self.write("go/go.mod", "module example.org/m\n\ngo 1.22\n\nrequire example.org/dep v1.2.3\n")
        self.assertGuard("lockfile", "FAIL", 1)
        self.write("go.sum", "")  # an ancestor go.sum does not count
        self.assertGuard("lockfile", "FAIL", 1)
        self.write("go/go.sum", "example.org/dep v1.2.3 h1:x=\n")
        self.assertGuard("lockfile", "PASS", 0)


class LicenseGuard(Fixture):
    def test_missing_license_fails(self):
        self.complete(); self.remove("LICENSE")
        rc, data = self.assertGuard("license", "FAIL", 1)
        self.assertEqual([g for g, v in data["guards"].items() if v["status"] == "FAIL"], ["license"])

    def test_empty_license_fails(self):
        self.complete(); self.write("LICENSE", "   \n")
        self.assertGuard("license", "FAIL", 1)

    def test_licence_spelling_and_extension_pass(self):
        self.complete(); self.remove("LICENSE"); self.write("LICENCE.md", "MIT Licence\n\nPermission is hereby granted ...\n")
        self.assertGuard("license", "PASS", 0)


class FailClosed(Fixture):
    def test_nonexistent_root_is_exit_2(self):
        r = subprocess.run([sys.executable, "-I", "-B", CHECKER, os.path.join(self.root, "does-not-exist")], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 2)

    def test_unknown_flag_is_exit_2(self):
        self.complete()
        r = subprocess.run([sys.executable, "-I", "-B", CHECKER, self.root, "--skip-everything"], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 2)

    def test_empty_directory_fails(self):
        rc, data, r = self.run_checker()
        self.assertEqual(rc, 1)
        self.assertEqual({g: v["status"] for g, v in data["guards"].items()},
                         {"citation": "FAIL", "placeholders": "PASS", "limitations": "FAIL", "boundary": "FAIL", "lockfile": "PASS", "license": "FAIL"})

    def test_guard_exception_is_exit_2(self):
        self.complete()
        mod = load_module()
        def boom(root):
            raise RuntimeError("simulated internal error")
        mod.GUARDS = [(n, boom if n == "license" else f) for n, f in mod.GUARDS]
        code, results = mod.evaluate(self.root)
        self.assertEqual(code, 2)
        self.assertEqual(results["license"]["status"], "ERROR")

    def test_unknown_status_is_exit_2(self):
        self.complete()
        mod = load_module()
        mod.GUARDS = [(n, (lambda root: ("SKIP", "not run")) if n == "boundary" else f) for n, f in mod.GUARDS]
        code, results = mod.evaluate(self.root)
        self.assertEqual(code, 2)

    def test_missing_guard_is_exit_2(self):
        self.complete()
        mod = load_module()
        mod.GUARDS = [(n, f) for n, f in mod.GUARDS if n != "citation"]
        code, results = mod.evaluate(self.root)
        self.assertEqual(code, 2)

    def test_internal_error_in_main_is_exit_2(self):
        self.complete()
        mod = load_module()
        def broken(argv):
            raise ValueError("simulated")
        mod.main = broken
        old = sys.argv
        try:
            sys.argv = ["repo_hygiene_check.py", self.root]
            self.assertEqual(mod._entry(), 2)
        finally:
            sys.argv = old

    def test_evaluate_never_returns_0_with_a_failure(self):
        self.complete(); self.remove("LICENSE")
        mod = load_module()
        code, results = mod.evaluate(self.root)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
