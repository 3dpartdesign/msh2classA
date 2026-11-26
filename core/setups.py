# msh2classA - Copyright (C) 2026 Marcin Bartoszewski, trading as 3dPARTdesign
# https://3dPARTdesign.com - GPL-3.0 with additional terms, see LICENSE and NOTICE.
"""scan_setups.json - the positions of the scan meshes, shared by Rhino,
Blender and FreeCAD. Lives in the folder of the scan files; the scan files
themselves are only ever READ.

File layout (the Rhino version writes the same, without the "_units" /
"_format" / "sha256" / "size" keys; they are optional):

{
  "front": {                                  <- setup name
    "_saved": 1764160000.0,                   <- time.time() when saved
    "_units": "mm",                           <- unit of the matrix moves AND of the scan coordinates
    "_format": "msh2classA-setup/1",
    "Bumper_scan": {                          <- mesh name (file name without extension)
      "file": "Bumper_scan.stl",
      "matrix_row_major": [16 numbers],       <- 4x4, column vectors (as Rhino, Blender, FreeCAD)
      "layer": "...", "block": "...",          <- Rhino only, kept as they are
      "sha256": "...", "size": 123456          <- of the scan file when the setup was saved
    }
  }
}
Keys starting with "_" are not meshes. Setups without "_units" are taken as mm.
"""
from __future__ import print_function, division
import hashlib
import json
import os
import time

SETUP_JSON = "scan_setups.json"
FORMAT = "msh2classA-setup/1"
UNIT_MM = {"mm": 1.0, "cm": 10.0, "m": 1000.0, "in": 25.4, "inch": 25.4, "ft": 304.8, "um": 0.001}
SCAN_EXT = (".stl", ".obj", ".ply")


def file_sha256(path, chunk=1 << 20):
    """SHA-256 of a file (read only)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def mesh_name(path):
    return os.path.splitext(os.path.basename(path))[0]


def unit_mm(units):
    """mm per unit; unknown -> mm."""
    return UNIT_MM.get((units or "mm").lower(), 1.0)


def setup_files(scan_paths):
    """scan_setups.json in every folder the scans come from (in order)."""
    out = []
    for p in scan_paths:
        f = os.path.join(os.path.dirname(os.path.abspath(p)), SETUP_JSON)
        if f not in out:
            out.append(f)
    return out


def read_setups(scan_paths, log=print):
    """All setups from the folders of the scans; the newest copy of a name wins."""
    data = {}
    for f in setup_files(scan_paths):
        if not os.path.isfile(f):
            continue
        try:
            with open(f) as fh:
                d = json.loads(fh.read())
        except Exception:
            log("  [WARN] could not read {}".format(f))
            continue
        for k, v in (d or {}).items():
            if not isinstance(v, dict):
                continue
            if k not in data or v.get("_saved", 0) >= data[k].get("_saved", 0):
                data[k] = v
    return data


def write_setups(scan_paths, data, log=print):
    """Write to scan_setups.json in every scan folder -> files written."""
    txt = json.dumps(data, indent=1, sort_keys=True)
    done = []
    for f in setup_files(scan_paths):
        try:
            with open(f, "w") as fh:
                fh.write(txt)
            done.append(f)
        except Exception:
            log("  [WARN] could not write {} (folder read-only?)".format(f))
    return done


def find_record(setup, path):
    """The entry of a scan in a setup: by mesh name, else by file name."""
    rec = setup.get(mesh_name(path))
    if isinstance(rec, dict):
        return rec
    fn = os.path.basename(path).lower()
    for k, v in setup.items():
        if isinstance(v, dict) and not k.startswith("_") and v.get("file", "").lower() == fn:
            return v
    return None


def scaled_matrix(rec, setup, model_mm):
    """The record's 4x4 (row-major, 16 numbers) for a model in model_mm mm per
    unit: the moves scaled, the turn unchanged."""
    m = list(rec["matrix_row_major"])
    f = unit_mm(setup.get("_units")) / model_mm
    for i in (3, 7, 11):
        m[i] *= f
    return m


def scan_scale(setup_units, model_mm):
    """Factor for the scan coordinates (they are in the setup unit)."""
    return unit_mm(setup_units) / model_mm


def check_file(rec, path):
    """None if fine / unknown, else a warning: the scan file changed since the setup."""
    want = rec.get("sha256") if rec else None
    if not want:
        return None
    try:
        got = file_sha256(path)
    except Exception as e:
        return "cannot read {}: {}".format(path, e)
    if got != want:
        return "{} CHANGED since the setup was saved (SHA-256 differs) - is it still the customer's scan?".format(
            os.path.basename(path))
    return None


def make_setup(scans, units="mm", hashes=None):
    """scans: [(path, matrix_row_major in `units`, extra dict or None)] -> setup dict."""
    st = {"_saved": time.time(), "_units": units, "_format": FORMAT}
    for (path, m, extra) in scans:
        rec = dict(extra or {})
        rec["file"] = os.path.basename(path)
        rec["matrix_row_major"] = [float(x) for x in m]
        try:
            rec["sha256"] = (hashes or {}).get(path) or file_sha256(path)
            rec["size"] = os.path.getsize(path)
        except Exception:
            pass
        st[mesh_name(path)] = rec
    return st


def identity():
    return [1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0]
