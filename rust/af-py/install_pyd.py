import shutil
import sysconfig
from pathlib import Path

site = Path(sysconfig.get_paths()["purelib"])
src = Path(__file__).parent / "target" / "release" / "af_core_py.dll"
dst = site / "af_core_py.pyd"
shutil.copy2(src, dst)
print(f"installed to {dst}")
