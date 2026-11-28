"""New patches never go onto a scan/mesh or photo layer (patch_layer), and nothing on those layers takes part
in the patches (a photo is a flat surface too); LoadPhotos gives back the current layer it found.
    python tests/test_layers.py"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); sys.argv = sys.argv[:1]
import test_scan_tools as T
import fakerhino as fr
fails = []
def chk(name, ok, info=""):
    print("  [%s] %s %s" % ("ok" if ok else "FAIL", name, info))
    if not ok: fails.append(name)

b = T.Bench(names=("A", "B")); ns = b.ns; L = b.sc.doc.Layers
work = L.add("Car::Body"); L.SetCurrentLayerIndex(work, True)
chk("current layer an ordinary one: patches go there", ns["patch_layer"]() == (work, "Car::Body"))
scan_li = b.sc.doc.Objects.FindId(b.objs["A"].Id).Attributes.LayerIndex
chk("(the scan/mesh layer is a reference layer)", ns["reference_layer"](scan_li) and not ns["reference_layer"](work),
    L[scan_li].FullPath)
L.SetCurrentLayerIndex(scan_li, True)
li, path = ns["patch_layer"]()
chk("current layer = a scan/mesh layer: patches go to '%s'" % ns["PATCH_ROOT"], path == ns["PATCH_ROOT"] and li is not None
    and L[li].FullPath == ns["PATCH_ROOT"])
chk("... the current layer itself is not changed", L.CurrentLayerIndex == scan_li)
root = L.add(ns["SCAN_ROOT"]); L.SetCurrentLayerIndex(root, True)
chk("... the scan/mesh root too", ns["patch_layer"]()[1] == ns["PATCH_ROOT"])

L.SetCurrentLayerIndex(work, True)
front = os.path.join(T.WORK, "front.png"); T.png(front, 2000, 1200)
b.run("load_photos", [("files", [front]), ("list", "Front")])
chk("LoadPhotos: the current layer is the one before (not the photo layer)", L.CurrentLayerIndex == work,
    L[L.CurrentLayerIndex].FullPath)
frame = [v for v in b.sc.doc.Objects.items.values() if isinstance(v, fr.PictureObject)][0]
chk("a photo lies on a reference layer", ns["on_reference_layer"](frame))
ph_li = frame.Attributes.LayerIndex; L.SetCurrentLayerIndex(ph_li, True)
chk("current layer = a photo layer: patches go to '%s'" % ns["PATCH_ROOT"], ns["patch_layer"]()[1] == ns["PATCH_ROOT"])
src = open(T.SCRIPT).read()
chk("collect_surfaces / collect_candidates skip the reference layers",
    src.count("if is_preview(obj) or on_reference_layer(obj): continue") == 2)
chk("the build adds its patch on patch_layer()", "at_ = ro.ObjectAttributes(); li_ = patch_layer()[0]" in src)
print("ALL OK" if not fails else "%d FAILED" % len(fails))
sys.exit(1 if fails else 0)
