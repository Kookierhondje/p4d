from pathlib import Path

from cffi import FFI
from setuptools import setup

ROOT = Path(__file__).resolve().parent

ffibuilder = FFI()
ffibuilder.cdef((ROOT / "src" / "p4d" / "py_fourd.h").read_text())

ffibuilder.set_source(
    "p4d._lib4d",
    '#include "fourd.h"',
    sources=[str(p.relative_to(ROOT)) for p in (ROOT / "lib4d_sql").glob("*.c")],
    include_dirs=["lib4d_sql"],
)

setup(ext_modules=[ffibuilder.distutils_extension()])
