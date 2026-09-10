"""Authored metric city kit; helpers accept the runtime's X/right, Y/up, Z/depth."""

import math

import bpy
from mathutils import Vector

ACTIVE = None
MATS = {}


def xyz(point):
    return Vector((point[0], -point[2], point[1]))


def surface(name, rgb, roughness=0.6, metallic=0, emission=0):
    mat = bpy.data.materials.new(name)
    mat.diffuse_color = (*rgb, 1)
    mat.use_nodes = True
    shader = mat.node_tree.nodes.get("Principled BSDF")
    shader.inputs["Base Color"].default_value = (*rgb, 1)
    shader.inputs["Roughness"].default_value = roughness
    shader.inputs["Metallic"].default_value = metallic
    shader.inputs["Emission Color"].default_value = (*rgb, 1)
    shader.inputs["Emission Strength"].default_value = emission
    MATS[name] = mat
    return mat


def begin(name):
    global ACTIVE
    old = bpy.data.collections.get(name)
    if old:
        for obj in list(old.all_objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(old)
    ACTIVE = bpy.data.collections.new(name)
    bpy.context.scene.collection.children.link(ACTIVE)


def assign(obj, name, mat):
    obj.name = name
    for group in list(obj.users_collection):
        group.objects.unlink(obj)
    ACTIVE.objects.link(obj)
    if mat:
        obj.data.materials.append(MATS[mat])
    return obj


def box(name, at, size, mat="Concrete", bevel=0.08):
    bpy.ops.mesh.primitive_cube_add(size=1, location=xyz(at))
    obj = bpy.context.object
    obj.scale = (size[0], size[2], size[1])
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        mod = obj.modifiers.new("Authored edge radius", "BEVEL")
        mod.width = min(bevel, min(size) / 3)
        mod.segments = 3
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return assign(obj, name, mat)


def beam(name, a, b, width, mat="Graphite"):
    delta = xyz(b) - xyz(a)
    center = (Vector(a) + Vector(b)) / 2
    obj = box(name, center, (width, delta.length, width), mat)
    obj.rotation_euler = delta.to_track_quat("Z", "Y").to_euler()
    return obj


def cylinder(name, at, radius, depth, mat="Metal", vertices=24):
    bpy.ops.mesh.primitive_cylinder_add(
        vertices=vertices, radius=radius, depth=depth, location=xyz(at)
    )
    return assign(bpy.context.object, name, mat)


def text_mesh(name, text, at, size, mat="Light"):
    bpy.ops.object.text_add(location=xyz(at), rotation=(math.pi / 2, 0, 0))
    obj = bpy.context.object
    obj.data.body = text
    obj.data.size = size
    obj.data.align_x = "CENTER"
    obj.data.extrude = 0.008
    obj.data.bevel_depth = 0.002
    # Text faces runtime -Z, the common building frontage.
    obj.rotation_euler = (math.pi / 2, 0, math.pi)
    bpy.ops.object.convert(target="MESH")
    return assign(bpy.context.object, name, mat)


def anchor(name, at):
    obj = bpy.data.objects.new(name, None)
    obj.location = xyz(at)
    ACTIVE.objects.link(obj)
    obj["city_anchor"] = True
    return obj


def tree(at=(0, 0, 0), scale=1):
    x, y, z = at

    def p(dx, dy, dz):
        return (x + dx * scale, y + dy * scale, z + dz * scale)

    beam("Trunk", p(0, 0, 0), p(0.1, 4.2, 0), 0.22 * scale, "Bark")
    for i, (dx, dy, dz, radius) in enumerate(
        ((-1, 4.3, 0, 1.7), (1, 5, 0.6, 1.9), (0, 6, 0, 1.7), (0, 4.8, -1.1, 1.4))
    ):
        beam("Branch", p(0, 2.8, 0), p(dx, dy, dz), 0.1 * scale, "Bark")
        bpy.ops.mesh.primitive_ico_sphere_add(
            subdivisions=2, radius=radius * scale, location=xyz(p(dx, dy, dz))
        )
        assign(bpy.context.object, "Foliage", "LeafLight" if i % 2 else "Leaf")


def planter(at, width, depth, trees=True):
    x, y, z = at
    box("Planter", (x, y + 0.42, z), (width, 0.84, depth), "Concrete", 0.15)
    box("Soil", (x, y + 0.87, z), (width - 0.4, 0.06, depth - 0.4), "Soil", 0)
    if trees:
        tree((x, y + 0.9, z), 0.7)


def optimize_static():
    """Keep dynamic doors and named anchors; batch static surfaces by material."""
    groups = {}
    for obj in list(ACTIVE.objects):
        if obj.type == "MESH" and obj.parent is None:
            key = tuple(mat.name for mat in obj.data.materials)
            groups.setdefault(key, []).append(obj)
    for mats, objects in groups.items():
        if len(objects) < 2:
            continue
        bpy.ops.object.select_all(action="DESELECT")
        for obj in objects:
            obj.select_set(True)
        bpy.context.view_layer.objects.active = objects[0]
        bpy.ops.object.join()
        bpy.context.object.name = f"{ACTIVE.name}_{'_'.join(mats)}"


def station():
    begin("Station")
    box("Platform", (0, -0.3, 8), (38, 0.6, 10))
    box("Floating canopy", (0, 7.1, 8), (44, 0.7, 15), "Graphite", 0.25)
    box("Aluminium roof", (0, 7.55, 8), (44.5, 0.2, 15.6), "Pearl", 0.08)
    for x in (-15, 15):
        beam("V column", (x, 0, 10), (x - 3.5, 6.8, 8), 0.42)
        beam("V column", (x, 0, 10), (x + 3.5, 6.8, 8), 0.42)
    for z in (0.5, 15.5):
        box("Linear lighting", (0, 6.69, z), (43, 0.1, 0.1), "Light", 0)
    for x in range(-18, 19, 6):
        box("Windbreak", (x, 2, 12.6), (5.7, 4, 0.12), "Glazing")
        box("Windbreak frame", (x - 2.9, 2, 12.6), (0.12, 4, 0.2), "Metal")
    for x in range(-17, 18, 2):
        box("Tactile boarding studs", (x, 0.025, 3.7), (1.7, 0.05, 0.3), "Amber", 0)
    box("Boarding safety strip", (0, 0.04, 3.2), (37, 0.08, 0.17), "Cyan", 0)
    for x in (-10, 10):
        box("Bench seat", (x, 0.48, 10), (4, 0.14, 0.75), "Metal")
        box("Bench back", (x, 0.9, 10.35), (4, 0.85, 0.1), "Metal")
        for offset in (-1.4, 1.4):
            box("Bench base", (x + offset, 0.21, 10), (0.18, 0.42, 0.5), "Graphite")
    for x in (-10, 10):
        box("Metro display housing", (x, 4.8, 5.5), (9, 1.5, 0.32), "Graphite")
        box("Metro display", (x, 4.8, 5.3), (8.6, 1.1, 0.03), "Screen")
        text_mesh("Destination display", "M1  CITY LINE", (x, 4.5, 5.25), 0.65, "Cyan")
    anchor("Station_Boarding", (0, 0, 3.2))
    anchor("Station_Lift", (0, 0, 16))
    optimize_static()


def loft(name, sections, mat):
    """Eight-sided rounded coach cross section, lofted along the rail axis."""
    perimeter = [
        (0.25, -2.0),
        (0.6, -2.45),
        (3.2, -2.45),
        (3.7, -1.9),
        (3.7, 1.9),
        (3.2, 2.45),
        (0.6, 2.45),
        (0.25, 2.0),
    ]
    verts = [xyz((x, y * sy, z * sz)) for x, sy, sz in sections for y, z in perimeter]
    faces = [tuple(reversed(range(8))), tuple(range(len(verts) - 8, len(verts)))]
    for ring in range(len(sections) - 1):
        for i in range(8):
            j = (i + 1) % 8
            faces.append((ring * 8 + i, ring * 8 + j, (ring + 1) * 8 + j, (ring + 1) * 8 + i))
    if sections[-1][0] < sections[0][0]:
        faces = [tuple(reversed(face)) for face in faces]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    ACTIVE.objects.link(obj)
    obj.data.materials.append(MATS[mat])
    bevel = obj.modifiers.new("Coach corner radius", "BEVEL")
    bevel.width = 0.14
    bevel.segments = 3
    bpy.context.view_layer.objects.active = obj
    bpy.ops.object.modifier_apply(modifier=bevel.name)
    return obj


def train():
    begin("Train")
    box("Cabin floor", (0, 0, 0), (18.5, 0.16, 4.65), "Metal", 0.08)
    box("Coach lower sill", (0, 0.36, -2.35), (17, 0.65, 0.22), "Pearl", 0.12)
    box("Coach roof", (0, 3.65, 0), (18.3, 0.4, 4.95), "Pearl", 0.18)
    box("Roof equipment", (0, 3.96, 0), (9, 0.3, 2.7), "Graphite", 0.1)
    for side in (-1, 1):
        z = side * 2.4
        for x in (-5.1, 5.1):
            box("Coach body side", (x, 0.65, z), (6.5, 1.2, 0.18), "Pearl", 0.08)
            box("Panoramic window strip", (x, 2.25, z), (6.25, 1.8, 0.09), "Glazing", 0.05)
            for offset in (-2, 0, 2):
                box(
                    "Window mullion",
                    (x + offset, 2.24, z + side * 0.06),
                    (0.1, 1.84, 0.06),
                    "Graphite",
                    0.01,
                )
            box("Turquoise identity line", (x, 1.17, z + side * 0.1), (6.6, 0.15, 0.035), "Cyan", 0)
        box("Upper side belt", (0, 3.3, z), (17, 0.36, 0.14), "Graphite", 0.04)
        box("Door header", (0, 3.15, z), (3.3, 0.25, 0.25), "Metal", 0.03)
        for x in (-1.62, 1.62):
            box("Door gasket", (x, 1.55, z), (0.12, 3.1, 0.2), "Graphite", 0.02)
    for direction in (-1, 1):
        loft(
            "Aerodynamic cab",
            [
                (direction * 8.4, 1, 1),
                (direction * 9.2, 0.97, 0.93),
                (direction * 10.2, 0.82, 0.66),
            ],
            "Pearl",
        )
        visor = box(
            "Cab wraparound windscreen",
            (direction * 10.24, 2.18, 0),
            (0.1, 1.52, 3.28),
            "Graphite",
            0.03,
        )
        visor.rotation_euler.y = direction * -0.18
        for z in (-1.25, 1.25):
            box("LED headlamp", (direction * 10.21, 0.89, z), (0.055, 0.22, 0.54), "Light", 0.04)
        box("Nose trim", (direction * 10.23, 0.6, 0), (0.04, 0.09, 2.7), "Cyan", 0.01)
    for x in (-5.8, 5.8):
        box("Bogie", (x, -0.48, 0), (3.1, 0.8, 3.5), "Graphite", 0.18)
        for dx in (-0.95, 0.95):
            for z in (-1.7, 1.7):
                wheel = cylinder("Wheel", (x + dx, -0.62, z), 0.5, 0.26, "Graphite")
                wheel.rotation_euler.x = math.pi / 2
    for side in (-1, 1):
        for x in (-6, -4, 4, 6):
            box("Passenger seat", (x, 0.51, side * 1.62), (1.55, 0.18, 0.66), "Upholstery", 0.09)
            box("Passenger back", (x, 0.99, side * 1.96), (1.55, 0.83, 0.12), "Upholstery", 0.08)
        beam("Handrail", (-7, 3.02, side * 1.25), (7, 3.02, side * 1.25), 0.07, "Amber")
    for x in (-2, 2):
        beam("Grab pole", (x, 0.1, 0), (x, 3.35, 0), 0.065, "Amber")
    box("Cabin ceiling light", (0, 3.41, 0), (15, 0.07, 0.22), "Light", 0.02)
    optimize_static()
    for sign, suffix in ((-1, "Left"), (1, "Right")):
        parent = anchor(f"Door_Platform_{suffix}", (sign * 0.7, 0, 0))
        parent["open_offset_x"] = sign * 1.25
        for name, at, size, mat in (
            ("Door leaf", (sign * 0.7, 1.5, 2.47), (1.36, 3, 0.12), "Metal"),
            ("Door glass", (sign * 0.7, 2, 2.55), (1.05, 1.62, 0.025), "Glazing"),
            ("Door safety light", (sign * 0.7, 2.94, 2.56), (1.1, 0.07, 0.035), "Cyan"),
        ):
            obj = box(name, at, size, mat, 0.025)
            world = obj.matrix_world.copy()
            obj.parent = parent
            obj.matrix_world = world
    anchor("Train_Boarding", (0, 0, 2.55))
    anchor("Train_Cabin", (0, 0.08, 0))


def workstation(x, z, index):
    box("Work desk", (x, 0.79, z + 0.8), (2.65, 0.12, 1.12), "Metal", 0.05)
    for dx in (-1.1, 1.1):
        box("Desk leg", (x + dx, 0.37, z + 0.8), (0.08, 0.74, 0.7), "Graphite", 0.02)
    box("Monitor stand", (x, 1.07, z + 1.08), (0.12, 0.45, 0.12), "Graphite")
    box("Ultrawide monitor", (x, 1.58, z + 1.1), (2.28, 0.97, 0.14), "Graphite", 0.05)
    box(
        f"Terminal_Screen_{index:02}", (x, 1.58, z + 1.012), (2.12, 0.82, 0.02), "TerminalScreen", 0
    )
    for row, width in enumerate((1.45, 0.9, 1.65, 1.1, 0.55)):
        box(
            "Code line",
            (x - 0.87 + width / 2, 1.85 - row * 0.13, z + 0.995),
            (width, 0.035, 0.015),
            "Cyan" if row % 2 else "ScreenText",
            0,
        )
    box("Keyboard", (x - 0.2, 0.88, z + 0.45), (1.02, 0.06, 0.33), "Graphite", 0.02)
    box("Mouse", (x + 0.65, 0.89, z + 0.45), (0.13, 0.07, 0.2), "Graphite", 0.03)
    # Park the chair beside the standing workstation, clear of its approach path.
    box("Chair seat", (x + 1.8, 0.48, z + 0.6), (0.65, 0.15, 0.65), "Upholstery", 0.06)
    box("Chair back", (x + 1.8, 0.94, z + 0.3), (0.64, 0.85, 0.1), "Upholstery", 0.05)
    cylinder("Chair pedestal", (x + 1.8, 0.22, z + 0.6), 0.08, 0.44)
    anchor(f"Workstation_{index:02}", (x, 0, z))


def building(name, title, accent, terminal=False):
    begin(name)
    box("Ground slab", (0, -0.22, 0), (42, 0.44, 38), "Concrete")
    box("Rear service core", (0, 15.3, 16), (30, 17.4, 5), "Graphite")
    # Open ground floor faces the plaza, making desks and agent activity visible.
    for level in (6.3, 13, 20):
        box("Cantilever floor", (0, level, 0), (43, 0.55, 38), "Concrete", 0.1)
        box("Floor edge reveal", (0, level - 0.23, -19.15), (42.6, 0.12, 0.13), accent, 0)
        if level < 20:
            for x in range(-18, 19, 6):
                box("Recessed glazing", (x, level + 3.2, -14), (5.76, 5.7, 0.15), "Glazing")
                box("Aluminium fin", (x - 3, level + 3.1, -14.25), (0.18, 6.1, 0.58), "Metal")
            for x in (-20, 20):
                box("Side glazing", (x, level + 3.2, 0), (0.16, 5.7, 31), "Glazing")
    for x in (-19.5, 19.5):
        for z in (-14, 10):
            beam("Diagonal megaframe", (x, 0, z + 3), (x, 20, z - 5), 0.72)
    box("Floating roof", (0, 20.8, 0), (48, 1.15, 42), "Graphite", 0.24)
    box("Roof crown", (0, 21.48, 0), (48.2, 0.22, 42.2), "Pearl", 0.08)
    box("Roof perimeter light", (0, 20.23, -21.04), (47, 0.1, 0.08), "Light", 0)
    box("Arrival canopy", (0, 5.7, -19.5), (36, 0.45, 6.2), "Graphite", 0.1)
    text_mesh("Building identity", title, (0, 6.9, -19.4), 1.65, accent)
    for x in (-17, 17):
        planter((x, 21.62, 1), 4, 26)
    anchor("Building_Entrance", (0, 0, -19))
    if terminal:
        for row, z in enumerate((-9, -5, -1, 3, 7, 11)):
            for col, x in enumerate((-12, -6, 0, 6, 12)):
                workstation(x, z, row * 5 + col)
        for x in (-16.5, 16.5):
            for z in (-4, 2, 8):
                box("Server cabinet", (x, 1.9, z), (1.8, 3.8, 1.8), "Graphite")
                for level in range(10):
                    box(
                        "Server status",
                        (x, 0.4 + level * 0.3, z - 0.92),
                        (1.3, 0.045, 0.03),
                        "Cyan",
                        0,
                    )
        text_mesh("Terminal command sign", ">_", (0, 15, -14.35), 4.5, "Cyan")
    else:
        # Reception stays beside the entry-to-work aisle.
        box("Reception desk", (-12, 1, -13), (10, 2, 2.4), "Pearl", 0.25)
        box("Reception trim", (-12, 1.1, -14.23), (9.5, 0.2, 0.05), accent, 0)
        for row, z in enumerate((-8, -3, 2, 7, 12, 16)):
            for col, x in enumerate((-12, -6, 0, 6, 12)):
                anchor(f"Interaction_{row * 5 + col:02}", (x, 0, z))
        for x in (-17, 17):
            planter((x, 0, 4), 4, 5)
        box("Mission wall", (0, 3.1, 18.35), (25, 4.2, 0.1), "Screen")
        for x in range(-10, 11, 5):
            box("Mission column", (x, 3.1, 18.25), (3.6, 3.7, 0.04), accent)
    optimize_static()


def landmark(name, title, accent, height):
    building(name, title, accent)
    # Roof structures establish distinct silhouettes at city viewing distance.
    if name == "Knowledge":
        for i in range(3):
            box(
                "Archive terrace", (0, 26 + i * 6, i * 3), (30 - i * 7, 4, 23 - i * 4), "Pearl", 0.3
            )
            box(
                "Archive glazing",
                (0, 26 + i * 6, -11.6 + i * 5),
                (29 - i * 7, 2.8, 0.12),
                "Glazing",
            )
    elif name == "Fabrication":
        for x in (-10, 10):
            box("Fabrication gantry", (x, 28, 0), (3, 13, 21), "Graphite")
        box("Gantry beam", (0, 34, 0), (30, 3, 5), accent)
        beam("Fabrication tool", (0, 32, 0), (0, 24, 0), 1.2, "Metal")
    else:
        for x in (-8, 8):
            box("Comms spire", (x, 21 + height / 2, 3), (7, height, 9), "Graphite")
            box("Comms light", (x, 21 + height / 2, -1.6), (0.26, height - 2, 0.1), accent)
        for level in (35, 45, 55):
            box("Sky connection", (0, level, 3), (24, 1, 5), "Metal")
    optimize_static()


def author_city_architecture():
    surface("Concrete", (0.48, 0.46, 0.42), 0.82)
    surface("Graphite", (0.021, 0.029, 0.036), 0.4, 0.5)
    surface("Metal", (0.4, 0.46, 0.5), 0.32, 0.7)
    surface("Pearl", (0.76, 0.79, 0.77), 0.34, 0.35)
    surface("Glazing", (0.052, 0.13, 0.17), 0.16, 0.35)
    surface("Light", (1, 0.73, 0.4), 0.4, 0, 1.1)
    surface("Cyan", (0.07, 0.68, 0.83), 0.4, 0.1, 0.7)
    surface("Gold", (0.8, 0.54, 0.19), 0.36, 0.4, 0.25)
    surface("Blue", (0.19, 0.34, 0.8), 0.4, 0.15, 0.4)
    surface("Orange", (0.9, 0.3, 0.065), 0.4, 0.1, 0.35)
    surface("Teal", (0.09, 0.64, 0.42), 0.4, 0.15, 0.35)
    surface("Amber", (0.86, 0.56, 0.09), 0.55)
    surface("Screen", (0.008, 0.023, 0.026), 0.27)
    surface("TerminalScreen", (0.008, 0.045, 0.052), 0.27, 0, 0.1)
    surface("ScreenText", (0.45, 0.92, 0.65), 0.5, 0, 0.65)
    surface("Upholstery", (0.04, 0.2, 0.24), 0.85)
    surface("Leaf", (0.095, 0.19, 0.055), 0.9)
    surface("LeafLight", (0.19, 0.28, 0.09), 0.9)
    surface("Bark", (0.11, 0.065, 0.03), 0.95)
    surface("Soil", (0.055, 0.04, 0.027), 1)
    station()
    train()
    building("Building", "TERMINAL / COMPUTE", "Cyan", terminal=True)
    building("Central", "CENTRAL / CIVIC HALL", "Gold")
    landmark("Knowledge", "KNOWLEDGE / ARCHIVE", "Blue", 18)
    landmark("Fabrication", "FABRICATION / LAB", "Orange", 18)
    landmark("Communications", "COMMUNICATIONS", "Teal", 44)
    begin("Tree")
    tree()
    optimize_static()
    for name, height, width in (
        ("SkylineOffice", 36, 26),
        ("SkylineTower", 76, 24),
        ("SkylineResidence", 24, 34),
    ):
        begin(name)
        box("Foundation", (0, 0.3, 0), (width + 2, 0.6, 26), "Concrete", 0)
        box("Facade mass", (0, height / 2, 0), (width, height, 24), "Glazing", 0)
        for y in range(3, height, 4):
            box("Floor belt", (0, y, 0), (width + 0.5, 0.45, 24.5), "Concrete", 0)
        for x in range(-width // 2, width // 2 + 1, 5):
            for z in (-12.1, 12.1):
                box("Facade fin", (x, height / 2, z), (0.26, height, 0.4), "Metal", 0)
        box("Roof cap", (0, height + 0.4, 0), (width + 1.5, 0.8, 25.5), "Concrete", 0)
        if name == "SkylineTower":
            box("Penthouse", (0, height + 5, 0), (width - 8, 10, 18), "Glazing", 0)
            box("Crown", (0, height + 10.4, 0), (width - 6, 0.8, 20), "Metal", 0)
        if name == "SkylineResidence":
            for y in range(4, height, 4):
                for x in (-11, 0, 11):
                    box("Balcony", (x, y, -13.5), (8, 0.35, 3.5), "Concrete", 0)
                    box("Balcony balustrade", (x, y + 0.75, -15.1), (8, 1.1, 0.12), "Glazing", 0)
        optimize_static()
    begin("Streetlamp")
    cylinder("Foot", (0, 0.06, 0), 0.32, 0.12, "Graphite", 16)
    beam("Light mast", (0, 0, 0), (0, 7, 0), 0.14, "Graphite")
    beam("Light arm", (0, 7, 0), (1.8, 7.3, 0), 0.13, "Graphite")
    box("Luminaire", (1.55, 7.25, 0), (1.1, 0.12, 0.4), "Metal", 0.03)
    box("Light panel", (1.55, 7.17, 0), (0.9, 0.03, 0.3), "Light", 0)
    optimize_static()
