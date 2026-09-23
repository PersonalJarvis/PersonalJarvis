"""Render the existing Gigi source as the small profile mark, without AI services."""

from pathlib import Path

import bpy
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[2]
source = ROOT / "art/studies/agent-symbol-companions/source/gigi-reference.blend"
bpy.ops.wm.open_mainfile(filepath=str(source), use_scripts=False)
scene = bpy.context.scene
camera = scene.camera
camera.location = (0.17, -1.2, 0.43)
camera.rotation_euler = (
    (Vector((0, 0, 0.205)) - camera.location).to_track_quat("-Z", "Y").to_euler()
)
camera.data.ortho_scale = 0.50
scene.render.engine = "CYCLES"
scene.cycles.samples = 32
scene.render.resolution_x = scene.render.resolution_y = 512
scene.render.resolution_percentage = 100
scene.render.film_transparent = True
scene.render.image_settings.file_format = "PNG"
scene.render.image_settings.color_mode = "RGBA"
scene.render.filepath = str(ROOT / "jarvis/ui/web/frontend/src/assets/gigi-companion-avatar.png")
bpy.ops.render.render(write_still=True)
