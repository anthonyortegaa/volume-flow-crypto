"""Smoke test confirming the package imports and the test runner is wired."""
from __future__ import annotations

import volume_flow


def test_package_import_exposes_expected_name() -> None:
    assert volume_flow.__name__ == "volume_flow"
