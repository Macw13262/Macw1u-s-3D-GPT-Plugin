"""Macw1u's 3D - API server (use as a ChatGPT Custom GPT Action)."""
from __future__ import annotations

import json
import os
import secrets
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field, field_validator
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

import engine

BASE_URL = os.environ.get("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("API_KEY", "")
DATA_DIR = Path(os.environ.get("DATA_DIR", "./data"))
MAX_SHAPES = int(os.environ.get("MAX_SHAPES", "150"))
MAX_DIM = 2000.0
KEEP_HOURS = float(os.environ.get("KEEP_HOURS", "72"))
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Remote MCP endpoint (Streamable HTTP). The public hostname is allowlisted
# explicitly so the SDK's DNS-rebinding protection remains enabled in production.
mcp_server = FastMCP("Macw1u's 3D")
_public_host = urlparse(BASE_URL).netloc
_mcp_security = TransportSecuritySettings(
    allowed_hosts=[_public_host, f"{_public_host.split(':')[0]}:*"]
)

@asynccontextmanager
async def _lifespan(_app):
    async with mcp_server.session_manager.run():
        yield

mcp_http_app = mcp_server.streamable_http_app(transport_security=_mcp_security)

app = FastAPI(
    title="Macw1u's 3D",
    version="1.0.0",
    description="Tinkercad-style 3D modelling API. Build models from primitive shapes and holes.",
    servers=[{"url": BASE_URL}],
    lifespan=_lifespan,
)


def require_key(x_api_key: str | None = Header(default=None)):
    if API_KEY and not secrets.compare_digest(x_api_key or "", API_KEY):
        raise HTTPException(401, "Missing or invalid X-API-Key header.")


Vec3 = list[float]


class Shape(BaseModel):
    shape: Literal[tuple(engine.SHAPES)] = Field(description="Primitive type.")  # type: ignore[valid-type]
    name: str | None = Field(default=None, max_length=40)
    size: Vec3 = Field(default=[20, 20, 20], description="Bounding box [x, y, z] in mm.")
    position: Vec3 = Field(default=[0, 0, 0], description="[x, y, z]: x/y = centre, z = bottom of shape.")
    rotation: Vec3 = Field(default=[0, 0, 0], description="Degrees about own centre [rx, ry, rz].")
    mode: Literal["solid", "hole"] = "solid"
    color: str | None = Field(default=None, description="Name (red, blue...) or hex like #ff8800.")
    sides: int | None = Field(default=None, ge=3, le=128, description="Facets for cylinder/cone/prism/torus.")
    wall: float | None = Field(default=None, ge=0.02, le=0.9, description="Tube wall thickness ratio 0.02-0.9.")

    @field_validator("size", "position", "rotation")
    @classmethod
    def _len3(cls, v):
        if len(v) != 3:
            raise ValueError("must have exactly 3 numbers")
        if any(abs(x) > MAX_DIM for x in v):
            raise ValueError(f"values must be within +/-{MAX_DIM}")
        return v

    @field_validator("size")
    @classmethod
    def _positive(cls, v):
        if any(x <= 0 for x in v):
            raise ValueError("size values must be > 0")
        return v


class ModelRequest(BaseModel):
    name: str = Field(default="Untitled model", max_length=80)
    shapes: list[Shape] = Field(min_length=1)


def _paths(mid: str) -> Path:
    if not mid.isalnum():
        raise HTTPException(404, "Not found")
    return DATA_DIR / mid


def _urls(mid: str) -> dict[str, str]:
    return {
        "viewer_url": f"{BASE_URL}/viewer/{mid}",
        "preview_png_url": f"{BASE_URL}/files/{mid}/preview.png",
        "stl_url": f"{BASE_URL}/files/{mid}/model.stl",
        "glb_url": f"{BASE_URL}/files/{mid}/model.glb",
        "obj_url": f"{BASE_URL}/files/{mid}/model.obj",
    }


def _cleanup() -> None:
    cutoff = time.time() - KEEP_HOURS * 3600
    for d in DATA_DIR.iterdir():
        if d.is_dir() and d.stat().st_mtime < cutoff:
            shutil.rmtree(d, ignore_errors=True)


@app.post("/v1/models", operation_id="createModel", dependencies=[Depends(require_key)],
          summary="Create a 3D model from shapes")
def create_model(req: ModelRequest):
    if len(req.shapes) > MAX_SHAPES:
        raise HTTPException(422, f"Too many shapes (max {MAX_SHAPES}).")
    specs = [s.model_dump(exclude_none=True) for s in req.shapes]
    if not any(s["mode"] == "solid" for s in specs):
        raise HTTPException(422, "Need at least one solid shape.")
    try:
        parts, merged = engine.build_scene(specs)
    except Exception as e:
        raise HTTPException(422, f"Could not build model: {e}")
    if not parts:
        raise HTTPException(422, "Everything was cut away by holes - nothing left to show.")
    _cleanup()
    mid = secrets.token_hex(6)
    out = _paths(mid)
    stats = engine.export_all(parts, merged, out)
    meta = {"id": mid, "name": req.name, "shapes": specs, "stats": stats, "created": time.time()}
    (out / "scene.json").write_text(json.dumps(meta))
    return {"id": mid, "name": req.name, **_urls(mid), "stats": stats}


@app.get("/v1/models/{model_id}", operation_id="getModel", dependencies=[Depends(require_key)],
         summary="Get a model's shape list so it can be edited")
def get_model(model_id: str):
    f = _paths(model_id) / "scene.json"
    if not f.exists():
        raise HTTPException(404, "Model not found (they expire after a few days).")
    meta = json.loads(f.read_text())
    return {**meta, **_urls(model_id)}


@app.get("/files/{model_id}/{filename}", include_in_schema=False)
def files(model_id: str, filename: str):
    if filename not in {"model.stl", "model.glb", "model.obj", "preview.png"}:
        raise HTTPException(404, "Not found")
    f = _paths(model_id) / filename
    if not f.exists():
        raise HTTPException(404, "Not found")
    media = {"stl": "model/stl", "glb": "model/gltf-binary", "obj": "text/plain", "png": "image/png"}
    dl = filename if filename != "preview.png" else None
    return FileResponse(f, media_type=media[filename.rsplit(".", 1)[1]], filename=dl)


@app.get("/viewer/{model_id}", response_class=HTMLResponse, include_in_schema=False)
def viewer(model_id: str):
    f = _paths(model_id) / "scene.json"
    if not f.exists():
        return HTMLResponse("<p style='font:16px sans-serif;padding:2rem'>This model has expired.</p>", 404)
    meta = json.loads(f.read_text())
    html = VIEWER_HTML.replace("__ID__", model_id).replace("__NAME__", _esc(meta["name"]))
    return HTMLResponse(html)


@app.get("/privacy", response_class=PlainTextResponse, include_in_schema=False)
def privacy():
    return ("Macw1u's 3D stores only the shape list and generated files you request. "
            f"Files are deleted automatically after {KEEP_HOURS:g} hours. No personal data is collected.")


@app.get("/", response_class=PlainTextResponse, include_in_schema=False)
def root():
    return "Macw1u's 3D API is running. See /docs."


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


VIEWER_HTML = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__NAME__ - Macw1u's 3D</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;700&family=Inter:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{--bg:#eef3fb;--ink:#14213d;--mute:#5b6b8c;--line:#c9d5ee;--accent:#2f5bea;--panel:#fff}
@media (prefers-color-scheme:dark){:root{--bg:#0f1626;--ink:#e8eefc;--mute:#92a1c4;--line:#26334f;--accent:#6c8cff;--panel:#16203a}}
*{box-sizing:border-box}html,body{height:100%;margin:0}
body{background:var(--bg);color:var(--ink);font:15px/1.5 Inter,system-ui,sans-serif;display:flex;flex-direction:column}
header{display:flex;align-items:center;gap:16px;flex-wrap:wrap;padding:12px 18px;background:var(--panel);border-bottom:1px solid var(--line)}
h1{font:700 18px "Space Grotesk",sans-serif;margin:0;letter-spacing:-.01em}
.sp{flex:1}
button,a.btn{font:500 14px Inter,sans-serif;color:var(--ink);background:transparent;border:1px solid var(--line);border-radius:6px;padding:7px 12px;cursor:pointer;text-decoration:none}
button:hover,a.btn:hover{border-color:var(--accent);color:var(--accent)}
a.primary{background:var(--accent);border-color:var(--accent);color:#fff}a.primary:hover{color:#fff;opacity:.9}
button:focus-visible,a.btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
button[aria-pressed=true]{border-color:var(--accent);color:var(--accent)}
#stage{flex:1;position:relative;min-height:0}
canvas{display:block;width:100%;height:100%;touch-action:none}
#hint{position:absolute;left:14px;bottom:12px;color:var(--mute);font-size:13px}
#err{position:absolute;inset:0;display:none;place-items:center;color:var(--mute)}
</style></head><body>
<header>
  <h1>__NAME__</h1><span class="sp"></span>
  <button id="wire" aria-pressed="false">Wireframe</button>
  <button id="spin" aria-pressed="false">Turntable</button>
  <a class="btn" href="/files/__ID__/model.glb">GLB</a>
  <a class="btn" href="/files/__ID__/model.obj">OBJ</a>
  <a class="btn primary" href="/files/__ID__/model.stl">Download STL</a>
</header>
<div id="stage"><canvas id="c"></canvas><div id="hint">Drag to orbit, scroll to zoom, right-drag to pan</div><div id="err">Could not load the model.</div></div>
<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}</script>
<script type="module">
import * as THREE from "three";
import {OrbitControls} from "three/addons/controls/OrbitControls.js";
import {GLTFLoader} from "three/addons/loaders/GLTFLoader.js";
const stage=document.getElementById("stage"),canvas=document.getElementById("c");
const dark=matchMedia("(prefers-color-scheme:dark)").matches;
const renderer=new THREE.WebGLRenderer({canvas,antialias:true});
renderer.setPixelRatio(Math.min(devicePixelRatio,2));
const scene=new THREE.Scene();scene.background=new THREE.Color(dark?0x0f1626:0xeef3fb);
const cam=new THREE.PerspectiveCamera(40,1,.1,10000);
scene.add(new THREE.HemisphereLight(0xffffff,dark?0x223055:0x9fb2d8,1.1));
const sun=new THREE.DirectionalLight(0xffffff,1.6);sun.position.set(1,2,1.4);scene.add(sun);
const controls=new OrbitControls(cam,canvas);controls.enableDamping=true;
const mats=[];
new GLTFLoader().load("/files/__ID__/model.glb",g=>{
  g.scene.traverse(o=>{if(o.isMesh){o.material=new THREE.MeshStandardMaterial({color:o.material.color||0x3e63dd,roughness:.55,metalness:.05});mats.push(o.material)}});
  scene.add(g.scene);
  const box=new THREE.Box3().setFromObject(g.scene),size=box.getSize(new THREE.Vector3()),ctr=box.getCenter(new THREE.Vector3());
  const r=Math.max(size.x,size.y,size.z);
  const grid=new THREE.GridHelper(Math.max(r*2.4,40),Math.round(Math.max(r*2.4,40)/10),dark?0x40527f:0x7f98d6,dark?0x26334f:0xc9d5ee);
  grid.position.y=box.min.y-.01;scene.add(grid);
  controls.target.copy(ctr);cam.position.set(ctr.x+r*1.5,ctr.y+r*1.1,ctr.z+r*1.8);cam.near=r/100;cam.far=r*100;cam.updateProjectionMatrix();
},undefined,()=>{document.getElementById("err").style.display="grid"});
const wire=document.getElementById("wire"),spin=document.getElementById("spin");
wire.onclick=()=>{const on=wire.getAttribute("aria-pressed")!=="true";wire.setAttribute("aria-pressed",on);mats.forEach(m=>m.wireframe=on)};
spin.onclick=()=>{const on=spin.getAttribute("aria-pressed")!=="true";spin.setAttribute("aria-pressed",on);controls.autoRotate=on};
function resize(){const w=stage.clientWidth,h=stage.clientHeight;renderer.setSize(w,h,false);cam.aspect=w/h;cam.updateProjectionMatrix()}
new ResizeObserver(resize).observe(stage);resize();
renderer.setAnimationLoop(()=>{controls.update();renderer.render(scene,cam)});
</script></body></html>"""


# Model Context Protocol tools
@mcp_server.tool()
def create_3d_model(name: str, shapes: list[dict[str, Any]]) -> dict[str, Any]:
    """Create a 3D model from primitive shapes and holes.

    Each shape uses: shape (box, cylinder, sphere, half_sphere, cone, pyramid,
    wedge, roof, torus, tube, prism), size [x,y,z] in mm, position [x,y,z]
    where z is the bottom, rotation [rx,ry,rz] in degrees, mode solid or hole,
    and optional color, sides, wall, and name. Holids cut earlier solids.
    Returns viewer, preview, STL, GLB and OBJ URLs plus mesh statistics.
    """
    try:
        request = ModelRequest.model_validate({"name": name, "shapes": shapes})
        return create_model(request)
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


@mcp_server.tool()
def get_3d_model(model_id: str) -> dict[str, Any]:
    """Get a previously created model's shape list, viewer and download URLs.

    Use this to inspect a model before changing or recreating it. Models expire
    automatically after the configured retention period.
    """
    try:
        return get_model(model_id)
    except HTTPException as exc:
        raise ValueError(str(exc.detail)) from exc


@mcp_server.tool()
def list_supported_3d_shapes() -> dict[str, Any]:
    """List the primitive shapes accepted by Macw1u's 3D model builder."""
    return {"shapes": engine.SHAPES, "units": "millimetres", "axes": "Z is up"}


# Keep this mount last: existing FastAPI routes must be matched before the
# catch-all mount. The MCP endpoint is then available at /mcp.
app.mount("/", mcp_http_app)
