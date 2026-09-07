"""Loads a model package: MODEL.py + model.yaml."""

import importlib.util
import inspect
from pathlib import Path

import yaml

from models.base import Model


def load_model(package_dir: str | Path) -> Model:
    package_dir = Path(package_dir)
    manifest_path = package_dir / "model.yaml"
    module_path = package_dir / "MODEL.py"

    if not manifest_path.exists():
        raise FileNotFoundError(f"no model.yaml in {package_dir}")
    if not module_path.exists():
        raise FileNotFoundError(f"no MODEL.py in {package_dir}")

    manifest = yaml.safe_load(manifest_path.read_text())
    for key in ("name", "version", "expected_features"):
        if key not in manifest:
            raise KeyError(f"model.yaml missing required key: {key!r}")

    spec = importlib.util.spec_from_file_location(
        f"model_{manifest['name']}", module_path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    candidates = [
        obj for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, Model) and obj is not Model
    ]
    if len(candidates) != 1:
        raise ValueError(
            f"{module_path} must define exactly one Model subclass, "
            f"found {len(candidates)}"
        )

    instance = candidates[0]()
    instance.name = manifest["name"]
    instance.version = manifest["version"]
    instance.expected_features = list(manifest["expected_features"])
    instance.manifest = manifest
    return instance
