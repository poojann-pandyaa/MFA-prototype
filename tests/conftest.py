import os
import tempfile

# Every amfa service reads its settings once, at first use; point them at a throwaway data
# directory before any test imports amfa.
os.environ.setdefault("AMFA_DATA_DIR", tempfile.mkdtemp(prefix="amfa-tests-"))
os.environ.setdefault("AMFA_ENV", "development")
os.environ.setdefault("AMFA_MODELS_DIR", tempfile.mkdtemp(prefix="amfa-models-"))
