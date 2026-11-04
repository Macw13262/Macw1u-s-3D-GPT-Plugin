"""
Macw1u's 3D - geometry engine.

Tinkercad-style modelling:
  * Every shape is a primitive that fits a bounding box (size = [x, y, z] in mm).
  * position places the shape on the workplane: x/y = centre, z = BOTTOM of the shape.
  * rotation is in degrees about the shape's own centre (x, y, z).
  * mode "solid" adds material, mode "hole" cuts material.
  * Holes cut every solid that comes BEFORE them in the list.
Z is up, units are millimetres.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import trimesh
from trimesh import transformations as tf

SHAPES = [
    "box", "cylinder", "sphere", "half_sphere", "cone", "pyramid",
    "wedge", "roof", "torus", "tube", "prism",
]

NAMED_COLORS = {
    "red": "#e5484d", "orange": "#f76b15", "yellow": "#f5d90a", "green": "#30a46c",
    "teal": "#12a594", "blue": "#3e63dd", "navy": "#1f2d5c", "purple": "#8e4ec6",
    "pink": "#d6409f", "white": "#f4f4f2", "grey": "#8b8d98", "gray": "#8b8d98",
    "black": "#202225", "brown": "#8d5b3a", "gold": "#d4a72c", "silver": "#b9bcc5",
}
DEFAULT_COLOR = "#3e63dd"


def parse_color(c: str | None) -> tuple[int, int, int, int]:
    c = (c or DEFAULT_COLOR).strip().lower()
    c = NAMED_COLORS.get(c, c)
    if not c.startswith("#") or len(c) not in (4, 7):
        c = DEFAULT_COLOR
    if len(c) == 4:
        c = "#" + "".join(ch * 2 for ch in c[1:])
    try:
        return (int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16), 255)
    except ValueError:
        return parse_color(DEFAULT_COLOR)


def _normalise(m: trimesh.Trimesh) -> trimesh.Trimesh:
    m = m.copy()
    m.apply_translation(-m.bounds.mean(axis=0))
    ext = np.maximum(m.extents, 1e-9)
    m.apply_transform(np.diag([*(1.0 / ext), 1.0]))
    return m


def _hull(points) -> trimesh.Trimesh:
    return trimesh.convex.convex_hull(np.array(points, dtype=float))


def unit_shape(kind: str, sides: int = 48, wall: float = 0.2) -> trimesh.Trimesh:
    sides = int(max(3, min(sides, 128)))
    if kind == "box":
        m = trimesh.creation.box([1, 1, 1])
    elif kind == "cylinder":
        m = trimesh.creation.cylinder(radius=0.5, height=1, sections=sides)
    elif kind == "prism":
        m = trimesh.creation.cylinder(radius=0.5, height=1, sections=max(3, min(sides, 32)))
    elif kind == "sphere":
        m = trimesh.creation.icosphere(subdivisions=3, radius=0.5)
    elif kind == "half_sphere":
        s = trimesh.creation.icosphere(subdivisions=3, radius=0.5)
        v = s.vertices[s.vertices[:, 2] >= -1e-9].copy()
        v[:, 2] = np.maximum(v[:, 2], 0.0)
        m = _hull(v)
    elif kind == "cone":
        m = trimesh.creation.cone(radius=0.5, height=1, sections=sides)
    elif kind == "pyramid":
        h = 0.5
        m = _hull([[-h, -h, -h], [h, -h, -h], [h, h, -h], [-h, h, -h], [0, 0, h]])
    elif kind == "wedge":
        h = 0.5
        m = _hull([[-h, -h, -h], [h, -h, -h], [h, h, -h], [-h, h, -h],
                   [-h, -h, h], [h, -h, h]])
    elif kind == "roof":
        h = 0.5
        m = _hull([[-h, -h, -h], [h, -h, -h], [h, h, -h], [-h, h, -h],
                   [0, -h, h], [0, h, h]])
    elif kind == "torus":
        m = trimesh.creation.torus(major_radius=0.35, minor_radius=0.15,
                                   major_sections=sides, minor_sections=24)
    elif kind == "tube":
        outer = trimesh.creation.cylinder(radius=0.5, height=1, sections=sides)
        w = float(min(max(wall, 0.02), 0.9))
        inner = trimesh.creation.cylinder(radius=0.5 * (1 - w), height=1.2, sections=sides)
        m = trimesh.boolean.difference([outer, inner], engine="manifold")
    else:
        raise ValueError(f"Unknown shape '{kind}'. Valid: {', '.join(SHAPES)}")
    return _normalise(m)


def build_part(spec: dict[str, Any]) -> trimesh.Trimesh:
    kind = spec["shape"]
    sx, sy, sz = [float(v) for v in spec.get("size", [20, 20, 20])]
    px, py, pz = [float(v) for v in spec.get("position", [0, 0, 0])]
    rx, ry, rz = [math.radians(float(v)) for v in spec.get("rotation", [0, 0, 0])]
    m = unit_shape(kind, sides=spec.get("sides", 48), wall=spec.get("wall", 0.2))
    m.apply_transform(np.diag([sx, sy, sz, 1.0]))
    R = tf.euler_matrix(rx, ry, rz, axes="sxyz")
    m.apply_transform(R)
    m.apply_translation([px, py, pz + sz / 2.0])
    m.merge_vertices()
    return m


def _union(meshes: list[trimesh.Trimesh]) -> trimesh.Trimesh | None:
    meshes = [m for m in meshes if m is not None and len(m.faces)]
    if not meshes:
        return None
    if len(meshes) == 1:
        return meshes[0]
    return trimesh.boolean.union(meshes, engine="manifold")


def build_scene(shapes: list[dict[str, Any]], merge: bool = False):
    """Return coloured parts after holes and a merged mesh for export."""
    steps = []
    for i, spec in enumerate(shapes):
        mode = spec.get("mode", "solid")
        steps.append({
            "idx": i,
            "name": spec.get("name") or f"{spec['shape']}_{i + 1}",
            "color": parse_color(spec.get("color")),
            "mode": mode,
            "mesh": build_part(spec),
        })
    parts = []
    for s in steps:
        if s["mode"] != "solid":
            continue
        mesh = s["mesh"]
        holes = [h["mesh"] for h in steps if h["mode"] == "hole" and h["idx"] > s["idx"]]
        if holes:
            cutter = _union(holes)
            if cutter is not None and _bbox_overlap(mesh, cutter):
                mesh = trimesh.boolean.difference([mesh, cutter], engine="manifold")
        if len(mesh.faces):
            parts.append((s["name"], s["color"], mesh))
    merged = _union([p[2] for p in parts])
    return parts, merged


def _bbox_overlap(a: trimesh.Trimesh, b: trimesh.Trimesh) -> bool:
    return bool(np.all(a.bounds[0] <= b.bounds[1]) and np.all(b.bounds[0] <= a.bounds[1]))


def export_all(parts, merged, out_dir) -> dict[str, Any]:
    from pathlib import Path
    from PIL import Image, ImageDraw

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    scene = trimesh.Scene()
    for name, color, mesh in parts:
        m = mesh.copy()
        m.visual = trimesh.visual.ColorVisuals(mesh=m, face_colors=np.tile(color, (len(m.faces), 1)))
        scene.add_geometry(m, node_name=name, geom_name=name)
    scene.apply_transform(tf.rotation_matrix(-math.pi / 2, [1, 0, 0]))
    (out / "model.glb").write_bytes(scene.export(file_type="glb"))

    stats: dict[str, Any] = {}
    if merged is not None:
        merged.export(out / "model.stl")
        merged.export(out / "model.obj")
        b = merged.bounds
        stats = {
            "size_mm": [round(float(v), 3) for v in (b[1] - b[0])],
            "triangles": int(len(merged.faces)),
            "volume_mm3": round(float(merged.volume), 2) if merged.is_watertight else None,
            "watertight": bool(merged.is_watertight),
            "printable": bool(merged.is_watertight and merged.is_winding_consistent),
        }
    render_preview(parts, out / "preview.png")
    return stats


def render_preview(parts, path, size=(1000, 750), elev=30, azim=-50) -> None:
    """Render a simple shaded PNG preview using a z-buffer."""
    from PIL import Image, ImageDraw

    tris, cols = [], []
    for _, color, mesh in parts:
        tris.append(mesh.triangles)
        cols.append(np.tile(np.array(color[:3], dtype=float) / 255.0, (len(mesh.faces), 1)))
    if not tris:
        return
    tris = np.concatenate(tris)
    cols = np.concatenate(cols)
    n_tri = len(tris)
    ss = 2 if n_tri < 25000 else 1
    W, H = size[0] * ss, size[1] * ss
    el, az = math.radians(elev), math.radians(azim)
    c = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), math.sin(el)])
    f = -c
    r = np.cross(f, [0, 0, 1.0]); r /= np.linalg.norm(r)
    u = np.cross(r, f)
    light = 0.55 * c - 0.35 * r + 0.6 * u
    light /= np.linalg.norm(light)
    flat = tris.reshape(-1, 3)
    lo, hi = flat.min(axis=0), flat.max(axis=0)
    ctr = (lo + hi) / 2
    span = float((hi - lo).max()) or 1.0
    gr = span * 0.9
    z0 = lo[2]

    def project(p):
        q = p - ctr
        return np.stack([q @ r, q @ u, q @ f], axis=-1)

    corners = np.array([[ctr[0] + sx * gr, ctr[1] + sy * gr, z0]
                        for sx in (-1, 1) for sy in (-1, 1)])
    allp = np.concatenate([project(flat), project(corners)])
    mn, mx = allp[:, :2].min(axis=0), allp[:, :2].max(axis=0)
    scale = min(W * 0.92 / (mx[0] - mn[0]), H * 0.92 / (mx[1] - mn[1]))
    mid = (mn + mx) / 2

    def to_px(p3):
        pr = project(p3)
        return (W / 2 + (pr[..., 0] - mid[0]) * scale,
                H / 2 - (pr[..., 1] - mid[1]) * scale, pr[..., 2])

    bg = Image.new("RGB", (W, H), (238, 243, 251))
    d = ImageDraw.Draw(bg)
    for t in np.linspace(-gr, gr, 11):
        for a, b in (([ctr[0] + t, ctr[1] - gr, z0], [ctr[0] + t, ctr[1] + gr, z0]),
                     ([ctr[0] - gr, ctr[1] + t, z0], [ctr[0] + gr, ctr[1] + t, z0])):
            xs, ys, _ = to_px(np.array([a, b]))
            d.line([(xs[0], ys[0]), (xs[1], ys[1])],
                   fill=(190, 203, 232), width=max(1, ss))
    img = np.asarray(bg, dtype=np.float32) / 255.0
    zbuf = np.full((H, W), np.inf, dtype=np.float32)
    e1, e2 = tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0]
    nrm = np.cross(e1, e2)
    nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
    facing = nrm @ f
    nrm[facing > 0] *= -1
    shade = 0.32 + 0.68 * np.clip(nrm @ light, 0, 1)
    rgb = np.clip(cols * shade[:, None], 0, 1)
    X, Y, Z = to_px(tris)
    for i in range(n_tri):
        x, y, z = X[i], Y[i], Z[i]
        x0, x1 = int(max(math.floor(x.min()), 0)), int(min(math.ceil(x.max()), W - 1))
        y0, y1 = int(max(math.floor(y.min()), 0)), int(min(math.ceil(y.max()), H - 1))
        if x1 < x0 or y1 < y0:
            continue
        den = ((y[1] - y[2]) * (x[0] - x[2]) +
               (x[2] - x[1]) * (y[0] - y[2]))
        if abs(den) < 1e-9:
            continue
        gx, gy = np.meshgrid(np.arange(x0, x1 + 1) + 0.5,
                             np.arange(y0, y1 + 1) + 0.5)
        w0 = ((y[1] - y[2]) * (gx - x[2]) +
              (x[2] - x[1]) * (gy - y[2])) / den
        w1 = ((y[2] - y[0]) * (gx - x[2]) +
              (x[0] - x[2]) * (gy - y[2])) / den
        w2 = 1.0 - w0 - w1
        inside = (w0 >= -1e-4) & (w1 >= -1e-4) & (w2 >= -1e-4)
        if not inside.any():
            continue
        zz = w0 * z[0] + w1 * z[1] + w2 * z[2]
        zs = zbuf[y0:y1 + 1, x0:x1 + 1]
        win = inside & (zz < zs)
        if win.any():
            zs[win] = zz[win]
            img[y0:y1 + 1, x0:x1 + 1][win] = rgb[i]
    out = Image.fromarray((img * 255).astype(np.uint8))
    if ss > 1:
        out = out.resize(size, Image.LANCZOS)
    out.save(path)
