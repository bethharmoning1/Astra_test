#!/usr/bin/env python3
"""Fresh CPU deployment check; no bridge runner/socket is started."""
from __future__ import annotations
import ctypes
import ctypes.util
from importlib.metadata import version
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent


def main():
    if not (sys.version_info.major == 3 and sys.version_info.minor == 11):
        raise RuntimeError(f"Use Python 3.11, got {sys.version}")
    for key in ("MUJOCO_GL", "PYOPENGL_PLATFORM"):
        if os.environ.get(key) != "osmesa":
            raise RuntimeError(f"Set {key}=osmesa before starting Python (source env_cpu.sh)")
    library = ctypes.util.find_library("OSMesa")
    if not library:
        raise RuntimeError("OSMesa library not found; ask administrator to install libosmesa6")
    ctypes.CDLL(library)
    print("Python:", sys.version.split()[0])
    print("OSMesa library:", library)
    for package in ("dexjoco", "mujoco", "gymnasium", "numpy", "scipy", "pillow", "PyOpenGL", "PyYAML"):
        print(f"{package}: {version(package)}")

    import dexjoco
    import mujoco
    import numpy as np
    from OpenGL import GL
    from PIL import Image

    actual_source = Path(dexjoco.__file__).resolve()
    expected_source = (ROOT / "dexjoco" / "dexjoco").resolve()
    if actual_source.parent != expected_source:
        raise RuntimeError(f"Wrong DexJoCo source: {actual_source}; expected {expected_source}")
    print("DexJoCo source:", actual_source)
    print("Upstream commit:", (ROOT / "UPSTREAM_COMMIT.txt").read_text().strip())
    if not (ROOT / "configs" / "rand_obj" / "click_mouse.yaml").is_file():
        raise RuntimeError("Official task configuration missing")
    if not (expected_source / "data").is_dir():
        raise RuntimeError("DexJoCo assets missing")

    xml = """<mujoco>
      <visual><global offwidth="640" offheight="640"/></visual>
      <worldbody>
        <light pos="0 0 3" dir="0 0 -1"/>
        <camera name="check" pos="2 -2 2" xyaxes="0.707 0.707 0 -0.408 0.408 0.816"/>
        <geom name="ground" type="plane" size="3 3 .1" rgba=".3 .5 .3 1"/>
        <body pos="0 0 .3"><freejoint/><geom type="sphere" size=".2" mass="1" rgba=".9 .2 .1 1"/></body>
      </worldbody>
    </mujoco>"""
    model = mujoco.MjModel.from_xml_string(xml)
    data = mujoco.MjData(model)
    for _ in range(10):
        mujoco.mj_step(model, data)
    renderer = mujoco.Renderer(model, height=640, width=640)
    try:
        renderer.update_scene(data, camera="check")
        image = renderer.render().copy()
        gl_renderer = GL.glGetString(GL.GL_RENDERER)
        print("OpenGL renderer:", gl_renderer.decode("utf-8", errors="replace") if gl_renderer else "unavailable")
    finally:
        renderer.close()
    if image.shape != (640, 640, 3) or image.dtype != np.uint8 or float(image.std()) < 1.0:
        raise RuntimeError(f"Invalid/constant image: shape={image.shape}, dtype={image.dtype}, std={image.std()}")
    output = ROOT / "artifacts" / "install-check" / "osmesa-smoke.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(image).save(output)
    print("CPU_INSTALL_RENDER_CHECK_OK")
    print("Image:", output)
    print("Independent scene check, NOT full DexJoCo task/performance acceptance.")


if __name__ == "__main__":
    main()
