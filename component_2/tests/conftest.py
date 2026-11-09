import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT2 = ROOT / "component_2"
if str(COMPONENT2) not in sys.path:
    sys.path.insert(0, str(COMPONENT2))
