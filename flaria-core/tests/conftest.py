import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("FLARIA_ENV", "development")
os.environ.setdefault("FLARIA_JWT_SECRET", "test-secret-test-secret-test-secret-test-secret-0123")
