# Renders the volumetric smoke + tracer sprites used by windtunnel.js.
#   blender -b --factory-startup -P blender/build_smoke.py
# Outputs (assets/smoke/):
#   smoke-atlas.webp  8x4 grid of 256px Cycles volume renders (filaments, puffs, curls)
#   motes.webp        4x1 grid of 128px tracer sprites (glow mote, round bokeh, hex bokeh, streak)
#   wisp.webp         512px tileable wisp-detail sheet (alpha = density) for advected dye texturing
#   atlas.json        layout + alpha note (all images are straight / unassociated alpha)
import bpy, os, math, random, json, shutil, tempfile
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
OUT = os.path.join(ROOT, 'assets', 'smoke')
TMP = tempfile.mkdtemp(prefix='smoke_')
os.makedirs(OUT, exist_ok=True)
CELL, COLS, ROWS = 256, 8, 4
random.seed(7)
ONLY_MOTES = os.environ.get('SMOKE_ONLY') == 'motes'   # fast iteration on the tracer sprites

# ---------- render setup: Cycles on Metal GPU + OIDN ----------
scene = bpy.context.scene
scene.render.engine = 'CYCLES'
prefs = bpy.context.preferences.addons['cycles'].preferences
try:
    prefs.compute_device_type = 'METAL'
    prefs.get_devices()
    for d in prefs.devices:
        d.use = d.type == 'METAL'
    scene.cycles.device = 'GPU'
except Exception as e:  # CPU still works, just slower
    print('Metal unavailable, using CPU:', e)
c = scene.cycles
c.samples = 128
c.use_denoising = True
c.denoiser = 'OPENIMAGEDENOISE'
c.volume_step_rate = 0.5
c.volume_max_steps = 512
c.max_bounces = 6
c.volume_bounces = 2
c.transparent_max_bounces = 8
scene.render.film_transparent = True
scene.render.image_settings.file_format = 'PNG'
scene.render.image_settings.color_mode = 'RGBA'
scene.render.image_settings.color_depth = '16'
scene.view_settings.view_transform = 'Standard'
scene.view_settings.look = 'None'

for ob in list(bpy.data.objects):
    bpy.data.objects.remove(ob, do_unlink=True)

world = bpy.data.worlds.new('W')
scene.world = world
world.use_nodes = True
world.node_tree.nodes['Background'].inputs['Color'].default_value = (0.55, 0.62, 0.75, 1)
world.node_tree.nodes['Background'].inputs['Strength'].default_value = 0.12

cam_data = bpy.data.cameras.new('Cam')
cam_data.type = 'ORTHO'
cam_data.ortho_scale = 2.0
cam = bpy.data.objects.new('Cam', cam_data)
cam.location = (0, 0, 6)
scene.collection.objects.link(cam)
scene.camera = cam

# key light: raking from upper-left like a tunnel lamp; faint cool rim from the right
sun = bpy.data.objects.new('Key', bpy.data.lights.new('Key', 'SUN'))
sun.data.energy = 5.0
sun.data.angle = math.radians(8)
sun.rotation_euler = (math.radians(20), math.radians(-70), math.radians(25))
scene.collection.objects.link(sun)
rim = bpy.data.objects.new('Rim', bpy.data.lights.new('Rim', 'SUN'))
rim.data.energy = 1.2
rim.data.color = (0.6, 0.75, 1.0)
rim.rotation_euler = (math.radians(-15), math.radians(75), 0)
scene.collection.objects.link(rim)


