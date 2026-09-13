"""Loads a model package: MODEL.py + model.yaml."""
import importlib.util, inspect
from pathlib import Path
import yaml
from models.base import Model


def load_model(package_dir):
    package_dir = Path(package_dir)
    manifest_path, module_path = package_dir/"model.yaml", package_dir/"MODEL.py"
    if not manifest_path.exists():
        raise FileNotFoundError(f"no model.yaml in {package_dir}")
    if not module_path.exists():
        raise FileNotFoundError(f"no MODEL.py in {package_dir}")

    manifest = yaml.safe_load(manifest_path.read_text())
    for k in ("name","version","expected_features"):
        if k not in manifest:
            raise KeyError(f"model.yaml missing required key: {k!r}")

    spec = importlib.util.spec_from_file_location(f"model_{manifest['name']}", module_path)
    mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    cands = [o for _, o in inspect.getmembers(mod, inspect.isclass)
             if issubclass(o, Model) and o is not Model]
    if len(cands) != 1:
        raise ValueError(f"{module_path} must define exactly one Model subclass, found {len(cands)}")

    inst = cands[0]()
    inst.name = manifest["name"]; inst.version = manifest["version"]
    inst.expected_features = list(manifest["expected_features"])
    inst.manifest = manifest
    return inst
