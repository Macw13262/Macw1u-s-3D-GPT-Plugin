# Macw1u's 3D

A Tinkercad-style 3D modelling backend with a **Model Context Protocol (MCP) server** and a REST API. Describe an object and the server builds a 3D model from primitive shapes and holes.

It can return:
- an interactive 3D viewer (orbit, zoom, wireframe, turntable)
- a rendered PNG preview
- STL for 3D printing, plus coloured GLB and OBJ exports

## Supported shapes

box, cylinder, sphere, half_sphere, cone, pyramid, wedge, roof, torus, tube, and prism.

Each shape supports dimensions, position, rotation, colour, and solid/hole mode.

## Run locally

```bash
pip install -r requirements.txt
API_KEY=choose-a-secret PUBLIC_BASE_URL=http://localhost:8000 uvicorn server:app --port 8000
```

API documentation: http://localhost:8000/docs

MCP endpoint (Streamable HTTP): `http://localhost:8000/mcp`

## MCP tools

- `create_3d_model` — create a model from a list of primitive shapes and holes; returns viewer, preview, STL, GLB and OBJ URLs plus mesh statistics.
- `get_3d_model` — retrieve a saved model's shape list and generated file URLs.
- `list_supported_3d_shapes` — list supported primitive types and modelling units.

For a deployed service, connect an MCP client to `https://YOUR-SERVICE-HOST/mcp`. The MCP route is mounted alongside the existing REST API. The server validates the public hostname to protect against DNS-rebinding requests.

## Deploy

The application is containerized and can be deployed to a container hosting provider such as Render, Railway, Fly.io, or Google Cloud Run.

1. Create a web service from this GitHub repository using its Dockerfile.
2. Set `API_KEY` to a long, random secret. Do not publish this value.
3. Set `PUBLIC_BASE_URL` to the public HTTPS address assigned by the hosting provider, without a trailing slash.
4. Optionally configure `DATA_DIR`, `KEEP_HOURS` (default 72), and `MAX_SHAPES` (default 150).
5. Open the service URL and confirm that the API responds.

For persistent generated files, attach persistent storage and set `DATA_DIR` to its mount path. Free hosting plans may sleep or use temporary storage.

## Connect an MCP client

Use the deployed HTTPS MCP endpoint: `https://YOUR-SERVICE-HOST/mcp` with the Streamable HTTP transport. The MCP endpoint currently uses the hosting provider's HTTPS boundary and does not yet implement per-user OAuth/API-key authentication. Do not treat the endpoint as private until authentication is configured.

The REST API remains available at `/docs` and retains its optional `X-API-Key` protection.

## License

This repository is protected by the **Macw1u's 3D Proprietary License** in [LICENSE](LICENSE). All rights are reserved. No permission is granted to copy, use, modify, or distribute the project without prior written permission. Third-party dependencies remain subject to their own licenses.