def render_to(path, res):
    scene.render.resolution_x = scene.render.resolution_y = res
    scene.render.resolution_percentage = 100
    scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def load_rgba(path):
    img = bpy.data.images.load(path)
    w, h = img.size
    px = np.empty(w * h * 4, dtype=np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return px.reshape(h, w, 4)  # bottom-up rows


def save_rgba(arr, path):
    h, w, _ = arr.shape
    img = bpy.data.images.new(os.path.basename(path), w, h, alpha=True, float_buffer=False)
    img.alpha_mode = 'STRAIGHT'
    img.pixels.foreach_set(np.clip(arr, 0, 1).astype(np.float32).ravel())
    img.filepath_raw = path
    try:
        img.file_format = 'WEBP'
        img.save(quality=92)
    except Exception:
        img.file_format = 'PNG'
        img.filepath_raw = path.rsplit('.', 1)[0] + '.png'
        img.save()
    bpy.data.images.remove(img)


# ---------- volume material: distorted ellipsoid x fbm detail ----------
def smoke_material():
    m = bpy.data.materials.new('Smoke')
    m.use_nodes = True
    nt = m.node_tree
    N, L = nt.nodes, nt.links
    N.clear()
    out = N.new('ShaderNodeOutputMaterial')
    vol = N.new('ShaderNodeVolumePrincipled')
    vol.inputs['Color'].default_value = (0.92, 0.95, 1.0, 1)
    vol.inputs['Anisotropy'].default_value = 0.35
    tc = N.new('ShaderNodeTexCoord')
    warp = N.new('ShaderNodeTexNoise'); warp.name = 'warp'; warp.noise_dimensions = '4D'
    warp.inputs['Detail'].default_value = 3.0
    sub = N.new('ShaderNodeVectorMath'); sub.operation = 'SUBTRACT'
    sub.inputs[1].default_value = (0.5, 0.5, 0.5)
    amp = N.new('ShaderNodeVectorMath'); amp.name = 'amp'; amp.operation = 'SCALE'
    add = N.new('ShaderNodeVectorMath'); add.operation = 'ADD'
    shp = N.new('ShaderNodeMapping'); shp.name = 'shape'
    grad = N.new('ShaderNodeTexGradient'); grad.gradient_type = 'SPHERICAL'
    powr = N.new('ShaderNodeMath'); powr.operation = 'POWER'; powr.inputs[1].default_value = 1.6
    det = N.new('ShaderNodeTexNoise'); det.name = 'detail'; det.noise_dimensions = '4D'
    det.inputs['Detail'].default_value = 10.0
    det.inputs['Roughness'].default_value = 0.62
    ramp = N.new('ShaderNodeMapRange'); ramp.name = 'ramp'
    ramp.inputs['From Min'].default_value = 0.42
    ramp.inputs['From Max'].default_value = 0.78
    ramp.interpolation_type = 'SMOOTHSTEP'
    mul = N.new('ShaderNodeMath'); mul.operation = 'MULTIPLY'
    dens = N.new('ShaderNodeMath'); dens.name = 'dens'; dens.operation = 'MULTIPLY'
    L.new(tc.outputs['Object'], warp.inputs['Vector'])
    L.new(warp.outputs['Color'], sub.inputs[0])
    L.new(sub.outputs[0], amp.inputs[0])
    L.new(tc.outputs['Object'], add.inputs[0])
    L.new(amp.outputs[0], add.inputs[1])
    L.new(add.outputs[0], shp.inputs['Vector'])
    L.new(shp.outputs[0], grad.inputs['Vector'])
    L.new(grad.outputs['Fac'], powr.inputs[0])
    L.new(add.outputs[0], det.inputs['Vector'])
    L.new(det.outputs['Fac'], ramp.inputs['Value'])
    L.new(powr.outputs[0], mul.inputs[0])
    L.new(ramp.outputs['Result'], mul.inputs[1])
    L.new(mul.outputs[0], dens.inputs[0])
    L.new(dens.outputs[0], vol.inputs['Density'])
    L.new(vol.outputs[0], out.inputs['Volume'])
    return m


bpy.ops.mesh.primitive_cube_add(size=2.0)
dom = bpy.context.active_object
mat = smoke_material()
dom.data.materials.append(mat)
nodes = mat.node_tree.nodes


def set_frame(kind, i):
    """kind: 'fil' long thin tendrils, 'puff' soft billows, 'curl' strongly warped eddies."""
    w = random.uniform(0, 100)
    nodes['warp'].inputs['W'].default_value = w
    nodes['detail'].inputs['W'].default_value = w + 13.7
    shape = nodes['shape'].inputs
    if kind == 'fil':
        shape['Scale'].default_value = (1.06, random.uniform(3.2, 4.6), random.uniform(3.0, 4.0))
        nodes['warp'].inputs['Scale'].default_value = random.uniform(0.9, 1.6)
        nodes['amp'].inputs['Scale'].default_value = random.uniform(0.35, 0.6)
        nodes['detail'].inputs['Scale'].default_value = random.uniform(3.5, 6.0)
        nodes['dens'].inputs[1].default_value = 45.0
    elif kind == 'puff':
        s = random.uniform(1.15, 1.45)
        shape['Scale'].default_value = (s, s * random.uniform(1.0, 1.3), s)
        nodes['warp'].inputs['Scale'].default_value = random.uniform(1.2, 2.2)
        nodes['amp'].inputs['Scale'].default_value = random.uniform(0.3, 0.5)
        nodes['detail'].inputs['Scale'].default_value = random.uniform(2.5, 4.0)
        nodes['dens'].inputs[1].default_value = 16.0
    else:
        shape['Scale'].default_value = (1.3, random.uniform(2.2, 3.2), 2.5)
        nodes['warp'].inputs['Scale'].default_value = random.uniform(1.8, 2.8)
        nodes['amp'].inputs['Scale'].default_value = random.uniform(0.8, 1.2)
        nodes['detail'].inputs['Scale'].default_value = random.uniform(4.0, 7.0)
        nodes['dens'].inputs[1].default_value = 30.0
    shape['Rotation'].default_value = (0, 0, math.radians(random.uniform(-8, 8)))


# ---------- smoke atlas ----------
def vignette(px):
    n = px.shape[0]
    yy, xx = np.mgrid[0:n, 0:n]
    r = np.hypot(xx - n / 2 + 0.5, yy - n / 2 + 0.5) / (n / 2)
    px[..., 3] *= np.clip((1.0 - r) / 0.12, 0, 1)   # zero alpha at cell borders: no seams when sprites rotate
    return px


def expose(arr, target=0.92):
    """Straight-alpha RGB normalise: brightest lit smoke -> target (keeps the Cycles shading, fixes exposure)."""
    a = arr[..., 3]
    lum = arr[..., :3].max(-1)[a > 0.05]
    if lum.size:
        arr[..., :3] *= target / max(1e-4, np.percentile(lum, 99.5))
    return arr


kinds = [] if ONLY_MOTES else ['fil'] * 12 + ['puff'] * 10 + ['curl'] * 10
atlas = np.zeros((ROWS * CELL, COLS * CELL, 4), np.float32)
for i, k in enumerate(kinds):
    set_frame(k, i)
    p = os.path.join(TMP, f'smoke_{i:02d}.png')
    render_to(p, CELL)
    px = vignette(load_rgba(p))
    col, row = i % COLS, i // COLS
    y0 = (ROWS - 1 - row) * CELL  # numpy rows are bottom-up
    atlas[y0:y0 + CELL, col * CELL:(col + 1) * CELL] = px
if not ONLY_MOTES:
    save_rgba(expose(atlas), os.path.join(OUT, 'smoke-atlas.webp'))

# ---------- wisp detail sheet (tileable) ----------
# Thin slab of fbm smoke stretched along x (flow direction), then a half-offset crossfade so it tiles.
wm = bpy.data.materials.new('Wisp')
wm.use_nodes = True
wt = wm.node_tree
wt.nodes.clear()
wtc = wt.nodes.new('ShaderNodeTexCoord')
wmap = wt.nodes.new('ShaderNodeMapping'); wmap.inputs['Scale'].default_value = (0.9, 3.4, 1.0)
wwarp = wt.nodes.new('ShaderNodeTexNoise'); wwarp.inputs['Scale'].default_value = 2.2; wwarp.inputs['Detail'].default_value = 4.0
wsub = wt.nodes.new('ShaderNodeVectorMath'); wsub.operation = 'SUBTRACT'; wsub.inputs[1].default_value = (0.5, 0.5, 0.5)
wamp = wt.nodes.new('ShaderNodeVectorMath'); wamp.operation = 'SCALE'; wamp.inputs['Scale'].default_value = 0.55
wadd = wt.nodes.new('ShaderNodeVectorMath'); wadd.operation = 'ADD'
wdet = wt.nodes.new('ShaderNodeTexNoise'); wdet.inputs['Scale'].default_value = 3.0
wdet.inputs['Detail'].default_value = 12.0; wdet.inputs['Roughness'].default_value = 0.6
wr = wt.nodes.new('ShaderNodeMapRange'); wr.interpolation_type = 'SMOOTHSTEP'
wr.inputs['From Min'].default_value = 0.40; wr.inputs['From Max'].default_value = 0.80
wmul = wt.nodes.new('ShaderNodeMath'); wmul.operation = 'MULTIPLY'; wmul.inputs[1].default_value = 9.0
wvol = wt.nodes.new('ShaderNodeVolumePrincipled'); wvol.inputs['Color'].default_value = (0.92, 0.95, 1.0, 1)
wvol.inputs['Anisotropy'].default_value = 0.35
wout = wt.nodes.new('ShaderNodeOutputMaterial')
L = wt.links.new
L(wtc.outputs['Object'], wmap.inputs['Vector'])
L(wmap.outputs[0], wwarp.inputs['Vector'])
L(wwarp.outputs['Color'], wsub.inputs[0]); L(wsub.outputs[0], wamp.inputs[0])
L(wmap.outputs[0], wadd.inputs[0]); L(wamp.outputs[0], wadd.inputs[1])
L(wadd.outputs[0], wdet.inputs['Vector'])
L(wdet.outputs['Fac'], wr.inputs['Value']); L(wr.outputs['Result'], wmul.inputs[0])
L(wmul.outputs[0], wvol.inputs['Density']); L(wvol.outputs[0], wout.inputs['Volume'])
dom.data.materials[0] = wm
dom.scale = (1.0, 1.0, 0.25)
p = os.path.join(TMP, 'wisp.png')
render_to(p, 64 if ONLY_MOTES else 512)
wi = load_rgba(p)
h, w = wi.shape[:2]
# tile one axis at a time: the rolled copy's seam lands where the original has full weight
mx = np.sin(np.pi * (np.arange(w) + 0.5) / w)[None, :, None] ** 2
wi = wi * mx + np.roll(wi, w // 2, 1) * (1 - mx)
my = np.sin(np.pi * (np.arange(h) + 0.5) / h)[:, None, None] ** 2
wisp = wi * my + np.roll(wi, h // 2, 0) * (1 - my)
a = wisp[..., 3]
wisp[..., 3] = np.clip(a / max(1e-4, np.percentile(a, 99.5)), 0, 1)
if not ONLY_MOTES:
    save_rgba(expose(wisp), os.path.join(OUT, 'wisp.webp'))
dom.hide_render = True

# ---------- tracer motes ----------
# Emissive spheres against black (no film transparency): the rendered light IS the additive sprite,
# stored as alpha (luminance) with white RGB. Glow halo from an emissive volume, bokeh from real DOF.
MC = 128
scene.render.film_transparent = False
world.node_tree.nodes['Background'].inputs['Strength'].default_value = 0.0
sun.hide_render = rim.hide_render = True
scene.render.image_settings.file_format = 'OPEN_EXR'   # float: no clipping of the emitter core
scene.render.image_settings.color_depth = '32'


def emissive(name, strength):
    mt = bpy.data.materials.new(name)
    mt.use_nodes = True
    n = mt.node_tree.nodes; n.clear()
    em = n.new('ShaderNodeEmission')
    em.inputs['Color'].default_value = (1, 1, 1, 1)
    em.inputs['Strength'].default_value = strength
    mt.node_tree.links.new(em.outputs[0], n.new('ShaderNodeOutputMaterial').inputs['Surface'])
    return mt


bpy.ops.mesh.primitive_uv_sphere_add(radius=0.05, segments=32, ring_count=16)
dot = bpy.context.active_object
dot.data.materials.append(emissive('Glow', 40.0))
bpy.ops.object.shade_smooth()

halo_m = bpy.data.materials.new('Halo')
halo_m.use_nodes = True
hn = halo_m.node_tree.nodes; hn.clear()
hem = hn.new('ShaderNodeEmission')
hg = hn.new('ShaderNodeTexGradient'); hg.gradient_type = 'SPHERICAL'
htc = hn.new('ShaderNodeTexCoord')
hp = hn.new('ShaderNodeMath'); hp.operation = 'POWER'; hp.inputs[1].default_value = 5.0
hm = hn.new('ShaderNodeMath'); hm.operation = 'MULTIPLY'; hm.inputs[1].default_value = 20.0
hl = halo_m.node_tree.links
hl.new(htc.outputs['Object'], hg.inputs['Vector']); hl.new(hg.outputs['Fac'], hp.inputs[0])
hl.new(hp.outputs[0], hm.inputs[0]); hl.new(hm.outputs[0], hem.inputs['Strength'])
hl.new(hem.outputs[0], hn.new('ShaderNodeOutputMaterial').inputs['Volume'])
bpy.ops.mesh.primitive_uv_sphere_add(radius=0.95, segments=48, ring_count=24)
halo = bpy.context.active_object
halo.data.materials.append(halo_m)
halo.scale = (0.3, 0.3, 0.3)

c.samples = 256
motes = np.zeros((MC, MC * 4, 4), np.float32)


def put(slot, path):
    px = load_rgba(path)
    lum = np.maximum(px[..., :3].mean(-1) - np.median(px[:4, :4, :3]), 0)
    cell = np.zeros_like(px)
    cell[..., :3] = 1.0
    cell[..., 3] = 1.0 - np.exp(-3.0 * lum / max(1e-6, lum.max()))   # soft film shoulder keeps the halo
    motes[:, slot * MC:(slot + 1) * MC] = vignette(cell)


cam_data.ortho_scale = 0.6        # core fills ~1/6 of the cell so it survives at particle size
p = os.path.join(TMP, 'm0.exr'); render_to(p, MC); put(0, p)       # sharp mote + glow halo
halo.hide_render = True
# bokeh: object far behind the focal plane, wide aperture -> defocus disc filling ~70% of the cell
cam_data.type = 'PERSP'; cam_data.lens = 50; cam_data.sensor_width = 36
cam.location = (0, 0, 3.0)
cam_data.dof.use_dof = True
cam_data.dof.focus_distance = 0.25
cam_data.dof.aperture_fstop = 0.6
cam_data.dof.aperture_blades = 0
dot.data.materials[0] = emissive('Bokeh', 4000.0)
p = os.path.join(TMP, 'm1.exr'); render_to(p, MC); put(1, p)       # round bokeh
cam_data.dof.aperture_blades = 6
cam_data.dof.aperture_rotation = math.radians(15)
p = os.path.join(TMP, 'm2.exr'); render_to(p, MC); put(2, p)       # hexagonal bokeh
# streak: elongated emitter, slightly defocused so the ends feather
cam_data.dof.aperture_blades = 0
cam_data.dof.focus_distance = 1.6
cam_data.dof.aperture_fstop = 1.6
dot.data.materials[0] = emissive('Streak', 60.0)
dot.scale = (14.0, 0.6, 0.6)
p = os.path.join(TMP, 'm3.exr'); render_to(p, MC); put(3, p)
save_rgba(motes, os.path.join(OUT, 'motes.webp'))

with open(os.path.join(OUT, 'atlas.json'), 'w') as f:
    json.dump({
        'smoke': {'file': 'smoke-atlas.webp', 'cell': CELL, 'cols': COLS, 'rows': ROWS,
                  'frames': {'filament': [0, 11], 'puff': [12, 21], 'curl': [22, 31]}},
        'motes': {'file': 'motes.webp', 'cell': MC, 'cols': 4, 'rows': 1,
                  'frames': ['glow', 'bokeh-round', 'bokeh-hex', 'streak'],
                  'encoding': 'white RGB, alpha = emitted light (additive mask)'},
        'wisp': {'file': 'wisp.webp', 'size': 512, 'tileable': True, 'channel': 'alpha = density'},
        'alpha': 'straight (unassociated). Premultiply on upload: gl.pixelStorei(UNPACK_PREMULTIPLY_ALPHA_WEBGL, true).',
        'renderer': 'Cycles (Metal GPU) + OpenImageDenoise, Standard view transform',
    }, f, indent=2)
shutil.rmtree(TMP, ignore_errors=True)
print('SMOKE BUILD DONE ->', OUT)
