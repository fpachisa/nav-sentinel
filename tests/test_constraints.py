"""The deployed artefact must be the tested artefact.

`constraints.txt` is what the image installs under, and it exists because without it pip resolved
whatever was newest at build time. One build picked up `google-api-core` 2.35.0 against 2.34.0 here,
and Firestore **queries** started failing in Cloud Run with `Invalid database id %28default%29` while
document reads carried on working -- so the application loaded and only the pages that run a query
returned 500. A smoke test fetching one document would have passed.

The first attempt at this file was generated with `python -m pip freeze` into a venv that has no
pip, so it was **empty**, and an empty constraints file silently constrains nothing. It shipped once
before this test existed.
"""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

import pytest

CONSTRAINTS = Path(__file__).resolve().parents[1] / "constraints.txt"

#: Packages whose version has actually broken this deployment or would break it silently.
CRITICAL = (
    "google-api-core",
    "google-cloud-firestore",
    "google-cloud-core",
    "google-adk",
    "google-genai",
    "fastapi",
    "python-multipart",
)


def _pinned() -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in CONSTRAINTS.read_text().splitlines():
        if "==" in line and not line.startswith("#"):
            name, _, version = line.partition("==")
            pins[name.strip().lower()] = version.strip()
    return pins


class TestTheConstraintsFileIsReal:
    def test_it_exists(self):
        assert CONSTRAINTS.is_file(), "the image installs under this file"

    def test_it_is_not_empty(self):
        """The failure this file's own history is about. An empty constraints file is not a
        constraint; it is a comment that pip reads and ignores."""
        assert len(_pinned()) >= 20, f"only {len(_pinned())} pins — the freeze failed"

    @pytest.mark.parametrize("package", CRITICAL)
    def test_every_package_that_has_broken_a_deployment_is_pinned(self, package):
        assert package in _pinned(), f"{package} is unpinned; the image may resolve a newer one"

    @pytest.mark.parametrize("package", CRITICAL)
    def test_the_pin_matches_what_is_installed_here(self, package):
        """Otherwise the file pins a version nobody has run. A constraint that disagrees with the
        test environment moves the untested-artefact problem rather than fixing it."""
        assert _pinned()[package] == metadata.version(package)

    def test_the_dockerfile_actually_applies_them(self):
        """A constraints file the build does not reference is decoration."""
        dockerfile = (CONSTRAINTS.parent / "Dockerfile").read_text()
        assert "constraints.txt" in dockerfile
        assert "-c constraints.txt" in dockerfile
        assert "COPY pyproject.toml constraints.txt" in dockerfile
