"""Plugin version compatibility and conflict checking."""
from __future__ import annotations

import sys
from dataclasses import dataclass

from packaging.specifiers import SpecifierSet, InvalidSpecifier
from packaging.version import Version, InvalidVersion

import dataportal
from dataportal.plugins.base import PluginMeta


@dataclass
class CompatResult:
    compatible: bool
    reasons: list[str]


def check_compatibility(meta: PluginMeta, loaded_plugins: list[str]) -> CompatResult:
    """Check if a plugin is compatible with the current environment."""
    reasons = []

    # Check dataportal version
    try:
        core_version = Version(dataportal.__version__)
        spec = SpecifierSet(meta.dataportal_version)
        if core_version not in spec:
            reasons.append(
                f"Requires dataportal {meta.dataportal_version}, "
                f"current is {dataportal.__version__}"
            )
    except (InvalidSpecifier, InvalidVersion) as e:
        reasons.append(f"Invalid dataportal_version specifier: {e}")

    # Check python version
    try:
        py_version = Version(f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
        spec = SpecifierSet(meta.python_version)
        if py_version not in spec:
            reasons.append(
                f"Requires Python {meta.python_version}, "
                f"current is {py_version}"
            )
    except (InvalidSpecifier, InvalidVersion) as e:
        reasons.append(f"Invalid python_version specifier: {e}")

    # Check conflicts
    for conflict in meta.conflicts_with:
        if conflict in loaded_plugins:
            reasons.append(f"Conflicts with loaded plugin '{conflict}'")

    return CompatResult(compatible=len(reasons) == 0, reasons=reasons)
