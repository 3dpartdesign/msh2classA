# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
"""Build the Rhino, Blender and FreeCAD versions.

    python tools/build.py

Rhino:   joins the files listed in build_order.txt (dev-only blocks removed)
         into dist/rhino/msh2classA.py, with the files it needs next to it
         (display mode .ini, matcap .png, LICENSE, NOTICE).
Blender: dist/msh2classA-blender-<version>.zip (Edit > Preferences > Get
         Extensions > Install from Disk).
FreeCAD: dist/msh2classA-freecad-<version>.zip (unzip into FreeCAD's Mod folder).
Every zip in dist/ is ready for a GitHub release.
"""
from __future__ import print_function
import io
import os
import re
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BEGIN = "# >>> dev-only"
END = "# <<< dev-only"
NEXT_TO_SCRIPT = ["rhino/msh2classA_3dPARTdesign_com.ini",
                  "assets/reflection_matcap_3dPARTdesign_com.png",
                  "LICENSE", "NOTICE"]


def read(rel):
    with io.open(os.path.join(ROOT, rel), "r", encoding="utf-8", newline="") as f:
        return f.read()


def strip_dev(text, rel):
    out, inside = [], False
    for line in text.splitlines(True):
        if line.startswith(BEGIN):
            inside = True
            continue
        if line.startswith(END):
            if not inside:
                sys.exit("build: '%s' has an end marker without a start" % rel)
            inside = False
            continue
        if not inside:
            out.append(line)
    if inside:
        sys.exit("build: '%s' has a dev-only block that is never closed" % rel)
    return "".join(out)


def order():
    return [l.strip() for l in read("build_order.txt").splitlines()
            if l.strip() and not l.strip().startswith("#")]


def check_constants():
    """Constants kept twice (core/constants.py for plain Python, rhino part for
    the build) must agree."""
    core = read("core/constants.py")
    rhino = read("rhino/candidates.py")
    for name in re.findall(r"^([A-Z_]+)\s*=", core, re.M):
        a = re.search(r"^%s\s*=\s*([^\s#]+)" % name, core, re.M).group(1)
        b = re.search(r"^%s\s*=\s*([^\s#]+)" % name, rhino, re.M)
        if b is None or b.group(1) != a:
            sys.exit("build: %s differs: core/constants.py = %s, rhino/candidates.py = %s"
                     % (name, a, b.group(1) if b else "missing"))


def check_legal(text):
    """Version, legal line and CAD Services text: the Rhino build and
    core/legal.py (used by Blender / FreeCAD) must say the same."""
    ns = {"__name__": "msh2classA_build_check"}
    exec(compile(text, "msh2classA.py", "exec"), ns)
    lg = {"__name__": "legal"}
    exec(compile(read("core/legal.py"), "legal.py", "exec"), lg)
    for k in ("VERSION", "LEGAL_LINE", "CAD_SERVICES_EMAIL", "CAD_SERVICES_URL",
              "CAD_SERVICES_QUESTIONS", "CAD_SERVICES_TEXT"):
        if ns.get(k) != lg.get(k):
            sys.exit("build: %s differs between the Rhino part and core/legal.py" % k)


def build_rhino():
    text = "".join(strip_dev(read(rel), rel) for rel in order())
    compile(text, "msh2classA.py", "exec")                     # syntax check
    check_legal(text)
    version = re.search(r'^VERSION = "msh2classA ([^"]+)"', text, re.M).group(1)
    out_dir = fresh(os.path.join(ROOT, "dist", "rhino"))
    script = os.path.join(out_dir, "msh2classA.py")
    with io.open(script, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    names = ["msh2classA.py"]
    for rel in NEXT_TO_SCRIPT:
        if copy(rel, out_dir):
            names.append(os.path.basename(rel))
    zpath = os.path.join(ROOT, "dist", "msh2classA-rhino-%s.zip" % version)
    zip_dir(out_dir, zpath, "msh2classA/")
    print("built %s (%d lines)" % (os.path.relpath(script, ROOT), text.count("\n")))
    print("      %s" % os.path.relpath(zpath, ROOT))
    return version


def fresh(path):
    if os.path.isdir(path):
        shutil.rmtree(path)
    os.makedirs(path)
    return path


def copy(rel, dst_dir, name=None):
    src = os.path.join(ROOT, rel)
    if not os.path.isfile(src):
        print("  [WARN] missing %s - not in the build" % rel)
        return False
    shutil.copy2(src, os.path.join(dst_dir, name or os.path.basename(rel)))
    return True


def copy_core(dst):
    """The shared core as a sub-package (dev-only blocks KEPT: they are its imports)."""
    d = fresh(os.path.join(dst, "core"))
    for f in sorted(os.listdir(os.path.join(ROOT, "core"))):
        if f.endswith(".py"):
            shutil.copy2(os.path.join(ROOT, "core", f), d)


def zip_dir(src_dir, zpath, prefix=""):
    if os.path.exists(zpath):
        os.remove(zpath)
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        for base, dirs, files in os.walk(src_dir):
            dirs[:] = [d for d in dirs if d != "__pycache__"]
            for f in sorted(files):
                full = os.path.join(base, f)
                z.write(full, prefix + os.path.relpath(full, src_dir).replace(os.sep, "/"))


def build_blender(version):
    """Blender extension: the files at the root of the zip (Install from Disk)."""
    out = fresh(os.path.join(ROOT, "dist", "blender", "msh2classA"))
    for rel in ("blender/__init__.py", "blender/blender_manifest.toml", "LICENSE", "NOTICE"):
        copy(rel, out)
    copy_core(out)
    os.makedirs(os.path.join(out, "assets"))
    copy("assets/reflection_matcap_3dPARTdesign_com.png", os.path.join(out, "assets"))
    zpath = os.path.join(ROOT, "dist", "msh2classA-blender-%s.zip" % version)
    zip_dir(out, zpath)
    print("      %s" % os.path.relpath(zpath, ROOT))


def build_freecad(version):
    """FreeCAD workbench: unzip into FreeCAD's Mod folder -> Mod/msh2classA/."""
    if not os.path.isdir(os.path.join(ROOT, "freecad")):
        return
    out = fresh(os.path.join(ROOT, "dist", "freecad", "msh2classA"))
    for f in sorted(os.listdir(os.path.join(ROOT, "freecad"))):
        src = os.path.join(ROOT, "freecad", f)
        if os.path.isfile(src):
            shutil.copy2(src, out)
        elif os.path.isdir(src) and f != "__pycache__":
            shutil.copytree(src, os.path.join(out, f), ignore=shutil.ignore_patterns("__pycache__"))
    pkg = os.path.join(out, "msh2classA_fc")
    copy_core(pkg)
    for rel in ("LICENSE", "NOTICE"):
        copy(rel, out)
    zpath = os.path.join(ROOT, "dist", "msh2classA-freecad-%s.zip" % version)
    zip_dir(out, zpath, "msh2classA/")
    print("      %s" % os.path.relpath(zpath, ROOT))


def main():
    check_constants()
    version = build_rhino()
    build_blender(version)
    build_freecad(version)


if __name__ == "__main__":
    main()
