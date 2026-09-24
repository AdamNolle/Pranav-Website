"""
Procedurally builds Pranav's 2022+-regulation F1 car and exports assets/car.glb.

Run from the repo root:
    blender --background --python blender/build_car.py -- [--render 3q side nose top rear front detail]
                                                          [--samples 56] [--no-bake] [--no-export]

Blender is Z-up with the car pointing down +X (metres); the glTF exporter converts to Y-up.
Front axle at x = +1.70, rear axle at x = -1.90 (3.60 m wheelbase), overall ~5.55 m x 2.00 m x 0.95 m.

Livery brief (from Williams FW46/FW47, Alpine A524, RB20/VCARB, McLaren's 2026 matte-black Monaco car):
  - satin black base, gloss colour: the matte/gloss contrast does the work, not extra colours;
  - one hero sweep that follows the car's feature lines: nose -> chassis shoulder -> sidepod top ->
    downwash ramp -> coke-bottle -> fin -> rear-wing endplates, never a flat stripe;
  - colour travels along the sweep: electric blue #2b7bff at the nose, deep #0b3a9e, navy #0b1f4d under
    the cockpit, back to electric blue at the rear (a gradient you read as speed);
  - a fine silver pinstripe underlines the sweep (Williams-style), stopping short of the ends;
  - sponsors sit in clean black space at legible sizes: J.B. Hunt title on the sidepod flank and endplates,
    Georgia Tech on engine cover/endplates, Missouri S&T on nose and front-wing endplates;
  - bare gloss carbon for the wings and floor; blue only on flap edges, never whole panels;
  - panel lines and fasteners are painted into the same texture set so the body reads as real hardware.
  The livery is one side-projected (x, z) texture set (base / ORM+coat / normal) shared by body, sidepods,
  fillets, airbox, fin and endplates, so the sweep flows continuously over the curvature. Logos stay
  conformal decals (a side projection would mirror them on one side).

Pipeline:
  1. textures are generated with numpy (carbon twill, paint flake, rubber grain, livery art, type) and
     written to assets/car/*.webp; logos come from assets/logos/*-white.png;
  2. bodywork is lofted from B-spline cross-sections whose control points are interpolated between
     keyframe stations with a monotone cubic (PCHIP), so surfaces stay taut with crisp shoulder lines;
  3. livery is applied as conformal decal meshes (grid ray-cast onto the body, offset 1.2 mm);
  4. the GLB is exported with Draco geometry + WebP textures; a contact-shadow AO map is baked to
     assets/car/shadow.png for the web floor; optional Cycles previews go to blender/preview_<view>.png.
"""
import bpy, bmesh, math, os, sys
import numpy as np
from mathutils import Vector, Matrix, Euler
from mathutils.bvhtree import BVHTree

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'assets', 'car.glb')
TEX = os.path.join(ROOT, 'assets', 'car')
LOGOS = os.path.join(ROOT, 'assets', 'logos')
FONT = '/System/Library/Fonts/Supplemental/DIN Condensed Bold.ttf'
ARGS = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []


def arg_after(flag, default=None):
    return ARGS[ARGS.index(flag) + 1] if flag in ARGS and ARGS.index(flag) + 1 < len(ARGS) else default


VIEWS = []
if '--render' in ARGS:
    for a in ARGS[ARGS.index('--render') + 1:]:
        if a.startswith('--'):
            break
        VIEWS.append(a)
    VIEWS = VIEWS or ['3q']
SAMPLES = int(arg_after('--samples', 56))
DO_BAKE = '--no-bake' not in ARGS
DO_EXPORT = '--no-export' not in ARGS

NUMBER = '7'
XF, XR, R = 1.70, -1.90, 0.36          # axles, tyre radius (720 mm)
WF, WR = 0.305, 0.405                  # tyre widths
YF, YR = 1.0 - WF / 2, 1.0 - WR / 2    # wheel centre offsets -> 2.00 m overall

bpy.ops.wm.read_factory_settings(use_empty=True)
scene = bpy.context.scene
col = scene.collection
os.makedirs(TEX, exist_ok=True)
rng = np.random.default_rng(7)


# =============================================================== GPU (Metal) Cycles
def use_gpu():
    scene.render.engine = 'CYCLES'
    try:
        prefs = bpy.context.preferences.addons['cycles'].preferences
        prefs.compute_device_type = 'METAL'
        prefs.get_devices()
        for dv in prefs.devices:
            dv.use = True
        scene.cycles.device = 'GPU'
        print('Cycles devices:', [(dv.name, dv.type) for dv in prefs.devices])
    except Exception as e:
        print('GPU unavailable, CPU render', e)
        scene.cycles.device = 'CPU'
    scene.cycles.use_denoising = True
    try:
        scene.cycles.denoiser = 'OPENIMAGEDENOISE'
    except Exception:
        pass




def lin(hexcol):
    h = hexcol.lstrip('#')
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in c)


BLUE, DEEP, NAVY = lin('#2b7bff'), lin('#0b3a9e'), lin('#0b1f4d')
SILVER = (0.62, 0.64, 0.68)


# =============================================================== textures (numpy)
def to_srgb(a):
    a = np.clip(a, 0, 1)
    return np.where(a <= 0.0031308, a * 12.92, 1.055 * np.power(a, 1 / 2.4) - 0.055)


def save_img(name, arr, noncolor=False):
    """arr: (h, w, c) float in 0..1, row 0 = top. Colour arrays must already be sRGB-encoded."""
    arr = np.asarray(arr, np.float32)
    if arr.ndim == 2:
        arr = arr[..., None]
    h, w, c = arr.shape
    if c == 1:
        arr = np.repeat(arr, 3, axis=2)
    if arr.shape[2] == 3:
        arr = np.concatenate([arr, np.ones((h, w, 1), np.float32)], 2)
    img = bpy.data.images.new(name, w, h, alpha=True)
    img.pixels.foreach_set(np.ascontiguousarray(arr[::-1]).ravel())
    img.filepath_raw = os.path.join(TEX, name + '.webp')
    img.file_format = 'WEBP'
    img.save()
    if noncolor:
        img.colorspace_settings.name = 'Non-Color'
    return img


def blur(a, r):
    """Separable box-blur x3 (~gaussian), wraps (tileable)."""
    for _ in range(3):
        for ax in (0, 1):
            acc = np.zeros_like(a)
            for k in range(-r, r + 1):
                acc += np.roll(a, k, axis=ax)
            a = acc / (2 * r + 1)
    return a


def height_to_normal(hgt, strength):
    gx = (np.roll(hgt, -1, 1) - np.roll(hgt, 1, 1)) * strength
    gy = (np.roll(hgt, -1, 0) - np.roll(hgt, 1, 0)) * strength
    n = np.stack([-gx, gy, np.ones_like(hgt)], -1)       # rows go down the image -> +v is up
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return n * 0.5 + 0.5


def carbon_textures():
    """2x2 twill, 16 tows per tile. Tile = 96 mm -> 6 mm tows."""
    N, T = 1024, 16
    P = N // T
    yy, xx = np.mgrid[0:N, 0:N].astype(np.float32) + 0.5
    ci, cj = (xx // P).astype(int), (yy // P).astype(int)
    fu, fv = (xx % P) / P, (yy % P) / P
    warp = ((ci + cj) % 4) < 2                            # warp (vertical tow) on top
    across = np.where(warp, fu, fv)                      # position across the visible tow
    along = np.where(warp, fv, fu)
    phase = np.where(warp, (cj + ci) % 4, (cj + ci) % 4 - 2)   # 0/1: first or second cell of the float
    t = (phase + along) / 2.0                            # 0..1 along the two-cell float
    bulge = np.sqrt(np.clip(1 - (2 * across - 1) ** 2, 0, 1))
    dip = np.sin(np.clip(t, 0, 1) * math.pi) ** 0.5
    fib_noise = rng.standard_normal((N, N)).astype(np.float32)
    # fibre streaks run along the tow: average noise along the tow direction
    sv = sum(np.roll(fib_noise, k, 0) for k in range(-6, 7)) / 13
    sh = sum(np.roll(fib_noise, k, 1) for k in range(-6, 7)) / 13
    streaks = np.where(warp, sv, sh)
    hgt = bulge * (0.55 + 0.45 * dip) + streaks * 0.04
    nrm = height_to_normal(blur(hgt, 1), 2.2)
    # colour: tows facing the light differently read lighter/darker, plus gaps
    base = np.where(warp, 0.030, 0.017) * (0.75 + 0.35 * bulge) + streaks * 0.004
    gap = (1 - bulge) ** 6
    base = base * (1 - 0.6 * gap)
    col_ = to_srgb(np.repeat(base[..., None], 3, 2) * np.array([0.95, 0.98, 1.05]))
    rough = 0.34 + 0.10 * gap + 0.03 * np.abs(streaks)
    orm = np.stack([np.ones_like(rough), rough, np.zeros_like(rough)], -1)
    return (save_img('carbon_basecolor', col_), save_img('carbon_normal', nrm, True),
            save_img('carbon_orm', orm, True))


def flake_normal():
    """Metallic flake: per-flake random micro-tilt. Tile = 40 mm, flakes ~0.3 mm."""
    N, cell = 256, 4
    g = N // cell
    tilt = rng.standard_normal((g, g, 2)).astype(np.float32) * 0.35
    tilt = np.repeat(np.repeat(tilt, cell, 0), cell, 1)
    n = np.concatenate([tilt, np.ones((N, N, 1), np.float32)], -1)
    n /= np.linalg.norm(n, axis=-1, keepdims=True)
    return save_img('paint_flake_normal', n * 0.5 + 0.5, True)


def rubber_normal():
    N = 256
    h = blur(rng.standard_normal((N, N)).astype(np.float32), 1) + 0.5 * blur(rng.standard_normal((N, N)).astype(np.float32), 4)
    return save_img('rubber_normal', height_to_normal(h, 1.2), True)


def brushed_textures():
    """Brushed titanium: streaks along U."""
    W, H = 512, 128
    n = rng.standard_normal((H, W)).astype(np.float32)
    s = sum(np.roll(n, k, 1) for k in range(-20, 21)) / 41
    s = (s - s.mean()) / (s.std() + 1e-6)
    rough = np.clip(0.26 + 0.05 * s, 0, 1)
    orm = np.stack([np.ones_like(rough), rough, np.ones_like(rough)], -1)
    nrm = height_to_normal(s * 0.2, 1.0)
    return save_img('titanium_orm', orm, True), save_img('titanium_normal', nrm, True)


# ---------------------------------------------------------------- type rasteriser
_fonts = {}


def text_tris(text, spacing=1.0, font=FONT):
    if font not in _fonts:
        _fonts[font] = bpy.data.fonts.load(font, check_existing=True)
    cu = bpy.data.curves.new('tmp', 'FONT')
    cu.body = text
    cu.font = _fonts[font]
    cu.size = 1.0
    cu.space_character = spacing
    cu.resolution_u = 12
    ob = bpy.data.objects.new('tmp', cu)
    col.objects.link(ob)
    bpy.context.view_layer.update()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(bpy.context.evaluated_depsgraph_get()))
    bpy.data.objects.remove(ob)
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.triangulate(bm, faces=bm.faces)
    tris = np.array([[(v.co.x, v.co.y) for v in f.verts] for f in bm.faces], np.float32)
    bm.free()
    bpy.data.meshes.remove(me)
    return tris


def raster(tris, W, H, ss=4):
    """tris in pixel coords (x right, y down). Returns coverage (H, W)."""
    acc = np.zeros((H * ss, W * ss), bool)
    for tri in tris * ss:
        x0, y0 = np.maximum(np.floor(tri.min(0)).astype(int), 0)
        x1, y1 = np.ceil(tri.max(0)).astype(int)
        x1, y1 = min(x1, W * ss), min(y1, H * ss)
        if x1 <= x0 or y1 <= y0:
            continue
        X, Y = np.meshgrid(np.arange(x0, x1) + 0.5, np.arange(y0, y1) + 0.5)
        a, b, c = tri

        def e(p, q):
            return (q[0] - p[0]) * (Y - p[1]) - (q[1] - p[1]) * (X - p[0])
        e0, e1, e2 = e(a, b), e(b, c), e(c, a)
        acc[y0:y1, x0:x1] |= ((e0 >= 0) & (e1 >= 0) & (e2 >= 0)) | ((e0 <= 0) & (e1 <= 0) & (e2 <= 0))
    return acc.reshape(H, ss, W, ss).mean((1, 3))


def text_mask(text, H, W=None, spacing=1.0, shear=0.0, pad=0.12, align='center'):
    """Rasterise text to a coverage mask of height H (cap height fills H*(1-2*pad))."""
    tris = text_tris(text, spacing)
    tris[..., 0] += tris[..., 1] * shear
    mn, mx = tris.reshape(-1, 2).min(0), tris.reshape(-1, 2).max(0)
    s = H * (1 - 2 * pad) / (mx[1] - mn[1])
    tw = (mx[0] - mn[0]) * s
    if W is None:
        W = int(math.ceil(tw + 2 * pad * H))
    ox = {'center': (W - tw) / 2, 'left': pad * H, 'right': W - tw - pad * H}[align]
    px = np.empty_like(tris)
    px[..., 0] = (tris[..., 0] - mn[0]) * s + ox
    px[..., 1] = H - ((tris[..., 1] - mn[1]) * s + pad * H)
    return raster(px, W, H)


def rgba(mask, color_lin, alpha=1.0):
    c = to_srgb(np.array(color_lin, np.float32))
    out = np.zeros(mask.shape + (4,), np.float32)
    out[..., :3] = c
    out[..., 3] = mask * alpha
    return out


def over(dst, src):
    a = src[..., 3:4]
    dst[..., :3] = src[..., :3] * a + dst[..., :3] * (1 - a)
    dst[..., 3:4] = a + dst[..., 3:4] * (1 - a)
    return dst


def pad_pow2(arr):
    h, w = arr.shape[:2]
    H, W = 1 << (h - 1).bit_length(), 1 << (w - 1).bit_length()
    out = np.zeros((H, W, arr.shape[2]), np.float32)
    out[(H - h) // 2:(H - h) // 2 + h, (W - w) // 2:(W - w) // 2 + w] = arr
    return out


# ---------------------------------------------------------------- logos
def _read_png(path):
    src = bpy.data.images.load(path)
    w, h = src.size
    a = np.empty(w * h * 4, np.float32)
    src.pixels.foreach_get(a)
    bpy.data.images.remove(src)
    return a.reshape(h, w, 4)[::-1].copy()


def jbhunt_knockout():
    """Reversed J.B. Hunt treatment for dark paint: the official roll + wordmark as white marks straight on
    the paint. Taken from the official colour artwork: black ink inside the yellow field only, so the outer
    badge frame (the 'white box') and the (R) outside it are dropped."""
    a = _read_png(os.path.join(LOGOS, 'jbhunt-color.png'))
    r, g, b, al = a[..., 0], a[..., 1], a[..., 2], a[..., 3]
    yellow = (r > 0.6) & (g > 0.55) & (b < 0.35) & (al > 0.5)
    ys, xs = np.nonzero(yellow)
    y0, y1, x0, x1 = ys.min(), ys.max(), xs.min(), xs.max()
    ink = ((r + g + b) / 3 < 0.35) & (al > 0.5)
    m = np.zeros(ink.shape, np.float32)
    m[y0 + 2:y1 - 1, x0 + 2:x1 - 1] = ink[y0 + 2:y1 - 1, x0 + 2:x1 - 1]
    m = blur(m, 1)                                         # soften the 1-bit edge a touch
    out = np.ones(a.shape, np.float32)
    out[..., 3] = np.clip(m * 1.15, 0, 1)
    return out


def _minmax_filter(x, r, f):
    """Separable square min/max filter (f = np.minimum / np.maximum) of radius r."""
    for axis in (0, 1):
        p = np.pad(x, [(r, r) if i == axis else (0, 0) for i in range(2)], mode='edge')
        n, out = x.shape[axis], None
        for k in range(2 * r + 1):
            s = np.take(p, range(k, k + n), axis=axis)
            out = s if out is None else f(out, s)
        x = out
    return x


def jbhunt_wordmark(img):
    """Phone variant of the J.B. Hunt mark: the wordmark alone, enlarged to fill the same decal.
    On phones the sidepod mark is ~57 device px across, so the roll's hatch lines are sub-pixel and
    average into a grey smear over the letters. A morphological opening (radius 6 px at 1024 wide)
    removes the thin hatch and roll strokes (3-5 px) and keeps the letters and dots (~30 px strokes)."""
    w, h = img.size
    a = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(a)
    a = a.reshape(h, w, 4)
    solid = (a[..., 3] > 0.5).astype(np.float32)
    opened = _minmax_filter(_minmax_filter(solid, 6, np.minimum), 6, np.maximum)
    keep = _minmax_filter(opened, 2, np.maximum)            # keep the letters' anti-aliased edges
    letters = a[..., 3] * keep
    ys, xs = np.nonzero(letters > 0.02)
    crop = letters[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    ch, cw = crop.shape
    k = min(0.92 * w / cw, 0.86 * h / ch)
    sw, sh = max(1, int(cw * k)), max(1, int(ch * k))
    tmp = bpy.data.images.new('tmp_jbhunt_wm', cw, ch, alpha=True)
    px = np.ones((ch, cw, 4), np.float32)
    px[..., 3] = crop
    tmp.pixels.foreach_set(px.ravel())
    tmp.scale(sw, sh)
    s = np.empty(sw * sh * 4, np.float32)
    tmp.pixels.foreach_get(s)
    bpy.data.images.remove(tmp)
    out = np.zeros((h, w, 4), np.float32)
    out[..., :3] = 1.0
    y0, x0 = (h - sh) // 2, (w - sw) // 2
    out[y0:y0 + sh, x0:x0 + sw, 3] = s.reshape(sh, sw, 4)[..., 3]
    wm = bpy.data.images.new('logo_jbhunt_wordmark', w, h, alpha=True)
    wm.pixels.foreach_set(out.ravel())
    wm.filepath_raw = os.path.join(TEX, 'logo-jbhunt-wordmark.webp')
    wm.file_format = 'WEBP'
    wm.save()
    return wm


def load_logo(key, max_edge=1024):
    """Crop a transparent white logo to its alpha bounds, downscale, save as WebP. Returns (image, aspect)."""
    path = os.path.join(LOGOS, key + '-white.png')
    if key == 'jbhunt' and os.path.exists(os.path.join(LOGOS, 'jbhunt-color.png')):
        a = jbhunt_knockout()
    elif not os.path.exists(path):
        print('!! logo missing:', path)
        return None, 1.0
    else:
        a = _read_png(path)
    src = None
    ys, xs = np.nonzero(a[..., 3] > 0.02)
    a = a[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    h, w = a.shape[:2]
    padp = int(0.04 * max(h, w))
    a = np.pad(a, ((padp, padp), (padp, padp), (0, 0)))
    a[..., :3] = 1.0                                    # pure white mark, alpha carries the shape
    h, w = a.shape[:2]
    img = bpy.data.images.new('logo_' + key, w, h, alpha=True)
    img.pixels.foreach_set(np.ascontiguousarray(a[::-1]).ravel())
    k = max_edge / max(w, h)
    if k < 1:
        img.scale(max(1, int(w * k)), max(1, int(h * k)))
    img.filepath_raw = os.path.join(TEX, 'logo-' + key + '.webp')
    img.file_format = 'WEBP'
    img.save()
    return img, w / h


# =============================================================== materials
def principled(name, color=(0.5, 0.5, 0.5), metallic=0.0, rough=0.5, coat=0.0, coat_rough=0.03,
               emit=None, aniso=0.0):
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:
        pass
    b = m.node_tree.nodes['Principled BSDF']
    b.inputs['Base Color'].default_value = (*color, 1)
    b.inputs['Metallic'].default_value = metallic
    b.inputs['Roughness'].default_value = rough
    b.inputs['Coat Weight'].default_value = coat
    b.inputs['Coat Roughness'].default_value = coat_rough
    if aniso:
        b.inputs['Anisotropic'].default_value = aniso
    if emit:
        b.inputs['Emission Color'].default_value = (*emit[0], 1)
        b.inputs['Emission Strength'].default_value = emit[1]
    m.diffuse_color = (*color, 1)
    return m


def tex_node(m, img, scale=None, loc=(-700, 0), uv='UVMap'):
    nt = m.node_tree
    t = nt.nodes.new('ShaderNodeTexImage')
    t.image = img
    t.location = loc
    if not scale and uv != 'UVMap':
        uvn = nt.nodes.new('ShaderNodeUVMap')
        uvn.uv_map = uv
        uvn.location = (loc[0] - 300, loc[1])
        nt.links.new(uvn.outputs['UV'], t.inputs['Vector'])
    if scale:
        uvn = nt.nodes.new('ShaderNodeUVMap')
        uvn.uv_map = uv
        uvn.location = (loc[0] - 400, loc[1])
        mp = nt.nodes.new('ShaderNodeMapping')
        mp.location = (loc[0] - 200, loc[1])
        mp.inputs['Scale'].default_value = (scale, scale, 1)
        nt.links.new(uvn.outputs['UV'], mp.inputs['Vector'])
        nt.links.new(mp.outputs['Vector'], t.inputs['Vector'])
    return t


def add_normal(m, img, strength, scale=None, uv='UVMap'):
    nt = m.node_tree
    b = nt.nodes['Principled BSDF']
    t = tex_node(m, img, scale, (-700, -400), uv)
    nm = nt.nodes.new('ShaderNodeNormalMap')
    nm.inputs['Strength'].default_value = strength
    nt.links.new(t.outputs['Color'], nm.inputs['Color'])
    nt.links.new(nm.outputs['Normal'], b.inputs['Normal'])


def add_orm(m, img, scale=None, uv='UVMap'):
    nt = m.node_tree
    b = nt.nodes['Principled BSDF']
    t = tex_node(m, img, scale, (-700, -150), uv)
    sep = nt.nodes.new('ShaderNodeSeparateColor')
    nt.links.new(t.outputs['Color'], sep.inputs['Color'])
    nt.links.new(sep.outputs['Green'], b.inputs['Roughness'])
    nt.links.new(sep.outputs['Blue'], b.inputs['Metallic'])
    return sep


def add_base(m, img, scale=None, alpha=False, uv='UVMap'):
    nt = m.node_tree
    b = nt.nodes['Principled BSDF']
    t = tex_node(m, img, scale, (-700, 250), uv)
    nt.links.new(t.outputs['Color'], b.inputs['Base Color'])
    if alpha:
        nt.links.new(t.outputs['Alpha'], b.inputs['Alpha'])
        t.extension = 'CLIP'
        try:
            m.surface_render_method = 'BLENDED'
        except Exception:
            pass
        try:
            m.blend_method = 'BLEND'
        except Exception:
            pass
    return t


print('generating textures ...')
CARBON = carbon_textures()
FLAKE = flake_normal()
RUBBER_N = rubber_normal()
TI_ORM, TI_N = brushed_textures()
CARBON_SCALE = 1 / 0.096      # UVs are in metres; 96 mm tile of 16 tows
FLAKE_SCALE = 1 / 0.02


def carbon_mat(name='Carbon', coat=0.55, coat_rough=0.16):
    m = principled(name, (0.02, 0.02, 0.022), 0.0, 0.4, coat, coat_rough)
    add_base(m, CARBON[0], CARBON_SCALE)
    add_orm(m, CARBON[2], CARBON_SCALE)
    add_normal(m, CARBON[1], 0.6, CARBON_SCALE)
    return m


def paint(name, color, metallic=0.55, rough=0.36, flake=0.12):
    m = principled(name, color, metallic, rough, 1.0, 0.025)
    add_normal(m, FLAKE, flake, FLAKE_SCALE)
    return m


M = {
    'black': paint('Paint_Black', (0.0035, 0.004, 0.005), 0.3, 0.4),
    'livery': principled('Paint_Livery', (0.004, 0.0045, 0.006), 0.3, 0.42, 0.3, 0.03),
    'blue': paint('Paint_Blue', BLUE, 0.35, 0.3, 0.08),
    'deep': paint('Paint_DeepBlue', DEEP, 0.45, 0.32, 0.08),
    'carbon': carbon_mat(),
    'inlet': principled('Inlet_Dark', (0.004, 0.004, 0.005), 0.0, 0.62),
    'interior': principled('Cockpit_Interior', (0.008, 0.008, 0.009), 0.0, 0.7),
    'foam': principled('Headrest_Foam', (0.012, 0.012, 0.013), 0.0, 0.92),
    'ti': principled('Titanium_Brushed', (0.50, 0.48, 0.46), 1.0, 0.28, aniso=0.6),
    'polished': principled('Metal_Polished', (0.92, 0.92, 0.93), 1.0, 0.07),
    'exhaust': principled('Exhaust_Titanium', (0.42, 0.36, 0.33), 1.0, 0.3),
    'rim': principled('Rim_Magnesium', (0.05, 0.05, 0.055), 1.0, 0.34),
    'rubber': principled('Tyre_Rubber', (0.016, 0.016, 0.017), 0.0, 0.86),
    'plank': principled('Plank', (0.075, 0.05, 0.03), 0.0, 0.55),
    'visor': principled('Visor', (0.03, 0.04, 0.09), 1.0, 0.04),
    'glass': principled('Mirror_Glass', (0.9, 0.9, 0.92), 1.0, 0.02),
    'lens': principled('Camera_Lens', (0.01, 0.01, 0.012), 0.0, 0.05, 1.0, 0.01),
    'rain': principled('RainLight', (0.25, 0.0, 0.0), 0.0, 0.35, emit=((1.0, 0.02, 0.01), 18.0)),
    'screen': principled('Wheel_Display', (0.01, 0.02, 0.05), 0.0, 0.2, emit=(BLUE, 1.5)),
}
add_orm(M['ti'], TI_ORM)
add_normal(M['ti'], TI_N, 0.3)
add_normal(M['rubber'], RUBBER_N, 0.35, 1 / 0.05)


# =============================================================== geometry helpers
def link(obj, *mats):
    col.objects.link(obj)
    for m in mats:
        obj.data.materials.append(m)
    return obj


def finish(obj, sharp=35, smooth=True):
    me = obj.data
    if smooth:
        me.shade_smooth()
        if sharp:
            me.set_sharp_from_angle(angle=math.radians(sharp))
    return obj


def box_uv(obj):
    """World-scale (metres) box projection for textured parts that aren't lofted."""
    me = obj.data
    if not me.uv_layers:
        me.uv_layers.new(name='UVMap')
    uv = me.uv_layers.active.data
    mw = obj.matrix_world
    for p in me.polygons:
        n = p.normal
        ax = max(range(3), key=lambda i: abs(n[i]))
        for li in p.loop_indices:
            co = mw @ me.vertices[me.loops[li].vertex_index].co
            if ax == 0:
                uv[li].uv = (co.y, co.z)
            elif ax == 1:
                uv[li].uv = (co.x, co.z)
            else:
                uv[li].uv = (co.x, co.y)


def pchip(xs, ys, xq):
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    h = np.diff(xs)
    d = np.diff(ys) / h
    n = len(xs)
    m = np.zeros(n)
    for k in range(1, n - 1):
        if d[k - 1] * d[k] > 0:
            w1, w2 = 2 * h[k] + h[k - 1], h[k] + 2 * h[k - 1]
            m[k] = (w1 + w2) / (w1 / d[k - 1] + w2 / d[k])
    m[0], m[-1] = d[0], d[-1]
    xq = np.atleast_1d(np.asarray(xq, float))
    i = np.clip(np.searchsorted(xs, xq) - 1, 0, n - 2)
    t = (xq - xs[i]) / h[i]
    t2, t3 = t * t, t * t * t
    return ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h[i] * m[i]
            + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h[i] * m[i + 1])


def aa(x, px):
    return np.clip(x / px + 0.5, 0, 1)


class Track:
    """Keyframed parameter table along x. keys: list of (x, {param: value})."""

    def __init__(self, names, rows):
        rows = sorted(rows, key=lambda r: r[0])
        self.x = [r[0] for r in rows]
        self.p = {n: [r[1 + i] for r in rows] for i, n in enumerate(names)}

    def __call__(self, x):
        return {n: float(pchip(self.x, v, x)[0]) for n, v in self.p.items()}


def bspline_closed(ctrl, S):
    C = np.asarray(ctrl, float)
    n = len(C)
    t = np.arange(S) / S
    b = np.stack([(1 - t) ** 3, 3 * t ** 3 - 6 * t ** 2 + 4, -3 * t ** 3 + 3 * t ** 2 + 3 * t + 1, t ** 3], 1) / 6
    out = [b @ np.stack([C[i - 1], C[i], C[(i + 1) % n], C[(i + 2) % n]]) for i in range(n)]
    return np.vstack(out)


def bspline_open(ctrl, S):
    C = np.asarray(ctrl, float)
    C = np.vstack([C[0], C[0], C, C[-1], C[-1]])
    t = np.arange(S) / S
    b = np.stack([(1 - t) ** 3, 3 * t ** 3 - 6 * t ** 2 + 4, -3 * t ** 3 + 3 * t ** 2 + 3 * t + 1, t ** 3], 1) / 6
    out = [b @ C[i:i + 4] for i in range(len(C) - 3)]
    out.append(C[-1:])
    return np.vstack(out)


def corners(poly):
    """poly: [(a, b, tightness)] closed polygon. Each corner -> 3 control points; small t = crisp corner."""
    pts, n = [], len(poly)
    for k, (a, b, t) in enumerate(poly):
        P = np.array([a, b])
        A, B = np.array(poly[k - 1][:2]), np.array(poly[(k + 1) % n][:2])
        pts += [P + t * (A - P), P, P + t * (B - P)]
    return pts


def mirror_half(half):
    """half: from top centre (y=0) down the +y side to bottom centre (y=0)."""
    return half + [(-y, z, t) for (y, z, t) in reversed(half[1:-1])]


def mesh_from_bm(name, bm, mats, sharp=35):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    return finish(link(bpy.data.objects.new(name, me), *mats), sharp)


def loft(name, rings, mats, matfn=None, cap0=True, cap1=True, sharp=35, closed=True, recalc=True):
    """rings: list of (n, 3) arrays with consistent vertex correspondence. UVs in metres."""
    rings = [np.asarray(r, float) for r in rings]
    Rn, n = len(rings), len(rings[0])
    A = np.stack(rings)
    seg = np.linalg.norm(np.roll(A, -1, 1) - A, axis=2)
    around = np.concatenate([np.zeros((Rn, 1)), np.cumsum(seg, 1)], 1)
    along = np.zeros((Rn, n))
    along[1:] = np.cumsum(np.linalg.norm(A[1:] - A[:-1], axis=2), 0)
    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new('UVMap')
    V = [[bm.verts.new(p) for p in ring] for ring in rings]
    m = n if closed else n - 1
    for r in range(Rn - 1):
        for i in range(m):
            j = (i + 1) % n
            try:
                f = bm.faces.new((V[r][i], V[r][j], V[r + 1][j], V[r + 1][i]))
            except ValueError:
                continue
            uvs = ((along[r][i], around[r][i]), (along[r][j], around[r][i + 1]),
                   (along[r + 1][j], around[r + 1][i + 1]), (along[r + 1][i], around[r + 1][i]))
            for lp, uv in zip(f.loops, uvs):
                lp[uvl].uv = uv
            if matfn:
                c = f.calc_center_median()
                f.material_index = matfn(c, r, i)
    for flag, r in ((cap0, 0), (cap1, Rn - 1)):
        if not flag:
            continue
        c = bm.verts.new(A[r].mean(0))
        for i in range(n):
            j = (i + 1) % n
            f = bm.faces.new((c, V[r][j], V[r][i]) if r == 0 else (c, V[r][i], V[r][j]))
            for lp in f.loops:
                lp[uvl].uv = (lp.vert.co.y, lp.vert.co.z)
            if matfn:
                f.material_index = matfn(f.calc_center_median(), -1 if r == 0 else Rn, i)
    if recalc:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return mesh_from_bm(name, bm, mats, sharp)


def frames(path, up=(0, 0, 1)):
    """Parallel-transport frames along a polyline."""
    P = [Vector(p) for p in path]
    T = []
    for i in range(len(P)):
        a, b = P[max(i - 1, 0)], P[min(i + 1, len(P) - 1)]
        T.append((b - a).normalized())
    N = [(Vector(up) - T[0] * T[0].dot(Vector(up))).normalized()]
    for i in range(1, len(P)):
        n = N[-1] - T[i] * T[i].dot(N[-1])
        N.append(n.normalized())
    B = [t.cross(n) for t, n in zip(T, N)]
    return P, T, N, B


def smooth_path(pts, S=8):
    return bspline_open(pts, S)


def sweep(name, path, profile, mats, matfn=None, scale=None, up=(0, 0, 1), cap=True, sharp=40):
    """Sweep a 2D profile [(n, b)] along a 3D path. scale(t)-> (sn, sb) optional taper."""
    P, T, N, B = frames(path, up)
    rings = []
    for k, (p, n, b) in enumerate(zip(P, N, B)):
        sn, sb = scale(k / (len(P) - 1)) if scale else (1, 1)
        rings.append([p + n * (u * sn) + b * (v * sb) for u, v in profile])
    return loft(name, rings, mats, matfn, cap, cap, sharp)


def ellipse(a, b, n=16, rot=0.0):
    return [(a * math.cos(2 * math.pi * i / n + rot), b * math.sin(2 * math.pi * i / n + rot)) for i in range(n)]


def rrect(w, h, r, n=6):
    """Rounded rectangle profile (w, h full sizes)."""
    pts = []
    cx, cy = w / 2 - r, h / 2 - r
    for sx, sy, a0 in ((1, 1, 0), (-1, 1, 90), (-1, -1, 180), (1, -1, 270)):
        for i in range(n + 1):
            a = math.radians(a0 + 90 * i / n)
            pts.append((sx * cx + r * math.cos(a), sy * cy + r * math.sin(a)))
    return pts


def strut(name, p1, p2, chord, thick, m, flow=(1, 0, 0), taper=1.0):
    """Aerofoil-section suspension member: flattened ellipse with its chord aligned to the airflow."""
    p1, p2 = Vector(p1), Vector(p2)
    ax = (p2 - p1).normalized()
    f = Vector(flow)
    c = (f - ax * ax.dot(f))
    if c.length < 1e-4:
        c = Vector((0, 0, 1)) - ax * ax.z
    c.normalize()
    t = ax.cross(c)
    prof = []
    for i in range(28):
        a = 2 * math.pi * i / 28
        x = math.cos(a)
        # slightly drooped aerofoil: blunt leading edge, sharper trailing edge
        y = math.sin(a) * (0.5 + 0.5 * (1 + x) / 2) ** 0.5 if x < 0 else math.sin(a)
        prof.append((x * chord / 2, y * thick / 2))
    rings = []
    for k in range(4):
        s = k / 3
        p = p1.lerp(p2, s)
        sc = 1 + (taper - 1) * s
        rings.append([p + c * u * sc + t * v * sc for u, v in prof])
    return loft(name, rings, [m], sharp=50)


def rbox(name, size, loc, m, rot=(0, 0, 0), bevel=0.004, segs=3):
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=size, verts=bm.verts)
    bmesh.ops.rotate(bm, verts=bm.verts, cent=(0, 0, 0), matrix=Euler(rot).to_matrix())
    bmesh.ops.translate(bm, vec=loc, verts=bm.verts)
    o = mesh_from_bm(name, bm, [m], sharp=0)
    if bevel:
        bv = o.modifiers.new('Bevel', 'BEVEL')
        bv.width = bevel
        bv.segments = segs
        bv.limit_method = 'ANGLE'
        bv.harden_normals = True
    return o


def plate(name, outline, thick, y, m, bevel=0.002, bend=None, sharp=30):
    """Extruded plate from an (x, z) outline, centred at y. bend(x, z) -> dy for curved plates."""
    bm = bmesh.new()
    lo = [bm.verts.new((x, y - thick / 2, z)) for x, z in outline]
    hi = [bm.verts.new((x, y + thick / 2, z)) for x, z in outline]
    bm.faces.new(lo)
    bm.faces.new(list(reversed(hi)))
    n = len(outline)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((lo[i], lo[j], hi[j], hi[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    # triangulate caps nicely, then densify so bending stays smooth
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 4], quad_method='BEAUTY', ngon_method='BEAUTY')
    if bend:
        bmesh.ops.subdivide_edges(bm, edges=[e for e in bm.edges if e.calc_length() > 0.03], cuts=3, use_grid_fill=True)
        for v in bm.verts:
            v.co.y += bend(v.co.x, v.co.z)
    o = mesh_from_bm(name, bm, [m], sharp=sharp)
    if bevel:
        bv = o.modifiers.new('Bevel', 'BEVEL')
        bv.width = bevel
        bv.segments = 2
        bv.limit_method = 'ANGLE'
        bv.harden_normals = True
    box_uv(o)
    return o


def naca(tc, camber=0.04, n=22):
    """Inverted cambered aerofoil (downforce): returns closed [(x/c, z/c)] from TE over the top to TE."""
    xs = (1 - np.cos(np.linspace(0, math.pi, n))) / 2
    yt = 5 * tc * (0.2969 * np.sqrt(xs) - 0.126 * xs - 0.3516 * xs ** 2 + 0.2843 * xs ** 3 - 0.1036 * xs ** 4)
    yc = -camber * 4 * xs * (1 - xs)                       # camber downwards: suction side underneath
    upper = list(zip(xs[::-1], (yc + yt)[::-1]))
    lower = list(zip(xs[1:-1], (yc - yt)[1:-1]))
    return upper + lower


def wing(name, ys, fn, mats, tc=0.09, camber=0.05, matfn=None):
    """Wing element spanning y. fn(y) -> (x_le, z_le, chord, pitch_deg). Positive pitch raises the TE."""
    prof = naca(tc, camber)
    rings = []
    for y in ys:
        xle, zle, ch, pitch = fn(y)
        a = math.radians(pitch)
        ca, sa = math.cos(a), math.sin(a)
        ring = []
        for u, v in prof:
            px, pz = u * ch, v * ch
            ring.append((xle - (px * ca - pz * sa), y, zle + px * sa + pz * ca))
        rings.append(ring)
    o = loft(name, rings, mats, matfn, sharp=30)
    return o


def revolve(name, prof, mats, center, side=1, segs=96, matfn=None, sharp=40):
    """Revolve [(r, y)] about the wheel axle (Y). side=-1 mirrors in y. UV u=angle*r, v=profile length."""
    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new('UVMap')
    cx, cy, cz = center
    prof = np.asarray(prof, float)
    L = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(prof, axis=0), axis=1))])
    V = []
    for k in range(segs):
        a = 2 * math.pi * k / segs
        V.append([bm.verts.new((cx + r * math.sin(a), cy + side * y, cz + r * math.cos(a))) for r, y in prof])
    for k in range(segs):
        k2 = (k + 1) % segs
        for i in range(len(prof) - 1):
            vs = (V[k][i], V[k2][i], V[k2][i + 1], V[k][i + 1])
            if side < 0:
                vs = vs[::-1]
            try:
                f = bm.faces.new(vs)
            except ValueError:
                continue
            # UVs: angle fraction (u) and profile fraction (v)
            for lp in f.loops:
                idx_k = k if lp.vert in (V[k][i], V[k][i + 1]) else k + 1
                idx_i = i if lp.vert in (V[k][i], V[k2][i]) else i + 1
                uu = idx_k / segs
                lp[uvl].uv = (1 - uu if side > 0 else uu, L[idx_i] / L[-1])   # read correctly from outside on both sides
            if matfn:
                f.material_index = matfn(i)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    return mesh_from_bm(name, bm, mats, sharp)


# =============================================================== bodywork
# Main body: nose -> monocoque -> engine cover -> gearbox/crash structure.
# Half-section: top centre (0, zr) -> top shoulder (wt, zt) -> side max (ws, zs) -> bottom corner (wb, zb) -> (0, zb)
BODY = Track(['zb', 'wb', 'ws', 'zs', 'wt', 'zt', 'zr', 'tt', 'ts'], [
    # x      zb     wb     ws     zs     wt     zt     zr     tt    ts
    (2.975, 0.150, 0.058, 0.084, 0.188, 0.060, 0.228, 0.236, 0.30, 0.45),
    (2.85, 0.140, 0.072, 0.096, 0.198, 0.068, 0.258, 0.268, 0.26, 0.45),
    (2.55, 0.146, 0.078, 0.102, 0.236, 0.074, 0.330, 0.342, 0.22, 0.45),
    (2.20, 0.172, 0.086, 0.115, 0.305, 0.082, 0.430, 0.446, 0.18, 0.45),
    (1.95, 0.192, 0.095, 0.126, 0.342, 0.090, 0.490, 0.510, 0.16, 0.45),
    (1.70, 0.186, 0.112, 0.146, 0.378, 0.100, 0.548, 0.572, 0.14, 0.45),
    (1.40, 0.140, 0.140, 0.190, 0.418, 0.120, 0.604, 0.628, 0.13, 0.45),
    (1.10, 0.092, 0.180, 0.244, 0.448, 0.150, 0.642, 0.662, 0.12, 0.45),
    (0.80, 0.080, 0.205, 0.274, 0.458, 0.190, 0.652, 0.668, 0.12, 0.45),
    (0.45, 0.080, 0.214, 0.284, 0.458, 0.220, 0.652, 0.664, 0.12, 0.45),
    (0.20, 0.080, 0.214, 0.278, 0.460, 0.200, 0.680, 0.700, 0.14, 0.45),
    (-0.05, 0.080, 0.200, 0.244, 0.470, 0.118, 0.800, 0.846, 0.20, 0.45),
    (-0.45, 0.080, 0.180, 0.204, 0.450, 0.084, 0.748, 0.785, 0.22, 0.45),
    (-0.90, 0.085, 0.150, 0.164, 0.400, 0.064, 0.650, 0.680, 0.24, 0.45),
    (-1.30, 0.100, 0.120, 0.130, 0.356, 0.054, 0.568, 0.588, 0.26, 0.45),
    (-1.62, 0.128, 0.098, 0.104, 0.330, 0.050, 0.512, 0.524, 0.28, 0.45),
    (-1.80, 0.150, 0.084, 0.088, 0.318, 0.046, 0.480, 0.490, 0.30, 0.45),
    (-1.95, 0.180, 0.070, 0.075, 0.310, 0.040, 0.402, 0.412, 0.30, 0.45),
    (-2.25, 0.228, 0.050, 0.054, 0.308, 0.034, 0.370, 0.378, 0.30, 0.45),
    (-2.33, 0.250, 0.040, 0.044, 0.308, 0.030, 0.356, 0.362, 0.30, 0.45),
])
NOSE_TIP, BODY_END = 2.975, -2.33


def body_section(x, S=4):
    p = BODY(x)
    half = [(0.0, p['zr'], 0.5), (p['wt'] * 0.55, (p['zr'] + p['zt']) / 2 + 0.004, 0.5),
            (p['wt'], p['zt'], p['tt']), ((p['wt'] + p['ws']) / 2 + 0.006, (p['zt'] + p['zs']) / 2, 0.5),
            (p['ws'], p['zs'], p['ts']), (p['wb'], p['zb'], 0.28), (0.0, p['zb'], 0.5)]
    yz = bspline_closed(corners(mirror_half(half)), S)
    return np.column_stack([np.full(len(yz), x), yz[:, 0], yz[:, 1]])


def body_halfwidth(x, z):
    """Half-width of the main body at (x, z) (for placing pickups/mirrors)."""
    s = body_section(x)
    m = s[:, 1] > 0
    ys, zs = s[m, 1], s[m, 2]
    near = np.argsort(np.abs(zs - z))[:2]
    return float(ys[near].mean())


def body_top(x):
    return BODY(x)['zr']


def build_body():
    xs = list(np.linspace(NOSE_TIP, 2.70, 10)) + list(np.linspace(2.70, BODY_END, 160))[1:]
    rings = [body_section(x) for x in xs]
    # rounded nose tip: two shrinking rings ahead of the tip
    c = rings[0].mean(0)
    for dx, k in ((0.010, 0.82), (0.017, 0.5)):
        rings.insert(0, np.column_stack([np.full(len(rings[0]), NOSE_TIP + dx), (body_section(NOSE_TIP)[:, 1:] - c[1:]) * k + c[1:]]))

    def mat(c, r, i):
        return 1 if c.z < BODY(c.x)['zb'] + 0.012 else 0      # carbon underside, livery elsewhere
    return loft('Body', rings, [M['livery'], M['carbon']], mat, sharp=40)


# Sidepods (per side). Closed section in (y, z):
#  A inner-top (inside body) -> B top-outer -> C outer shoulder -> D undercut bottom -> E inner-bottom
POD = Track(['yo', 'zt', 'zsh', 'yu', 'zu', 'yi', 'tb', 'tc'], [
    # x      yo     ztop   zsh    yu     zu     yi     tB    tC
    (0.74, 0.560, 0.622, 0.560, 0.520, 0.448, 0.150, 0.18, 0.30),
    (0.62, 0.618, 0.628, 0.548, 0.470, 0.300, 0.150, 0.18, 0.30),
    (0.42, 0.656, 0.622, 0.520, 0.430, 0.175, 0.150, 0.20, 0.32),
    (0.00, 0.662, 0.572, 0.468, 0.452, 0.122, 0.140, 0.22, 0.34),
    (-0.45, 0.598, 0.482, 0.392, 0.460, 0.100, 0.120, 0.26, 0.36),
    (-0.90, 0.462, 0.392, 0.312, 0.380, 0.095, 0.100, 0.30, 0.38),
    (-1.28, 0.300, 0.322, 0.252, 0.262, 0.100, 0.080, 0.34, 0.40),
    (-1.52, 0.182, 0.282, 0.222, 0.160, 0.115, 0.060, 0.40, 0.42),
    (-1.66, 0.105, 0.262, 0.210, 0.090, 0.130, 0.050, 0.45, 0.45),
])
POD_FRONT, POD_END = 0.74, -1.66


def pod_section(x, side=1, S=5):
    p = POD(x)
    poly = [(p['yi'], p['zt'] + 0.01, 0.5), ((p['yi'] + p['yo']) / 2, p['zt'] + 0.004, 0.5),
            (p['yo'] - 0.05, p['zt'] - 0.006, p['tb']), (p['yo'], p['zsh'], p['tc']),
            (p['yu'], p['zu'], 0.3), (p['yi'], p['zu'] - 0.01, 0.5)]
    yz = bspline_closed(corners(poly), S)
    return np.column_stack([np.full(len(yz), x), side * yz[:, 0], yz[:, 1]])


def build_pod(side):
    xs = np.linspace(POD_FRONT, POD_END, 90)
    outer = [pod_section(x, side) for x in xs]
    s0 = outer[0]
    c = s0.mean(0)
    c[1] = side * (POD(POD_FRONT)['yi'] + POD(POD_FRONT)['yo']) / 2

    def shrink(k, dx):
        r = (s0 - c) * [0, k, k] + c
        r[:, 0] = POD_FRONT + dx
        return r
    # letterbox inlet: deep dark mouth -> inner wall -> rounded lip -> outer skin
    rings = [shrink(0.70, -0.22), shrink(0.82, -0.02), shrink(0.92, 0.006), shrink(0.985, 0.003)] + outer
    nm = 3
    # overbite: the upper lip leads the lower lip (2023-25 letterbox inlets), easing out over the first stations
    p0 = POD(POD_FRONT)
    for k in range(len(rings)):
        w = 1.0 if k < 6 else max(0.0, 1 - (k - 5) / 6)
        if w <= 0:
            break
        t = np.clip((rings[k][:, 2] - p0['zu']) / (p0['zt'] - p0['zu']), 0, 1)
        rings[k] = rings[k].copy()
        rings[k][:, 0] += 0.055 * t ** 1.5 * w

    def mat(cc, r, i):
        return 1 if r < nm - 1 or r == -1 else 0
    return loft('Sidepod_' + ('L' if side > 0 else 'R'), rings, [M['livery'], M['inlet']], mat, sharp=45)


def apply_mods(obj):
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
    obj.modifiers.clear()
    old = obj.data
    obj.data = me
    bpy.data.meshes.remove(old)


def boolean_cut(target, cutter, mat_slot_mat):
    if mat_slot_mat.name not in [m.name for m in target.data.materials]:
        target.data.materials.append(mat_slot_mat)
    cutter.data.materials.clear()
    cutter.data.materials.append(mat_slot_mat)
    md = target.modifiers.new('Cut', 'BOOLEAN')
    md.operation = 'DIFFERENCE'
    md.solver = 'EXACT'
    md.object = cutter
    try:
        md.material_mode = 'TRANSFER'
    except Exception:
        pass
    apply_mods(target)
    bpy.data.objects.remove(cutter)


def prism(name, outline_xy, z0, z1):
    bm = bmesh.new()
    lo = [bm.verts.new((x, y, z0)) for x, y in outline_xy]
    hi = [bm.verts.new((x, y, z1)) for x, y in outline_xy]
    bm.faces.new(lo)
    bm.faces.new(list(reversed(hi)))
    n = len(lo)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((lo[i], lo[j], hi[j], hi[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    col.objects.link(o)
    return o


COCKPIT = [(1.03, 0.0), (1.00, 0.10), (0.90, 0.19), (0.70, 0.232), (0.45, 0.238), (0.26, 0.228), (0.17, 0.19), (0.15, 0.0)]


def cockpit_outline(n=80):
    half = COCKPIT
    full = half + [(x, -y) for x, y in reversed(half[1:-1])]
    pts = bspline_closed(corners([(x, y, 0.45) for x, y in full]), 5)
    return [tuple(p) for p in pts]


print('building body ...')
body = build_body()
pods = [build_pod(1), build_pod(-1)]


def seg_intersect(p1, p2, q1, q2):
    d1, d2 = p2 - p1, q2 - q1
    den = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(den) < 1e-12:
        return None
    t = ((q1[0] - p1[0]) * d2[1] - (q1[1] - p1[1]) * d2[0]) / den
    u = ((q1[0] - p1[0]) * d1[1] - (q1[1] - p1[1]) * d1[0]) / den
    return (t, u) if 0 <= t <= 1 and 0 <= u <= 1 else None


def walk(poly, i, t, dist, step):
    """Walk `dist` along a polyline from the point at segment i, param t, in direction step (+1/-1)."""
    p = poly[i] + t * (poly[(i + 1) % len(poly)] - poly[i])
    k = (i + 1) % len(poly) if step > 0 else i
    left = dist
    while True:
        q = poly[k]
        d = np.linalg.norm(q - p)
        if d >= left:
            return p + (q - p) * (left / d)
        left -= d
        p = q
        k = (k + step) % len(poly)


def build_fillet(side, r=0.028):
    """Concave blend where the sidepod top meets the chassis flank: a quadratic Bezier per station whose end
    tangents run along both surfaces (control point = the intersection), lofted along x."""
    rings = []
    for x in np.linspace(0.70, -1.30, 70):
        ch = body_section(x)[:, 1:]
        ch = ch[: len(ch) // 2 + 1]                               # top centre -> +y side -> bottom centre
        pod = pod_section(x, 1)[:, 1:]
        best = None
        for i in range(len(pod)):
            for j in range(len(ch) - 1):
                hit = seg_intersect(pod[i], pod[(i + 1) % len(pod)], ch[j], ch[j + 1])
                if hit:
                    z = pod[i][1] + hit[0] * (pod[(i + 1) % len(pod)][1] - pod[i][1])
                    if best is None or z > best[0]:
                        best = (z, i, hit[0], j, hit[1])
        if best is None:
            continue
        _, i, t, j, u = best
        C = pod[i] + t * (pod[(i + 1) % len(pod)] - pod[i])
        rr = r * min(1.0, (0.72 - x) / 0.12)                       # grow in from the inlet lip
        A = walk(ch, j, u, rr, -1)                                  # up the chassis flank
        B = walk(pod, i, t, rr, +1)                                 # outward along the sidepod top
        ts = np.linspace(0, 1, 9)[:, None]
        yz = (1 - ts) ** 2 * A + 2 * ts * (1 - ts) * C + ts ** 2 * B
        yz[:, 0] *= side
        # push 0.4 mm proud so it never z-fights the surfaces it blends
        rings.append(np.column_stack([np.full(len(yz), x), yz[:, 0] + side * 0.0004 * np.sin(ts[:, 0] * np.pi), yz[:, 1] + 0.0004]))
    return loft('SidepodFillet_' + ('L' if side > 0 else 'R'), rings, [M['livery']], cap0=False, cap1=False, closed=False,
                recalc=False, sharp=0)


fillets = [build_fillet(1), build_fillet(-1)]

# cockpit opening
boolean_cut(body, prism('CockpitCut', cockpit_outline(), 0.47, 1.3), M['interior'])

# NACA ducts on the chassis flanks
for s in (1, -1):
    x0, z0 = 1.34, 0.50
    hw = body_halfwidth(x0, z0)
    bm = bmesh.new()
    # tapered wedge: narrow mouth at front sloping into the surface toward the rear
    pts = [(0.0, 0.004), (0.16, 0.016), (0.16, -0.016), (0.0, -0.004)]
    vs_out = [bm.verts.new((x0 + 0.08 - px, s * (hw + 0.03), z0 + pz)) for px, pz in pts]
    vs_in = [bm.verts.new((x0 + 0.08 - px, s * (hw - 0.012 * (px / 0.16) - 0.0005), z0 + pz)) for px, pz in pts]
    bm.faces.new(vs_out)
    bm.faces.new(vs_in)
    for i in range(4):
        j = (i + 1) % 4
        bm.faces.new((vs_out[i], vs_out[j], vs_in[j], vs_in[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new('NACA')
    bm.to_mesh(me)
    bm.free()
    cut = bpy.data.objects.new('NACA', me)
    col.objects.link(cut)
    boolean_cut(body, cut, M['inlet'])
finish(body, 40)

# ---------------------------------------------------------------- airbox / roll hoop
AIR = Track(['zb', 'w', 'zs', 'wt', 'zt', 'zr'], [
    (0.255, 0.640, 0.090, 0.840, 0.040, 0.935, 0.948),
    (0.20, 0.630, 0.108, 0.840, 0.050, 0.938, 0.952),
    (0.05, 0.640, 0.118, 0.830, 0.056, 0.932, 0.948),
    (-0.25, 0.620, 0.100, 0.800, 0.050, 0.880, 0.896),
    (-0.55, 0.580, 0.070, 0.720, 0.040, 0.782, 0.794),
    (-0.85, 0.520, 0.040, 0.600, 0.026, 0.640, 0.648),
])


def air_section(x):
    p = AIR(x)
    half = [(0.0, p['zr'], 0.5), (p['wt'], p['zt'], 0.22), (p['w'], p['zs'], 0.35), (p['w'] * 0.9, p['zb'], 0.4), (0.0, p['zb'], 0.5)]
    yz = bspline_closed(corners(mirror_half(half)), 4)
    return np.column_stack([np.full(len(yz), x), yz[:, 0], yz[:, 1]])


def build_airbox():
    xs = np.linspace(0.255, -0.85, 50)
    rings = [air_section(x) for x in xs]
    c = rings[0].mean(0)
    for dx, k in ((0.008, 0.9), (0.012, 0.6)):
        r = (air_section(0.255) - c) * [0, k, k] + c
        r[:, 0] = 0.255 + dx
        rings.insert(0, r)
    o = loft('Airbox', rings, [M['livery'], M['blue']], lambda c, r, i: 1 if c.x > 0.232 else 0, sharp=45)
    # intake mouth: rounded-triangle recess
    tri = [(0.0, 0.925), (0.03, 0.92), (0.078, 0.85), (0.07, 0.83), (0.0, 0.83)]
    full = tri + [(-y, z) for y, z in reversed(tri[1:-1])]
    prof = bspline_closed(corners([(y, z, 0.35) for y, z in full]), 4)
    bm = bmesh.new()
    lo = [bm.verts.new((0.40, y, z)) for y, z in prof]
    hi = [bm.verts.new((0.07, y * 0.8, 0.88 + (z - 0.88) * 0.8)) for y, z in prof]
    bm.faces.new(lo)
    bm.faces.new(list(reversed(hi)))
    for i in range(len(lo)):
        j = (i + 1) % len(lo)
        bm.faces.new((lo[i], lo[j], hi[j], hi[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    me = bpy.data.meshes.new('AirCut')
    bm.to_mesh(me)
    bm.free()
    cut = bpy.data.objects.new('AirCut', me)
    col.objects.link(cut)
    boolean_cut(o, cut, M['inlet'])
    finish(o, 45)
    return o


airbox = build_airbox()
# T-camera on top of the roll hoop
tcam = loft('TCam', [np.column_stack([np.full(12, x), np.array(ellipse(w, h, 12))[:, 0], np.array(ellipse(w, h, 12))[:, 1] + 0.968])
                     for x, w, h in ((0.19, 0.01, 0.006), (0.17, 0.05, 0.016), (0.10, 0.055, 0.017), (0.06, 0.03, 0.01))],
            [M['blue']], sharp=60)          # T-cam in the driver's colour (electric blue)

# ---------------------------------------------------------------- livery: baked 3D-aware atlas
# Every painted panel gets a packed 'Livery' UV atlas. Cycles bakes world POSITION, NORMAL and a part-ID
# into it; the livery is then *painted in numpy as a function of 3D position + normal*, so graphics follow
# feature lines, wrap over the top surfaces the web camera sees, and stay crisp at any curvature.
LIVERY = arg_after('--livery', 'D')
ATLAS = 2048


def smoothstep(a, b, x):
    t = np.clip((x - a) / (b - a), 0, 1)
    return t * t * (3 - 2 * t)


def build_livery_atlas(objs):
    for o in objs:
        if o.modifiers:
            apply_mods(o)
        if 'Livery' not in o.data.uv_layers:
            o.data.uv_layers.new(name='Livery')
        o.data.uv_layers.active = o.data.uv_layers['Livery']
    bpy.ops.object.select_all(action='DESELECT')
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.mode_set(mode='EDIT')
    bpy.ops.mesh.select_all(action='SELECT')
    bpy.ops.uv.smart_project(angle_limit=math.radians(52), island_margin=0.002, area_weight=0.0,
                             correct_aspect=True, scale_to_bounds=False)
    bpy.ops.uv.select_all(action='SELECT')
    bpy.ops.uv.average_islands_scale()
    bpy.ops.uv.pack_islands(margin=0.0025, rotate=True)
    bpy.ops.object.mode_set(mode='OBJECT')
    for o in objs:
        o.data.uv_layers.active = o.data.uv_layers['UVMap'] if 'UVMap' in o.data.uv_layers else o.data.uv_layers['Livery']


def bake_livery_maps(objs, size=ATLAS):
    """Bake world position, world normal and part-ID into the Livery atlas. Returns (P, N, ID, mask)."""
    scene.render.engine = 'CYCLES'
    use_gpu()
    scene.cycles.samples = 1
    scene.cycles.use_denoising = False
    saved = {o.name: [sl.material for sl in o.material_slots] for o in objs}
    renders = {}
    for o in objs:
        o.data.uv_layers['Livery'].active = True
        o.data.uv_layers['Livery'].active_render = True
    for kind in ('POSITION', 'NORMAL', 'EMIT'):
        img = bpy.data.images.new('bake_' + kind, size, size, alpha=True, float_buffer=True)
        for idx, o in enumerate(objs):
            mb = bpy.data.materials.new('bake_%s_%d' % (kind, idx))
            try:
                mb.use_nodes = True
            except Exception:
                pass
            nt = mb.node_tree
            tn = nt.nodes.new('ShaderNodeTexImage'); tn.image = img
            nt.nodes.active = tn
            if kind == 'EMIT':
                em = nt.nodes.new('ShaderNodeEmission')
                em.inputs['Color'].default_value = ((idx + 1) / 64.0, 1.0, 0.0, 1)
                nt.links.new(em.outputs['Emission'], nt.nodes['Material Output'].inputs['Surface'])
            for sl in o.material_slots:
                sl.material = mb
        bpy.ops.object.select_all(action='DESELECT')
        for o in objs:
            o.select_set(True)
        bpy.context.view_layer.objects.active = objs[0]
        kw = dict(type=kind, margin=10, use_clear=True)
        if kind == 'NORMAL':
            kw['normal_space'] = 'OBJECT'
        bpy.ops.object.bake(**kw)
        a = np.empty(size * size * 4, np.float32)
        img.pixels.foreach_get(a)
        renders[kind] = a.reshape(size, size, 4)[::-1].copy()      # row 0 = top (v = 1)
    for o in objs:
        for sl, m in zip(o.material_slots, saved[o.name]):
            sl.material = m
        o.data.uv_layers['UVMap' if 'UVMap' in o.data.uv_layers else 'Livery'].active_render = True
    P = renders['POSITION'][..., :3]
    N = renders['NORMAL'][..., :3] * 2 - 1
    E = renders['EMIT']
    mask = E[..., 1] > 0.5
    ID = np.rint(E[..., 0] * 64).astype(int) - 1
    return P, N, ID, mask


def crisp(f, px_floor=1e-4):
    """Anti-aliased step of a signed field in texel space (f > 0 inside), robust at island seams."""
    gy, gx = np.gradient(f)
    g = np.sqrt(gx * gx + gy * gy)
    lim = np.nanpercentile(g, 90) * 4 + px_floor
    g = np.clip(g, px_floor, lim)
    return np.clip(f / g + 0.5, 0, 1)


def mixc(a, b, t):
    return a * (1 - t[..., None]) + np.asarray(b) * t[..., None]


def livery_paint(P, N, ID, mask, parts, concept):
    """Return (rgb_lin, coat, rough, metal, height) for the atlas. `parts` maps part-name -> id."""
    x, y, z = P[..., 0], P[..., 1], P[..., 2]
    ay, nz, ny = np.abs(y), N[..., 2], np.abs(N[..., 1])
    pid = lambda *names: np.isin(ID, [parts[n] for n in names if n in parts])
    is_body, is_pod = pid('Body'), pid('Sidepod_L', 'Sidepod_R', 'SidepodFillet_L', 'SidepodFillet_R')
    is_air, is_fin, is_ep = pid('Airbox'), pid('Fin'), pid('RW_Endplate_L', 'RW_Endplate_R')
    is_helmet = pid('Helmet')
    zr = pchip(BODY.x, BODY.p['zr'], x.ravel()).reshape(x.shape)
    zs = pchip(BODY.x, BODY.p['zs'], np.clip(x, -2.3, 2.9).ravel()).reshape(x.shape)
    podt = pchip(POD.x, POD.p['zt'], np.clip(x, POD_END, POD_FRONT).ravel()).reshape(x.shape)
    podsh = pchip(POD.x, POD.p['zsh'], np.clip(x, POD_END, POD_FRONT).ravel()).reshape(x.shape)

    BLACK = np.array((0.0032, 0.0036, 0.0046))
    blue, deep, navy = np.array(BLUE), np.array(DEEP), np.array(NAVY)
    rgb = np.broadcast_to(BLACK, P.shape).copy()
    paint = np.zeros(x.shape)            # 1 = gloss colour, 0 = satin black
    pin = np.zeros(x.shape)
    white = np.zeros(x.shape)            # white graphic (number panels etc.)

    def along(stops):
        xs = [a for a, _ in stops][::-1]
        return np.stack([pchip(xs, [c[k] for _, c in stops][::-1], x.ravel()).reshape(x.shape) for k in range(3)], -1)

    def lay(alpha, color):
        nonlocal rgb, paint
        rgb = mixc(rgb, color, alpha)
        paint = np.maximum(paint, alpha)

    if concept == 'A':
        # Value progression (Mercedes/Aston logic): electric-blue front third, crisp diagonal slash on the
        # monocoque, navy mid-section, second slash through the sidepod, satin black rear.
        s1 = crisp((1.20 + (0.66 - z) * 1.1 + ay * 0.9) - x) * 0 + crisp(x - (1.20 + (0.66 - z) * 1.1 - ay * 0.9))
        s2 = crisp(x - (-0.35 + (0.62 - z) * 0.9 - ay * 0.4))
        lay(s2, along([(3, deep), (0.8, navy), (-0.6, navy * 0.6)]))
        lay(s1, along([(3.0, blue * 1.05), (2.0, blue), (1.3, deep)]))
        pin = np.maximum(crisp(0.0028 - np.abs(x - (1.20 + (0.66 - z) * 1.1 - ay * 0.9) + 0.012)),
                         crisp(0.0028 - np.abs(x - (-0.35 + (0.62 - z) * 0.9 - ay * 0.4) + 0.012)))
        pin *= (is_body | is_pod | is_air)
    elif concept == 'B':
        # VCARB logic: lines that work *with* the sidepod swoop + a diagonal swoosh on the monocoque.
        # (1) nose + monocoque: electric blue, swept-back chevron edge between the front wheels and the halo
        edge1 = 1.06 + (0.66 - z) * 1.25 + ay * 0.85                    # chevron seen from above
        nose = crisp(x - edge1) * (is_body | is_air)
        lay(nose, along([(3.0, blue * 1.08), (2.3, blue), (1.35, deep)]))
        pin = np.maximum(pin, crisp(0.0026 - np.abs(x - edge1 + 0.014)) * is_body)
        # (2) sidepod swoop: the blade rides the sidepod shoulder, crisp leading edge at the inlet lip,
        #     soft trailing fade to navy then black as it dives with the downwash
        blade_top = podt + 0.05
        blade_bot = podsh - 0.012 - 0.02 * smoothstep(0.6, -0.8, x)
        blade = crisp(z - blade_bot) * crisp(blade_top - z) * (is_pod | is_body) * (x < 0.9)
        fade = smoothstep(-1.45, -0.55, x)
        blade_col = along([(0.9, blue * 1.05), (0.1, blue), (-0.6, deep), (-1.3, navy)])
        blade = blade * np.clip(fade + 0.0, 0, 1) * (ay > 0.2)
        lay(blade, blade_col)
        pin = np.maximum(pin, crisp(0.0024 - np.abs(z - (blade_bot - 0.012))) * (is_pod) * (x < 0.65) * (x > -0.9)
                         * smoothstep(-0.9, -0.5, x) * (ay > 0.3))
        # (3) spine: electric-blue stripe down the airbox and engine cover into the fin, narrowing rearward
        w = np.interp(-x, [-0.3, 0.2, 1.0, 1.7], [0.10, 0.075, 0.035, 0.012])
        spine = crisp(w - ay) * (nz > 0.2) * (is_air | is_body) * (x < 0.28) * (x > -1.75) * (z > zr - 0.03)
        lay(spine, along([(0.3, blue), (-0.6, blue), (-1.7, deep)]))
        fin = is_fin * np.clip(smoothstep(-0.2, -1.4, x) * 0.7 + 0.3, 0, 1)
        lay(fin, along([(0.0, navy), (-0.9, deep), (-1.7, blue)]))
        # (4) rear-wing endplates: electric at the rolled tip fading down to black, pinstripe at the fade
        ep = is_ep * smoothstep(0.52, 0.86, z)
        lay(ep, np.broadcast_to(blue, P.shape) * (0.6 + 0.45 * smoothstep(0.52, 0.95, z))[..., None])
    elif concept == 'D':
        # Final: A's value progression (bright where the camera looks first, dark at the rear) + one VCARB-style
        # hero swoosh that rides the sidepod shoulder, dives with the downwash and climbs into the fin.
        slash1 = 1.18 + (0.66 - z) * 1.15 - ay * 0.95          # swept chevron on the monocoque (seen from above)
        slash2 = -0.30 + (0.62 - z) * 0.85 - ay * 0.45          # second cut through the sidepod
        mid = crisp(x - slash2) * (is_body | is_pod | is_air)
        lay(mid, along([(1.2, deep), (0.4, navy), (-0.6, navy * 0.55)]))
        front = crisp(x - slash1) * (is_body | is_air)
        lay(front, along([(3.0, blue * 1.1), (2.2, blue), (1.3, blue * 0.8 + deep * 0.2)]))
        pin = np.maximum(pin, crisp(0.0026 - np.abs(x - slash1 + 0.013)) * is_body)
        pin = np.maximum(pin, crisp(0.0024 - np.abs(x - slash2 + 0.012)) * (is_body | is_pod) * (z > 0.2))
        # satin-black lower body: blue/navy live on the upper surfaces only (gloss upper / satin lower)
        lower = (crisp((podsh - 0.055) - z) * is_pod + crisp((zs - 0.07) - z) * is_body * (x > -1.0) * (x < 2.2)) * (nz < 0.5)
        rgb = mixc(rgb, BLACK, np.clip(lower, 0, 1))
        paint = paint * (1 - np.clip(lower, 0, 1))
        # hero swoosh along the shoulder -> downwash -> coke -> fin
        kx = [0.74, 0.40, 0.0, -0.45, -0.90, -1.20, -1.45, -1.66]
        zc = pchip(kx[::-1], [0.566, 0.548, 0.515, 0.440, 0.335, 0.292, 0.360, 0.470][::-1], np.clip(x, -1.66, 0.74).ravel()).reshape(x.shape)
        hh = pchip(kx[::-1], [0.030, 0.028, 0.025, 0.021, 0.018, 0.018, 0.024, 0.030][::-1], np.clip(x, -1.66, 0.74).ravel()).reshape(x.shape)
        sw = crisp(hh - np.abs(z - zc)) * (ny > 0.25) * (is_pod | is_body) * crisp(0.72 - x) * smoothstep(-1.70, -1.55, x)
        lay(sw, along([(0.8, blue * 1.15), (-0.4, blue), (-1.7, blue * 0.9)]))
        pin = np.maximum(pin, crisp(0.0022 - np.abs(z - (zc - hh - 0.010))) * (ny > 0.25) * (is_pod | is_body)
                         * crisp(0.70 - x) * smoothstep(-1.2, -0.8, x))
        # spine stripe down the airbox + engine cover into the fin
        w = np.interp(-x, [-0.3, 0.2, 1.0, 1.7], [0.085, 0.06, 0.03, 0.012])
        spine = crisp(w - ay) * (nz > 0.25) * (is_air | is_body) * (x < 0.26) * (x > -1.75) * (z > zr - 0.03)
        lay(spine, along([(0.3, blue), (-0.8, blue), (-1.7, deep)]))
        lay(is_fin * 1.0, along([(0.0, navy), (-0.8, deep), (-1.7, blue)]))
        ep = is_ep * smoothstep(0.50, 0.90, z)
        lay(ep, np.broadcast_to(blue, P.shape) * (0.55 + 0.5 * smoothstep(0.5, 0.95, z))[..., None])
    else:
        # C: metallic electric-blue flanks, satin black spine; the shoulder line (normal turning from up to
        # side) is the boundary, underlined in silver.
        flank = crisp(ny - 0.62) * crisp(z - 0.14) * (is_body | is_pod) * (x > -1.6)
        lay(flank, along([(3.0, blue * 1.05), (1.0, blue), (-0.5, deep), (-1.5, navy)]))
        pin = crisp(0.02 - np.abs(ny - 0.60)) * (is_body | is_pod) * (x > -1.4) * (z > 0.3)
        lay(is_fin * 1.0, np.broadcast_to(deep, P.shape))
        lay(is_ep * smoothstep(0.52, 0.86, z), np.broadcast_to(blue, P.shape))

    # helmet in the livery: electric crown fading to navy, black chin, silver pin line at the visor brow
    hz = z - 0.745
    lay(is_helmet * smoothstep(-0.02, 0.10, hz), along([(0.6, blue), (0.3, deep)]))
    pin = np.maximum(pin, is_helmet * crisp(0.004 - np.abs(hz - 0.012)))

    # silver pinstripes
    rgb = mixc(rgb, SILVER, pin)
    # panel lines + fasteners, in 3D
    groove = np.zeros(x.shape)
    band = lambda f, w=0.0011: crisp(w - np.abs(f))
    groove = np.maximum(groove, band(x - 1.95) * is_body * (z > 0.12))                       # nose/chassis joint
    groove = np.maximum(groove, band(x + 0.32) * (is_body | is_air) * (z > 0.44))             # engine-cover front
    groove = np.maximum(groove, band(z - (zs + 0.03)) * is_body * (x < -0.32) * (x > -1.72))  # cover/sidepod split
    groove = np.maximum(groove, band(x - 0.55) * is_pod * (z > 0.2))                          # inlet surround
    groove = np.maximum(groove, band(x + 1.72) * is_body * (z > 0.3))
    bolts = np.zeros(x.shape)
    bp = [(-0.32 - 0.016, zz) for zz in np.arange(0.48, 0.92, 0.065)] + \
         [(xx, float(pchip(BODY.x, BODY.p['zs'], xx)[0]) + 0.046) for xx in np.arange(-0.42, -1.66, -0.12)] + \
         [(1.95 - 0.016, zz) for zz in np.arange(0.2, 0.58, 0.07)]
    for bx, bz in bp:
        near = mask & (np.abs(x - bx) < 0.01) & (np.abs(z - bz) < 0.01) & (is_body | is_air)
        if not near.any():
            continue
        d = np.sqrt((x[near] - bx) ** 2 + (z[near] - bz) ** 2)
        bolts[near] = np.maximum(bolts[near], np.clip((0.0034 - d) / 0.0006 + 0.5, 0, 1))
        groove[near] = np.maximum(groove[near], np.clip((0.0006 - np.abs(d - 0.0040)) / 0.0005 + 0.5, 0, 1))
    rgb = rgb * (1 - 0.8 * groove[..., None])
    rgb = mixc(rgb, (0.32, 0.33, 0.35), bolts)
    painted = np.clip(paint + pin, 0, 1)
    coat = painted                                   # satin black carries no clearcoat: only the colour is gloss
    rough = 0.58 - 0.36 * painted + 0.15 * groove
    metal = 0.45 - 0.1 * paint + 0.5 * np.clip(pin + bolts, 0, 1)      # dark-base metallic keeps satin black from greying
    height = -groove + 0.6 * bolts
    return rgb, coat, np.clip(rough, 0, 1), np.clip(metal, 0, 1), height


def livery_atlas(objs, concept):
    parts = {}
    for i, o in enumerate(objs):
        parts[o.name.split('.')[0] if not o.name.startswith('RW_Endplate') else o.name] = i
    P, N, ID, mask = bake_livery_maps(objs)
    rgb, coat, rough, metal, hgt = livery_paint(P, N, ID, mask, parts, concept)
    nrm = height_to_normal(np.where(mask, hgt, 0), 1.4)
    orm = np.stack([coat, rough, metal], -1)
    orm = orm.reshape(ATLAS // 2, 2, ATLAS // 2, 2, 3).mean((1, 3))          # finish varies slowly: 1K is plenty
    return (save_img('livery_basecolor', to_srgb(rgb)), save_img('livery_orm', orm, True),
            save_img('livery_normal', nrm, True))


def setup_livery(imgs):
    m = M['livery']
    nt = m.node_tree
    for n in [n for n in nt.nodes if n.type in ('TEX_IMAGE', 'UVMAP', 'SEPARATE_COLOR', 'NORMAL_MAP')]:
        nt.nodes.remove(n)
    base, orm, nrm = imgs
    add_base(m, base, uv='Livery')
    sep = add_orm(m, orm, uv='Livery')
    nt.links.new(sep.outputs['Red'], nt.nodes['Principled BSDF'].inputs['Coat Weight'])
    add_normal(m, nrm, 0.8, uv='Livery')


# ---------------------------------------------------------------- engine-cover fin
def fin_outline():
    # 2024-25 style: a slim fin that tracks the engine cover (7 cm proud, tapering to 3 cm)
    xs = [0.02, -0.30, -0.70, -1.10, -1.40, -1.55]
    top = [(0.02, 0.938)] + [(x, float(BODY(x)['zr']) + 0.075 - 0.03 * (-x / 1.55)) for x in xs[1:]]
    te = [(-1.585, float(BODY(-1.585)['zr']) + 0.02), (-1.60, BODY(-1.60)['zr'] - 0.02)]
    bottom = [(x, BODY(x)['zr'] - 0.035) for x in np.linspace(-1.58, -0.2, 10)] + [(-0.1, 0.80), (0.0, 0.86)]
    return top + te + bottom


fin = plate('Fin', list(reversed(fin_outline())), 0.009, 0.0, M['livery'], bevel=0.0025)

# ---------------------------------------------------------------- floor
FLOOR_Z, FLOOR_T = 0.066, 0.011
floor_half = [(1.34, 0.12), (1.26, 0.30), (1.10, 0.50), (0.94, 0.68), (0.80, 0.785), (0.60, 0.812), (-1.18, 0.812),
              (-1.32, 0.76), (-1.44, 0.66), (-1.50, 0.60), (-1.52, 0.52), (-1.52, 0.0)]


def build_floor():
    pts = bspline_open(floor_half[:-1], 4).tolist() + [floor_half[-1]]
    full = pts + [(x, -y) for x, y in reversed(pts[:-1])][1:]
    bm = bmesh.new()
    lo = [bm.verts.new((x, y, FLOOR_Z)) for x, y in full]
    hi = [bm.verts.new((x, y, FLOOR_Z + FLOOR_T)) for x, y in full]
    bm.faces.new(lo)
    bm.faces.new(list(reversed(hi)))
    n = len(lo)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((lo[i], lo[j], hi[j], hi[i]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bmesh.ops.triangulate(bm, faces=[f for f in bm.faces if len(f.verts) > 4], quad_method='BEAUTY', ngon_method='BEAUTY')
    o = mesh_from_bm('Floor', bm, [M['carbon']], sharp=30)
    bv = o.modifiers.new('Bevel', 'BEVEL')
    bv.width, bv.segments, bv.limit_method, bv.harden_normals = 0.003, 2, 'ANGLE', True
    box_uv(o)
    return o


floor = build_floor()
# upturned floor edge + floor-edge wing + edge scroll ahead of the rear tyre
for s in (1, -1):
    plate('FloorEdgeLip', [(0.55, FLOOR_Z), (-1.16, FLOOR_Z), (-1.16, FLOOR_Z + 0.05), (-0.9, FLOOR_Z + 0.056), (0.3, FLOOR_Z + 0.03), (0.55, FLOOR_Z + 0.012)],
          0.006, s * 0.808, M['carbon'], bevel=0.0015)
    rings = []
    prof = naca(0.1, 0.06)
    for x in np.linspace(-0.20, -1.12, 22):
        t = (x + 0.2) / -0.92
        zc = FLOOR_Z + 0.07 + 0.012 * math.sin(t * math.pi)
        ring = []
        for u, v in prof:
            # chord runs outboard -> inboard (flow turns inward around the tyre)
            yy = s * (0.83 - u * 0.075)
            ring.append((x, yy, zc + v * 0.075 + u * 0.075 * 0.25))
        rings.append(ring)
    loft('FloorEdgeWing', rings, [M['carbon']], sharp=30)
    # floor-edge stays
    strut('FloorStay', (-0.55, s * 0.80, FLOOR_Z + 0.02), (-0.50, s * 0.60, 0.30), 0.012, 0.006, M['ti'])
    # floor fences under the leading edge (Venturi tunnel inlets)
    for k, y0 in enumerate((0.20, 0.30, 0.40, 0.50)):
        out = [(1.30 - 0.02 * k, FLOOR_Z + FLOOR_T), (0.92, FLOOR_Z + FLOOR_T), (0.92, 0.035), (1.18 - 0.03 * k, 0.018), (1.32 - 0.02 * k, 0.03)]
        f = plate('FloorFence', out, 0.006, s * y0, M['carbon'], bevel=0.0012,
                  bend=lambda x, z, s=s, k=k: s * (0.05 + 0.02 * k) * ((1.30 - x) / 0.4) ** 2)

# plank / skid block
rbox('Plank', (2.5, 0.30, 0.03), (-0.05, 0, FLOOR_Z - 0.016), M['plank'], bevel=0.004)

# ---------------------------------------------------------------- diffuser
def build_diffuser():
    xs = np.linspace(-1.50, -2.22, 26)
    rings = []
    for x in xs:
        t = (x + 1.5) / -0.72
        z = FLOOR_Z + 0.02 + 0.24 * t ** 1.6
        hw = 0.52
        ys = np.linspace(-hw, hw, 41)
        # two tunnel roofs + central section
        prof = z + 0.03 * np.cos(ys / hw * math.pi * 2) * t
        top = np.column_stack([np.full(41, x), ys, prof + 0.008])
        bot = np.column_stack([np.full(41, x), ys[::-1], prof[::-1]])
        rings.append(np.vstack([top, bot]))
    o = loft('Diffuser', rings, [M['carbon']], sharp=35)
    box_uv(o)
    for s in (1, -1):
        plate('DiffuserFence', [(-1.44, FLOOR_Z), (-2.22, FLOOR_Z + 0.02), (-2.22, 0.36), (-1.9, 0.20), (-1.5, FLOOR_Z + 0.03)],
              0.008, s * 0.522, M['carbon'], bevel=0.002)
        for y0 in (0.14, 0.34):
            plate('DiffuserStrake', [(-1.62, FLOOR_Z + 0.02), (-2.20, FLOOR_Z + 0.02), (-2.20, 0.33), (-1.95, 0.20), (-1.66, 0.10)],
                  0.005, s * y0, M['carbon'], bevel=0.0012)
    return o


diffuser = build_diffuser()

# ---------------------------------------------------------------- front wing
# 2024-25 front wing: chord and angle vary strongly across the span. Near the nose the flaps are short and
# flat and rise to meet the nose (which sits on element 2) over a dipped neutral centre section; mid-span they
# are deepest; the outboard 15 % sweeps up steeply and rolls into the endplate.
FW_S = [0.0, 0.15, 0.45, 0.75, 0.90, 1.0]
FW_TAB = [  # (chord table, pitch table, t/c)
    ([0.300, 0.292, 0.272, 0.245, 0.215, 0.195], [2, 3, 4, 6, 8, 10], 0.10),
    ([0.110, 0.150, 0.190, 0.172, 0.135, 0.100], [7, 12, 16, 20, 24, 28], 0.085),
    ([0.082, 0.120, 0.160, 0.150, 0.120, 0.090], [12, 19, 26, 30, 34, 38], 0.08),
    ([0.062, 0.098, 0.135, 0.128, 0.105, 0.080], [18, 27, 35, 39, 42, 45], 0.075)   # tips capped at 45 deg: a moderate sweep, not a curl,
]
FW_ELEMS = [(None, None, t[2]) for t in FW_TAB]
FW_HALF = 0.962


def fw_stack(y):
    """Leading edges (x, z), chord and pitch of all four elements at span position y."""
    ay = abs(y)
    s = min(1.0, max(0.0, (ay - 0.24) / (FW_HALF - 0.24)))
    core = max(0.0, 1 - ay / 0.16) ** 2                       # under the nose
    xle = 3.035 - 0.05 * s ** 1.5
    zle = 0.072 + 0.018 * s + 0.012 * s ** 4 - 0.008 * max(0.0, 1 - ay / 0.25) ** 2   # neutral-section dip
    out = []
    for k, (ctab, ptab, tc) in enumerate(FW_TAB):
        c = float(pchip(FW_S, ctab, s)[0])
        pt = float(pchip(FW_S, ptab, s)[0])
        if k >= 1:
            zle += 0.028 * core                               # flaps rise to meet the nose on element 2
        out.append((xle, zle, c, pt))
        a = math.radians(pt)
        xte, zte = xle - c * math.cos(a), zle + c * math.sin(a)
        xle = xte + 0.030 - 0.03 * s ** 2 - 0.02 * s ** 6
        zle = zte + 0.010 + 0.006 * s ** 3                    # slot gap
    return out


def build_front_wing():
    ys = np.concatenate([np.linspace(-FW_HALF, -0.3, 44), np.linspace(-0.3, 0.3, 21)[1:-1], np.linspace(0.3, FW_HALF, 44)])
    for k in range(4):
        if k == 3:
            # top flap: blue inboard; outer ~30 % bare carbon with only a thin blue leading-edge stripe
            nprof = len(naca(FW_ELEMS[k][2], 0.05))
            le = nprof // 2
            fn = lambda c, r, i, le=le: 0 if (abs(c.y) < 0.70 * FW_HALF or abs(i - le) <= 2) else 1
            w = wing('FW_Element4', ys, lambda y: fw_stack(y)[3], [M['blue'], M['carbon']], tc=FW_ELEMS[k][2], camber=0.05, matfn=fn)
        else:
            w = wing('FW_Element%d' % (k + 1), ys, lambda y, k=k: fw_stack(y)[k], [M['carbon']], tc=FW_ELEMS[k][2], camber=0.05)
        box_uv(w)
    for s in (1, -1):
        y = s * (FW_HALF + 0.022)
        tip = fw_stack(FW_HALF)
        top_te = tip[3]
        a = math.radians(top_te[3])
        xte, zte = top_te[0] - top_te[2] * math.cos(a), top_te[1] + top_te[2] * math.sin(a)
        outline = [(3.06, 0.055), (3.06, 0.14), (2.98, 0.20), (2.80, 0.26), (xte + 0.06, zte + 0.02), (xte - 0.02, zte + 0.01),
                   (xte - 0.03, 0.20), (2.32, 0.10), (2.30, 0.055)]
        plate('FW_Endplate', outline, 0.010, y, M['carbon'], bevel=0.002,
              bend=lambda x, z, s=s: -s * 0.012 * max(0.0, (z - 0.14) / 0.2) ** 2 - s * 0.01 * max(0.0, (2.6 - x) / 0.3) ** 2)
        # footplate / diveplane
        # footplate with an upturned outer lip, and a small diveplane on the endplate
        plate('FW_Footplate', [(3.06, 0.050), (2.30, 0.050), (2.36, 0.060), (3.03, 0.060)], 0.11, s * (FW_HALF - 0.045), M['carbon'], bevel=0.002)
        plate('FW_FootLip', [(3.0, 0.058), (2.36, 0.058), (2.40, 0.085), (2.95, 0.075)], 0.006, s * (FW_HALF + 0.045), M['carbon'], bevel=0.0015)
        # slot-gap separators: short brackets bridging each slot at two span stations
        for yy in (0.50, 0.76):
            st = fw_stack(yy)
            for k in range(3):
                a_ = math.radians(st[k][3])
                te = (st[k][0] - st[k][2] * math.cos(a_), st[k][1] + st[k][2] * math.sin(a_))
                le = st[k + 1]
                out = [(te[0] + 0.018, te[1] - 0.006), (te[0] - 0.004, te[1] - 0.004), (le[0] - 0.012, le[1] + 0.008), (le[0] + 0.006, le[1] + 0.004)]
                plate('FW_SlotSeparator', out, 0.003, s * yy, M['carbon'], bevel=0.0008)
        # flap-adjuster actuator pod on the top flap, just outboard of the nose
        st = fw_stack(0.30)[3]
        a4 = math.radians(st[3])
        mx_, mz_ = st[0] - st[2] * 0.45 * math.cos(a4), st[1] + st[2] * 0.45 * math.sin(a4) + 0.012
        loft('FW_AdjusterPod', [np.column_stack([np.full(12, xx), np.array(ellipse(w, h, 12))[:, 0] + s * 0.30,
                                                 np.array(ellipse(w, h, 12))[:, 1] + mz_ + (mx_ - xx) * math.tan(a4) * 0.6])
                                for xx, w, h in ((mx_ + 0.05, 0.004, 0.003), (mx_ + 0.03, 0.016, 0.011), (mx_ - 0.03, 0.017, 0.012), (mx_ - 0.055, 0.006, 0.005))],
             [M['carbon']], sharp=60)



build_front_wing()

# ---------------------------------------------------------------- rear wing
RW_HALF = 0.472


def rw_main(y):
    s = abs(y) / RW_HALF
    spoon = 1 - s ** 2.2
    roll = max(0.0, (s - 0.86) / 0.14) ** 2                 # 2022+ rounded tip: the wing rolls down into the endplate
    return (-2.02 - 0.015 * s, 0.705 + 0.045 * spoon - 0.035 * roll, 0.27 + 0.05 * spoon - 0.02 * roll, 9 + 3 * spoon)


def rw_flap(y):
    xle, zle, c, p = rw_main(y)
    a = math.radians(p)
    xte, zte = xle - c * math.cos(a), zle + c * math.sin(a)
    s = abs(y) / RW_HALF
    roll = max(0.0, (s - 0.86) / 0.14) ** 2
    return (xte + 0.035, zte + 0.022 - 0.03 * roll, 0.205 - 0.01 * s - 0.03 * roll, 38 + 4 * s - 10 * roll)


def build_rear_wing():
    ys = np.linspace(-RW_HALF, RW_HALF, 41)
    box_uv(wing('RW_Mainplane', ys, rw_main, [M['carbon']], tc=0.10, camber=0.07))
    box_uv(wing('RW_DRSFlap', ys, rw_flap, [M['blue']], tc=0.08, camber=0.05))
    for s in (1, -1):
        y = s * (RW_HALF + 0.005)
        # 2022+ endplate: generous radii where it rolls into the wing (no sharp top corners), waisted leg to the beam wing
        ctrl = [(-1.99, 0.68, 0.4), (-2.03, 0.82, 0.45), (-2.16, 0.915, 0.45), (-2.36, 0.935, 0.45), (-2.49, 0.905, 0.4),
                (-2.51, 0.80, 0.3), (-2.50, 0.66, 0.3), (-2.44, 0.50, 0.35), (-2.42, 0.29, 0.12), (-2.20, 0.29, 0.12),
                (-2.16, 0.46, 0.35), (-2.07, 0.60, 0.4)]
        outline = [tuple(p) for p in bspline_closed(corners(ctrl), 5)]
        plate('RW_Endplate', outline, 0.010, y, M['livery'], bevel=0.0025,
              bend=lambda x, z, s=s: -s * 0.05 * max(0.0, (z - 0.80) / 0.14) ** 2.2)
        # endplate rain-light LED strip
        rbox('RW_LED', (0.012, 0.004, 0.16), (-2.507, s * (RW_HALF + 0.011), 0.80), M['rain'], bevel=0.001)
    # beam wing (two elements) between the endplate legs
    ys = np.linspace(-RW_HALF, RW_HALF, 31)
    box_uv(wing('BeamWing_1', ys, lambda y: (-2.19, 0.318 + 0.02 * (abs(y) / RW_HALF) ** 2, 0.14, 8), [M['carbon']], tc=0.1, camber=0.06))
    box_uv(wing('BeamWing_2', ys, lambda y: (-2.33, 0.352 + 0.025 * (abs(y) / RW_HALF) ** 2, 0.10, 30), [M['carbon']], tc=0.09, camber=0.05))
    # swan-neck pylons hooking over the mainplane
    for yy in (0.055, -0.055):
        pts = smooth_path([(-1.93, yy, 0.44), (-1.97, yy, 0.62), (-2.02, yy, 0.80), (-2.10, yy, 0.835), (-2.17, yy, 0.80)], 8)
        prof = [(u * 0.022, v * 0.006) for u, v in ellipse(1, 1, 14)]
        sweep('RW_SwanNeck', pts, prof, [M['carbon']], up=(1, 0, 0))
    # DRS actuator pod + link
    loft('RW_DRSPod', [np.column_stack([np.full(12, x), np.array(ellipse(w, h, 12))[:, 0], np.array(ellipse(w, h, 12))[:, 1] + z])
                       for x, w, h, z in ((-2.12, 0.004, 0.004, 0.79), (-2.15, 0.018, 0.016, 0.795), (-2.24, 0.02, 0.018, 0.80), (-2.30, 0.006, 0.008, 0.805))],
         [M['black']], sharp=60)
    strut('RW_DRSLink', (-2.28, 0, 0.80), (-2.33, 0, 0.845), 0.012, 0.005, M['ti'])


build_rear_wing()

# rain light at the tip of the crash structure
rbox('RainLight', (0.012, 0.075, 0.05), (BODY_END - 0.004, 0, 0.30), M['rain'], bevel=0.003)

# exhaust + wastegates, and the cooling outlet surround

def tube_x(name, x0, x1, y, z, r, m, wall=0.004, flare=1.0):
    prof = [(r - wall, x1), (r - wall, x0), (r, x0), (r * flare, x1)]
    bm = bmesh.new()
    segs = 32
    V = []
    for k in range(segs):
        a = 2 * math.pi * k / segs
        V.append([bm.verts.new((xx, y + rr * math.cos(a), z + rr * math.sin(a))) for rr, xx in prof])
    for k in range(segs):
        k2 = (k + 1) % segs
        for i in range(len(prof) - 1):
            bm.faces.new((V[k][i], V[k2][i], V[k2][i + 1], V[k][i + 1]))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    o = mesh_from_bm(name, bm, [m], sharp=50)
    box_uv(o)
    return o


tube_x('CoolingOutlet', -1.80, -1.93, 0, 0.465, 0.078, M['inlet'], wall=0.006)
tube_x('Exhaust', -1.78, -2.07, 0, 0.465, 0.054, M['exhaust'], wall=0.004, flare=1.04)
for s in (1, -1):
    tube_x('Wastegate', -1.82, -2.05, s * 0.07, 0.535, 0.017, M['exhaust'], wall=0.002)

# ---------------------------------------------------------------- halo + cockpit
def build_halo():
    half = [(0.175, 0.262, 0.600), (0.178, 0.268, 0.700), (0.215, 0.262, 0.790), (0.34, 0.245, 0.838), (0.55, 0.205, 0.848),
            (0.74, 0.130, 0.848), (0.86, 0.045, 0.846), (0.885, 0.0, 0.845)]
    full = half + [(x, -y, z) for x, y, z in reversed(half[:-1])]
    path = smooth_path(full, 6)
    prof = ellipse(0.021, 0.026, 18)
    halo = sweep('Halo', path, prof, [M['black']], up=(1, 0, 0))   # painted, as on 2024-25 cars
    # thin silver pinstripe along the underside/base line of the hoop
    sweep('HaloPinstripe', [(p[0], p[1] * 1.012, p[2] - 0.019) for p in path], ellipse(0.0022, 0.0022, 8), [M['polished']], up=(1, 0, 0))
    # carbon aero fairing on top of the hoop
    top = [p for p in path if p[2] > 0.80]
    fpath = [(p[0], p[1], p[2] + 0.02) for p in top]
    sweep('HaloFairing', fpath, [(u * 0.008, v * 0.03) for u, v in ellipse(1, 1, 16)], [M['carbon']], up=(0, 0, 1))
    # central pillar (blade section) down to the chassis
    pp = smooth_path([(0.885, 0.0, 0.845), (0.95, 0.0, 0.80), (1.04, 0.0, 0.71), (1.10, 0.0, 0.64)], 8)
    sweep('HaloPillar', pp, [(u * 0.02, v * 0.034) for u, v in ellipse(1, 1, 16)], [M['black']], up=(0, 1, 0))
    return halo


build_halo()

# cockpit rim padding following the opening, dropped onto the body surface
def build_cockpit():
    dg = bpy.context.evaluated_depsgraph_get()
    bvh = BVHTree.FromObject(body, dg)
    out = cockpit_outline()
    pts = []
    for x, y in out:
        # push slightly outward from the opening centre, then drop onto the surface
        cx = 0.6
        d = Vector((x - cx, y, 0))
        d = d.normalized() * 0.012 if d.length > 0 else d
        hit = bvh.ray_cast(Vector((x + d.x, y + d.y, 1.5)), Vector((0, 0, -1)), 3)
        z = hit[0].z if hit[0] else 0.66
        pts.append((x + d.x * 0.3, y + d.y * 0.3, z + 0.004))
    pts.append(pts[0])
    sweep('CockpitRim', pts, [(u * 0.014, v * 0.009) for u, v in ellipse(1, 1, 12)], [M['interior']], up=(0, 0, 1), cap=False)
    # headrest foam: U around the helmet
    hr = smooth_path([(0.70, 0.205, 0.645), (0.40, 0.212, 0.655), (0.24, 0.180, 0.665), (0.20, 0.0, 0.672),
                      (0.24, -0.180, 0.665), (0.40, -0.212, 0.655), (0.70, -0.205, 0.645)], 6)
    sweep('Headrest', hr, [(u * 0.03, v * 0.038) for u, v in rrect(1, 1, 0.3)], [M['foam']], up=(0, 0, 1))
    # steering wheel hint + display
    rbox('SteeringWheel', (0.03, 0.27, 0.11), (0.80, 0, 0.605), M['carbon'], rot=(0, math.radians(-25), 0), bevel=0.01)
    rbox('WheelDisplay', (0.004, 0.08, 0.045), (0.786, 0, 0.615), M['screen'], rot=(0, math.radians(-25), 0), bevel=0.002)


build_cockpit()

# helmet: shell, visor band, collar
def uv_sphere(name, radii, loc, m, keep=None, seg=48, rings_=24):
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=rings_, radius=1.0)
    if keep:
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if not keep(v.co)], context='VERTS')
    for v in bm.verts:
        v.co = Vector((v.co.x * radii[0], v.co.y * radii[1], v.co.z * radii[2])) + Vector(loc)
    return mesh_from_bm(name, bm, [m], sharp=0)


HELMET = (0.43, 0.0, 0.745)
uv_sphere('Helmet', (0.150, 0.128, 0.140), HELMET, M['livery'], keep=lambda c: c.z > -0.55)   # painted in the livery atlas
uv_sphere('Visor', (0.1515, 0.1295, 0.1415), HELMET, M['visor'], keep=lambda c: c.x > 0.42 and -0.12 < c.z < 0.42)
rbox('HANS', (0.12, 0.25, 0.035), (0.335, 0, 0.628), M['carbon'], bevel=0.014)

# ---------------------------------------------------------------- mirrors, cameras, antennas, pitots
for s in (1, -1):
    mx, my, mz = 0.93, s * 0.47, 0.668
    prof_m = [(0.036 * math.cos(a), 0.029 * math.sin(a)) for a in np.linspace(-math.pi / 2, math.pi / 2, 13)] + [(-0.034, 0.029), (-0.034, -0.029)]
    rings_m = []
    for yy in np.linspace(-0.088, 0.088, 13):
        k = (1 - (abs(yy) / 0.088) ** 6) ** 0.35 * 0.85 + 0.15            # rounded ends
        rings_m.append([(mx + u * (0.4 + 0.6 * k) if u > 0 else mx + u, my + yy, mz + v * k) for u, v in prof_m])
    loft('MirrorHousing', rings_m, [M['black']], sharp=50)
    rbox('MirrorGlass', (0.003, 0.15, 0.046), (mx - 0.0355, my, mz), M['glass'], bevel=0.003)
    ys_w = np.linspace(my - 0.085, my + 0.085, 7)
    box_uv(wing('MirrorWinglet', ys_w, lambda yy: (mx + 0.03, mz + 0.048, 0.06, 6), [M['carbon']], tc=0.09, camber=0.06))
    for dy in (-0.06, 0.06):
        strut('MirrorWingletStay', (mx + 0.005, my + dy, mz + 0.02), (mx + 0.0, my + dy, mz + 0.05), 0.02, 0.004, M['carbon'])
    strut('MirrorStalk', (0.95, s * body_halfwidth(0.95, 0.60) * 0.8, 0.60), (mx + 0.005, s * 0.40, mz - 0.01), 0.04, 0.009, M['carbon'])
    strut('MirrorStalk', (0.86, s * 0.47, 0.62), (mx - 0.01, s * 0.47, mz - 0.02), 0.035, 0.008, M['carbon'])
    # regulation camera housings on the chassis shoulders
    loft('CameraPod', [np.column_stack([np.full(10, x), np.array(ellipse(w, w, 10))[:, 0] + s * 0.12, np.array(ellipse(w, w, 10))[:, 1] + 0.60])
                       for x, w in ((1.58, 0.004), (1.56, 0.014), (1.50, 0.016), (1.44, 0.006))], [M['black']], sharp=60)
    uv_sphere('CameraLens', (0.006, 0.01, 0.01), (1.575, s * 0.12, 0.60), M['lens'])
    # pitot tubes under the nose tip
    strut('Pitot', (2.90, s * 0.03, 0.172), (3.06, s * 0.03, 0.172), 0.006, 0.006, M['ti'])
strut('Antenna', (1.22, 0, body_top(1.22) - 0.01), (1.20, 0, 0.74), 0.008, 0.004, M['black'])
strut('Antenna', (-1.0, 0.03, body_top(-1.0) - 0.02), (-1.02, 0.03, body_top(-1.0) + 0.06), 0.008, 0.004, M['black'])
strut('Pitot', (1.10, 0.0, 0.665), (1.22, 0, 0.690), 0.006, 0.006, M['ti'])

# ---------------------------------------------------------------- wheels
def tyre_profile(w):
    h = w / 2
    ctrl = [(0.236, h - 0.022), (0.250, h - 0.010), (0.285, h + 0.004), (0.325, h + 0.002), (0.352, h - 0.012),
            (0.3605, h - 0.045), (0.3615, 0.0), (0.3605, -h + 0.045), (0.352, -h + 0.012), (0.325, -h - 0.002),
            (0.285, -h - 0.004), (0.250, -h + 0.010), (0.236, -h + 0.022)]
    return bspline_open(ctrl, 5)


def sidewall_y(w, r):
    p = tyre_profile(w)
    p = p[: len(p) // 2]
    i = np.argsort(np.abs(p[:, 0] - r))[:2]
    return float(p[i, 1].mean())


def wheel(name, x, yc, w, side, front):
    hub = bpy.data.objects.new(name, None)
    hub.location = (x, yc, R)
    col.objects.link(hub)
    c = (x, yc, R)
    h = w / 2
    parts = [
        revolve('Tyre', tyre_profile(w), [M['rubber']], c, side),
        # rim barrel + outer flange
        revolve('Rim', [(0.200, -h + 0.02), (0.226, -h + 0.02), (0.228, h - 0.03), (0.240, h - 0.022), (0.242, h - 0.012),
                        (0.228, h - 0.010), (0.222, h - 0.02)], [M['rim']], c, side),
        # wheel cover: shallow dish, carbon, blue anodised retaining ring
        revolve('WheelCover', [(0.222, h - 0.016), (0.16, h - 0.022), (0.08, h - 0.026), (0.052, h - 0.026)], [M['cover']], c, side, sharp=0),
        revolve('WheelRing', [(0.064, h - 0.024), (0.052, h - 0.020), (0.052, h - 0.014), (0.064, h - 0.012), (0.064, h - 0.024)][::-1],
                [M['blue']], c, side, segs=48),
        # centre-lock nut: castellated look via 6 segments
        revolve('WheelNut', [(0.0, h + 0.004), (0.034, h + 0.004), (0.042, h - 0.004), (0.042, h - 0.030)], [M['polished']], c, side, segs=6, sharp=30),
        # brake-duct / rim deflector lip standing proud at the rim edge
        revolve('RimLip', [(0.2215, h - 0.018), (0.2235, h - 0.006), (0.2325, h - 0.004), (0.2345, h - 0.016)], [M['carbon']], c, side, segs=96),
    ]
    # sidewall lettering ring (conformal: follows the sidewall bulge, 0.6 mm proud)
    rs = np.linspace(0.262, 0.334, 6)
    prof = [(r, sidewall_y(w, r) + 0.0006) for r in rs]
    lab = revolve('TyreLabel', prof, [M['tyre_label']], c, side, segs=128)
    parts.append(lab)
    bpy.context.view_layer.update()
    for p in parts:
        mw = p.matrix_world.copy()
        p.parent = hub
        p.matrix_world = mw
    return hub


# ---------------------------------------------------------------- tyre label texture
def tyre_label_texture():
    """Pirelli-style sidewall layout without the trademark: the compound band runs as arcs round the outer
    shoulder, broken where the two big marks sit; small size/compound text sits between them. v=1 = outer."""
    W, H = 4096, 128
    img = np.zeros((H, W, 4), np.float32)
    u = (np.arange(W) + 0.5) / W
    gap = np.zeros(W)
    for c in (0.125, 0.625):                                  # big-mark positions (two per sidewall)
        gap = np.maximum(gap, smoothstep(0.13, 0.11, np.abs(((u - c + 0.5) % 1.0) - 0.5)))
    band = np.zeros((H, W))
    band[int(H * 0.05):int(H * 0.17)] = 1.0
    band *= (1 - gap)[None, :]
    img = over(img, rgba(band, (0.82, 0.82, 0.82), 0.95))
    seg = W // 4
    for k, (wd, hh, y0) in enumerate((('PK  SLICK', 0.50, 0.06), ('305/720 R18   C1', 0.24, 0.40),
                                      ('PK  SLICK', 0.50, 0.06), ('305/720 R18   C1', 0.24, 0.40))):
        m = text_mask(wd, int(H * hh), W=seg, spacing=1.25 if hh > 0.4 else 1.1, pad=0.0)
        r0 = int(H * y0)
        img[r0:r0 + m.shape[0], k * seg:(k + 1) * seg] = over(img[r0:r0 + m.shape[0], k * seg:(k + 1) * seg],
                                                              rgba(m, (0.78, 0.78, 0.78), 0.92))
    return save_img('tyre_label', img)


def wheel_cover_texture(W=512, H=256):
    """2022-style solid wheel cover in the livery: satin black disc, an electric-blue ring just inside the
    rim with a silver line outboard of it, and a deep-blue hub ring. v = rim (0) -> hub (1)."""
    v = (np.arange(H) + 0.5)[:, None] / H * np.ones((1, W))
    px = 1.0 / H
    rgb = np.broadcast_to(np.array((0.0045, 0.005, 0.006)), (H, W, 3)).copy()
    for lo, hi, colr in ((0.07, 0.15, BLUE), (0.84, 0.90, DEEP)):
        a = aa(v - lo, px) * aa(hi - v, px)
        rgb = mixc(rgb, colr, a)
    rgb = mixc(rgb, SILVER, aa(0.006 - np.abs(v - 0.045), px))
    return save_img('wheel_cover', to_srgb(rgb))


M['cover'] = principled('WheelCover', (0.01, 0.01, 0.012), 0.15, 0.48, 0.0, 0.05)      # satin, no coat
add_base(M['cover'], wheel_cover_texture())
M['tyre_label'] = principled('Tyre_Label', (0.8, 0.8, 0.8), 0.0, 0.7)
add_base(M['tyre_label'], tyre_label_texture(), alpha=True)

wheels = [wheel('Wheel_FL', XF, YF, WF, 1, True), wheel('Wheel_FR', XF, -YF, WF, -1, True),
          wheel('Wheel_RL', XR, YR, WR, 1, False), wheel('Wheel_RR', XR, -YR, WR, -1, False)]

# brake drums/ducts (static, inside the wheels) + front wheel-wake deflectors
for s in (1, -1):
    for x, yc, w, front in ((XF, YF, WF, True), (XR, YR, WR, False)):
        yin = yc - w / 2
        revolve('BrakeDrum', [(0.0, -0.10), (0.19, -0.10), (0.205, -0.07), (0.205, 0.03), (0.0, 0.03)], [M['carbon']],
                (x, s * (yin + 0.05), R), s, segs=48)
        # duct inlet facing forward (front) / vertical inlet (rear)
        if front:
            loft('BrakeDuctInlet', [np.column_stack([np.full(len(pr), xx), np.array(pr)[:, 0] + s * (yin - 0.035), np.array(pr)[:, 1] + 0.30])
                                    for xx, pr in ((x + 0.16, rrect(0.06, 0.09, 0.02)), (x + 0.14, rrect(0.07, 0.10, 0.025)),
                                                   (x + 0.02, rrect(0.07, 0.12, 0.025)))], [M['carbon']], sharp=50)
            rbox('BrakeDuctMouth', (0.004, 0.05, 0.075), (x + 0.162, s * (yin - 0.035), 0.30), M['inlet'], bevel=0.01)
            # wake deflector arc over the front of the tyre
            arc = [(x + (R + 0.03) * math.sin(math.radians(a)), s * (yin + 0.06), R + (R + 0.03) * math.cos(math.radians(a))) for a in np.linspace(18, 80, 14)]
            sweep('WheelDeflector', arc, [(u * 0.004, v * 0.055) for u, v in ellipse(1, 1, 12)], [M['carbon']], up=(0, 0, 1))
        else:
            plate('RearDuctWinglet', [(x + 0.30, 0.12), (x - 0.20, 0.12), (x - 0.25, 0.30), (x - 0.05, 0.58), (x + 0.20, 0.55), (x + 0.32, 0.35)],
                  0.008, s * (yin - 0.03), M['carbon'], bevel=0.002)
            for zz in (0.50, 0.55):
                box_uv(wing('RearDuctFin', np.linspace(s * (yin - 0.02), s * (yin + 0.10), 5) if s > 0 else np.linspace(s * (yin + 0.10), s * (yin - 0.02), 5),
                            lambda yy, zz=zz: (x + 0.12, zz, 0.12, 10), [M['carbon']], tc=0.08))

# ---------------------------------------------------------------- suspension
def suspension():
    ch, th = 0.06, 0.016
    for s in (1, -1):
        # front: double wishbone, pushrod, track rod
        uo, lo = (XF + 0.01, s * 0.74, 0.525), (XF, s * 0.77, 0.205)
        strut('FS_UpperFront', (XF + 0.24, s * 0.06, 0.48), uo, ch, th, M['carbon'])
        strut('FS_UpperRear', (XF - 0.30, s * 0.06, 0.49), uo, ch, th, M['carbon'])
        strut('FS_LowerFront', (XF + 0.32, s * 0.06, 0.24), lo, ch, th, M['carbon'])
        strut('FS_LowerRear', (XF - 0.30, s * 0.06, 0.22), lo, ch, th, M['carbon'])
        strut('FS_Pushrod', (XF - 0.02, s * 0.72, 0.23), (XF - 0.12, s * 0.10, 0.54), 0.04, 0.02, M['carbon'])
        strut('FS_TrackRod', (XF + 0.14, s * 0.06, 0.30), (XF + 0.13, s * 0.75, 0.29), 0.05, 0.014, M['carbon'])
        # rear: double wishbone, pullrod, toe link, driveshaft
        uo, lo = (XR + 0.01, s * 0.66, 0.50), (XR, s * 0.68, 0.18)
        strut('RS_UpperFront', (XR + 0.32, s * 0.05, 0.44), uo, ch, th, M['carbon'])
        strut('RS_UpperRear', (XR - 0.12, s * 0.05, 0.46), uo, ch, th, M['carbon'])
        strut('RS_LowerFront', (XR + 0.40, s * 0.05, 0.20), lo, ch, th, M['carbon'])
        strut('RS_LowerRear', (XR - 0.06, s * 0.05, 0.21), lo, ch, th, M['carbon'])
        strut('RS_Pullrod', (XR + 0.02, s * 0.64, 0.48), (XR + 0.26, s * 0.06, 0.22), 0.04, 0.02, M['carbon'])
        strut('RS_ToeLink', (XR - 0.16, s * 0.05, 0.31), (XR - 0.13, s * 0.66, 0.30), 0.05, 0.014, M['carbon'])
        rev = revolve('Driveshaft', [(0.0, 0.0), (0.028, 0.0), (0.028, 0.62), (0.0, 0.62)], [M['rubber']], (XR, s * 0.04, R), s, segs=20)


suspension()

# =============================================================== livery decals
dg_cache = {}


def bvh_of(obj):
    if obj.name not in dg_cache:
        dg_cache[obj.name] = BVHTree.FromObject(obj, bpy.context.evaluated_depsgraph_get())
    return dg_cache[obj.name]


def decal(name, targets, center, direction, up, w, h, m, res=None, offset=0.0012, flip_u=False):
    """Conformal decal: a (w x h) grid in the plane normal to `direction` is ray-cast onto the target
    surfaces and lifted `offset` along the surface normal. UVs 0..1 (u right, v up as seen along `direction`)."""
    d = Vector(direction).normalized()
    u = Vector(up)
    u = (u - d * u.dot(d)).normalized()
    r = d.cross(u)
    c = Vector(center)
    nx, ny = res or (max(8, int(w / 0.012)), max(4, int(h / 0.012)))
    bvhs = [bvh_of(t) for t in targets]
    bm = bmesh.new()
    uvl = bm.loops.layers.uv.new('UVMap')
    grid = {}
    for j in range(ny + 1):
        for i in range(nx + 1):
            p = c + r * ((i / nx - 0.5) * w) + u * ((j / ny - 0.5) * h) - d * 0.6
            best = None
            for b in bvhs:
                loc, nrm, idx, dist = b.ray_cast(p, d, 1.5)
                if loc is not None and (best is None or dist < best[2]):
                    best = (loc, nrm, dist)
            if best:
                n = best[1] if best[1].dot(d) < 0 else -best[1]
                grid[i, j] = bm.verts.new(best[0] + n * offset)
    for j in range(ny):
        for i in range(nx):
            q = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
            if all(k in grid for k in q):
                vs = [grid[k] for k in q]
                # reject faces that straddle a gap/step between surfaces
                if max((vs[a].co - vs[(a + 1) % 4].co).length for a in range(4)) > 3.5 * max(w / nx, h / ny):
                    continue
                f = bm.faces.new(vs)
                for lp, (ii, jj) in zip(f.loops, q):
                    uu = ii / nx
                    lp[uvl].uv = (1 - uu if flip_u else uu, jj / ny)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    for f in bm.faces:
        if f.normal.dot(d) > 0:
            f.normal_flip()
    return mesh_from_bm(name, bm, [m], sharp=0)


def decal_mat(name, img, rough=0.32, coat=1.0):
    m = principled(name, (1, 1, 1), 0.0, rough, coat, 0.03)
    add_base(m, img, alpha=True)
    return m


print('building livery ...')
LOGO = {k: load_logo(k) for k in ('jbhunt', 'georgia-tech', 'georgia-tech-wordmark', 'missouri-st')}
LOGO_MAT = {k: decal_mat('Decal_Logo_' + k, v[0]) for k, v in LOGO.items() if v[0]}


def logo_decal(key, targets, center, direction, up, height=None, width=None, **kw):
    if key not in LOGO_MAT:
        return None
    asp = LOGO[key][1]
    if width is None:
        width = height * asp
    if height is None:
        height = width / asp
    return decal('Decal_' + key, targets, center, direction, up, width, height, LOGO_MAT[key], **kw)


_type_cache = {}


def type_texture(name, text, color, H=256, spacing=1.05, shear=0.0):
    key = (text, tuple(color), spacing, shear)
    if key in _type_cache:
        return _type_cache[key]
    _type_cache[key] = _type_texture(name, text, color, H, spacing, shear)
    return _type_cache[key]


_mat_cache = {}


def _type_texture(name, text, color, H=256, spacing=1.05, shear=0.0):
    m = text_mask(text, H, spacing=spacing, shear=shear, pad=0.1)
    return save_img(name, pad_pow2(rgba(m, color))), m.shape[1] / m.shape[0]


WHITE = (0.85, 0.85, 0.85)


def type_decal(key, text, color, targets, center, direction, up, height, spacing=1.05, shear=0.0, **kw):
    img, asp = type_texture('type_' + key, text, color, spacing=spacing, shear=shear)
    # the texture is padded to a power of two: widen the decal to match the padded canvas
    W, H = img.size          # padded canvas; `height` is the text-box height (256 px)
    if img.name not in _mat_cache:
        _mat_cache[img.name] = decal_mat('Decal_' + key, img)
    return decal('Decal_' + key, targets, center, direction, up, height * W / 256, height * H / 256, _mat_cache[img.name], **kw)


def gradient_texture(name, W, H, fn):
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    u, v = (xx + 0.5) / W, 1 - (yy + 0.5) / H
    rgb, a = fn(u, v)
    out = np.concatenate([to_srgb(rgb), a[..., None]], -1)
    return save_img(name, out)


def aa(x, px):
    return np.clip(x / px + 0.5, 0, 1)


def livery():
    side_views = ((1, (0, -1, 0)), (-1, (0, 1, 0)))
    # (the hero sweep, pinstripe and panel lines are painted in the livery texture set; logos are decals)
    # 2) J.B. Hunt title logo on the sidepods
    for s, d in side_views:
        logo_decal('jbhunt', [pods[0 if s > 0 else 1]], (-0.10, s * 0.9, 0.37), d, (0, 0, 1), width=0.70)
    # 3) Missouri S&T on the nose sides and front-wing endplates
    for s, d in side_views:
        logo_decal('missouri-st', [body], (2.33, s * 0.4, 0.33), d, (0.25, 0, 1), width=0.22)
        eps = [o for o in col.objects if o.name.startswith('FW_Endplate') and (o.matrix_world @ o.data.vertices[0].co).y * s > 0]
        logo_decal('missouri-st', eps, (2.66, s * 1.2, 0.165), d, (0, 0, 1), width=0.28)
    # 4) Georgia Tech on the engine cover flanks and rear-wing endplates (inner faces)
    for s, d in side_views:
        logo_decal('georgia-tech', [body, airbox], (-0.52, s * 0.6, 0.62), d, (0.35, 0, 1), height=0.075)
        eps = [o for o in col.objects if o.name.startswith('RW_Endplate') and (o.matrix_world @ o.data.vertices[0].co).y * s > 0]
        # sized to the endplate (0.52 m x 0.68 m) so both marks read at hero scale
        logo_decal('jbhunt', eps, (-2.285, s * 1.0, 0.845), d, (0, 0, 1), width=0.36)
        logo_decal('georgia-tech-wordmark', eps, (-2.285, s * 1.0, 0.705), d, (0, 0, 1), width=0.38)
        type_decal('ep_no_%d' % s, NUMBER, WHITE, eps, (-2.31, s * 1.0, 0.52), d, (0, 0, 1), 0.16, shear=0.18)
    # 5) J.B. Hunt across the DRS flap's upper face, centred on the flap as built and sized to its chord
    #    (a fixed centre missed the flap once the rear wing moved, so the decal silently came out empty).
    #    Projected close to the face normal (down and rearward); upright from the hero camera ahead of the car.
    flap = [o for o in col.objects if o.name.startswith('RW_DRSFlap')]
    if flap:
        pts = [o.matrix_world @ Vector(c) for o in flap for c in o.bound_box]
        lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
        hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
        width = min(0.62, 0.72 * (hi.x - lo.x) * LOGO['jbhunt'][1], 0.6 * (hi.y - lo.y))
        dec = logo_decal('jbhunt', flap, ((lo.x + hi.x) / 2, 0, (lo.z + hi.z) / 2), (-0.35, 0, -1), (-1, 0, 0), width=width)
        print('DRS flap decal: chord x %.3f m, span %.3f m, logo %.3f m wide, %d faces' % (
            hi.x - lo.x, hi.y - lo.y, width, len(dec.data.polygons) if dec else 0))
    # 6) driver name on the cockpit flanks, number on the nose, PK + number on the fin
    for s, d in side_views:
        type_decal('name_%d' % s, 'P. KONDAPANENI', WHITE, [airbox], (-0.02, s * 0.5, 0.878), d, (-0.05, 0, 1), 0.032, spacing=1.12)
        # on the slim fin, sitting in the band just above the engine cover
        type_decal('fin_pk_%d' % s, 'PK', WHITE, [fin], (-0.62, s * 0.3, float(BODY(-0.62)['zr']) + 0.038), d, (0.1, 0, 1), 0.05, shear=0.18)
        type_decal('fin_no_%d' % s, NUMBER, WHITE, [fin], (-0.92, s * 0.3, float(BODY(-0.92)['zr']) + 0.034), d, (0.1, 0, 1), 0.046, shear=0.18)
    # race number on the nose top, projected straight down; upright when seen from ahead of the car
    type_decal('nose_no', NUMBER, WHITE, [body], (1.95, 0, float(BODY(1.95)['zr']) + 0.2), (0, 0, -1), (-1, 0, 0), 0.16, shear=0.18)


print('painting livery atlas (concept %s) ...' % LIVERY)
LIVERY_OBJS = [o for o in col.objects if o.type == 'MESH' and any(m == M['livery'] for m in o.data.materials)]
for o in LIVERY_OBJS:                       # stable part names for the painter
    if o.name.startswith('RW_Endplate'):
        o.name = 'RW_Endplate_' + ('L' if (o.matrix_world @ o.data.vertices[0].co).y > 0 else 'R')
LIVERY_OBJS = [o for o in col.objects if o.type == 'MESH' and any(m == M['livery'] for m in o.data.materials)]
build_livery_atlas(LIVERY_OBJS)
setup_livery(livery_atlas(LIVERY_OBJS, LIVERY))
dg_cache.clear()
livery()

# ---------------------------------------------------------------- rake: sprung mass pitched nose-down about the front contact patch
RAKE = math.radians(0.45)          # ~28 mm higher at the rear axle, typical ground-effect-era rake
UNSPRUNG = ('Wheel_', 'BrakeDrum', 'BrakeDuct', 'WheelDeflector', 'RearDuct')
pivot = Vector((XF, 0, 0))
rk = Matrix.Translation(pivot) @ Matrix.Rotation(RAKE, 4, 'Y') @ Matrix.Translation(-pivot)
for o in list(col.objects):
    if o.parent is None and not o.name.startswith(UNSPRUNG):
        o.matrix_world = rk @ o.matrix_world

# ---------------------------------------------------------------- UVs for every lofted/strut part lacking them
for o in col.objects:
    if o.type == 'MESH' and not o.data.uv_layers:
        box_uv(o)

# =============================================================== root + export
root = bpy.data.objects.new('Car', None)
col.objects.link(root)
bpy.context.view_layer.update()
for o in list(col.objects):
    if o is not root and o.parent is None:
        mw = o.matrix_world.copy()
        o.parent = root
        o.matrix_world = mw

stats = sum(len(o.data.polygons) for o in col.objects if o.type == 'MESH')
print('objects', len(col.objects), 'polys', stats)

# ---------------------------------------------------------------- ray-traced ambient occlusion (livery)
# Cycles bakes ambient occlusion (ray traced: 128 samples, 0.35 m reach, over a ground plane) onto the
# livery atlas, and the exporter writes it as the material's occlusionTexture. three.js applies it to
# all the studio light the body gets from the environment and the fill, so panel gaps, the sidepod
# undercut, the cockpit and the floor edge darken the way they do in a path-traced render, on every
# device, at no runtime cost. (Skip with --no-ao.)
if '--no-ao' not in ARGS and LIVERY_OBJS:
    import time
    t0 = time.time()
    use_gpu()
    scene.cycles.samples = 128
    world_prev = scene.world
    scene.world = bpy.data.worlds.new('AOBake')
    scene.world.light_settings.distance = 0.35
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0.3, 0, 0.0))
    ground = bpy.context.active_object
    ground.scale = (12, 8, 1)
    AO_N = 1024
    raw = bpy.data.images.new('livery_ao_raw', AO_N, AO_N, alpha=False, float_buffer=True)
    ml = M['livery']
    nt = ml.node_tree
    bake_node = nt.nodes.new('ShaderNodeTexImage')
    bake_node.image = raw
    nt.nodes.active = bake_node
    prev_uv = {}
    for o in LIVERY_OBJS:
        prev_uv[o.name] = o.data.uv_layers.active.name
        o.data.uv_layers.active = o.data.uv_layers['Livery']
    bpy.ops.object.select_all(action='DESELECT')
    for o in LIVERY_OBJS:
        o.select_set(True)
    bpy.context.view_layer.objects.active = LIVERY_OBJS[0]
    decals = [o for o in col.objects if o.name.startswith('Decal') and not o.hide_render]
    for o in decals:
        o.hide_render = True                              # logos float 1.2 mm up: they'd print into the AO
    # Cycles bakes into the active image node of EVERY material on the selected objects. The body parts
    # also carry carbon/paint slots whose active nodes are shared tiling textures (carbon base colour,
    # paint flake), so give each of those a throwaway target instead of letting AO paint over them.
    scratch, restore = bpy.data.images.new('bake_scratch', 8, 8), []
    for mat in {ms.material for o in LIVERY_OBJS for ms in o.material_slots if ms.material and ms.material != ml}:
        dn = mat.node_tree.nodes.new('ShaderNodeTexImage'); dn.image = scratch
        restore.append((mat, mat.node_tree.nodes.active, dn)); mat.node_tree.nodes.active = dn
    bpy.ops.object.bake(type='AO', margin=6, use_clear=True)
    for mat, prev, dn in restore:
        mat.node_tree.nodes.remove(dn)
        if prev: mat.node_tree.nodes.active = prev
    bpy.data.images.remove(scratch)
    for o in decals:
        o.hide_render = False
    for o in LIVERY_OBJS:
        o.data.uv_layers.active = o.data.uv_layers[prev_uv[o.name]]
    bpy.data.objects.remove(ground)
    scene.world = world_prev
    a = np.empty(AO_N * AO_N * 4, np.float32)
    raw.pixels.foreach_get(a)
    ao = a.reshape(AO_N, AO_N, 4)[..., 0]
    ao = np.clip(ao, 0, 1) ** 0.85                        # keep open panels near 1; crevices still read
    aoimg = save_img('livery_ao', np.stack([ao, ao, ao], -1)[::-1], True)
    bake_node.image = aoimg
    uvn = nt.nodes.new('ShaderNodeUVMap')
    uvn.uv_map = 'Livery'
    nt.links.new(uvn.outputs['UV'], bake_node.inputs['Vector'])
    sep = nt.nodes.new('ShaderNodeSeparateColor')
    nt.links.new(bake_node.outputs['Color'], sep.inputs['Color'])
    grp = bpy.data.node_groups.get('glTF Material Output')
    if grp is None:
        grp = bpy.data.node_groups.new('glTF Material Output', 'ShaderNodeTree')
        grp.interface.new_socket(name='Occlusion', in_out='INPUT', socket_type='NodeSocketFloat')
    gnode = nt.nodes.new('ShaderNodeGroup')
    gnode.node_tree = grp
    nt.links.new(sep.outputs['Red'], gnode.inputs['Occlusion'])
    bpy.data.images.remove(raw)
    print('baked livery AO (%.0fs): %d objects, mean %.2f' % (time.time() - t0, len(LIVERY_OBJS), float(ao.mean())))

if DO_EXPORT:
    kw = dict(filepath=OUT, export_format='GLB', export_apply=True, export_yup=True,
              export_draco_mesh_compression_enable=True, export_draco_mesh_compression_level=6,
              export_draco_position_quantization=13, export_draco_normal_quantization=11,
              export_draco_texcoord_quantization=14, export_image_format='WEBP', export_image_quality=88)
    bpy.ops.export_scene.gltf(**kw)
    print('Exported', OUT, os.path.getsize(OUT) // 1024, 'KB')

    # Phone variant: the car renders ~390 CSS px wide at DPR <= 1.25 there, so dense surfaces are
    # decimated (decals untouched, they must stay conformal) and Draco precision matches that scale.
    OUT_M = OUT.replace('car.glb', 'car_mobile.glb')
    dg = bpy.context.evaluated_depsgraph_get()
    for o in [o for o in col.objects if o.type == 'MESH']:
        # evaluated count: most density comes from subdivision applied at export
        if o.name.startswith('Decal') or len(o.evaluated_get(dg).data.polygons) < 1200:
            continue
        dm = o.modifiers.new('PhoneDecimate', 'DECIMATE')
        dm.decimate_type = 'COLLAPSE'; dm.ratio = 0.45; dm.use_collapse_triangulate = True
    for nm, sz in (('livery_basecolor', 1024), ('livery_normal', 1024), ('livery_orm', 512), ('livery_ao', 512),
                   ('carbon_basecolor', 512), ('carbon_normal', 512), ('carbon_orm', 512)):
        im = bpy.data.images.get(nm)
        if im and im.size[0] > sz:
            im.scale(sz, sz)                     # phone build: the car is ~390 CSS px wide at DPR <= 1.25
    # J.B. Hunt reads as clean white letters on phones instead of a grey hatched smear (jbhunt_wordmark)
    if LOGO['jbhunt'][0] and 'jbhunt' in LOGO_MAT:
        wm = jbhunt_wordmark(LOGO['jbhunt'][0])
        for n in LOGO_MAT['jbhunt'].node_tree.nodes:
            if n.type == 'TEX_IMAGE' and n.image == LOGO['jbhunt'][0]:
                n.image = wm
    kw_m = dict(kw, filepath=OUT_M, export_draco_position_quantization=12, export_draco_normal_quantization=10,
                export_draco_texcoord_quantization=12, export_draco_mesh_compression_level=7)
    bpy.ops.export_scene.gltf(**kw_m)
    print('Exported', OUT_M, os.path.getsize(OUT_M) // 1024, 'KB')
    for o in col.objects:                        # leave the scene as it was for the preview renders
        if o.type == 'MESH' and 'PhoneDecimate' in o.modifiers:
            o.modifiers.remove(o.modifiers['PhoneDecimate'])
    if 'jbhunt' in LOGO_MAT:
        for n in LOGO_MAT['jbhunt'].node_tree.nodes:
            if n.type == 'TEX_IMAGE' and n.image and n.image.name == 'logo_jbhunt_wordmark':
                n.image = LOGO['jbhunt'][0]

# =============================================================== contact shadow bake
if DO_BAKE:
    import time
    t0 = time.time()
    use_gpu()
    scene.cycles.samples = 256
    world = bpy.data.worlds.new('BakeW')
    scene.world = world
    world.light_settings.distance = 0.55
    SW, SH, CX = 7.2, 3.2, 0.3
    bpy.ops.mesh.primitive_plane_add(size=1, location=(CX, 0, 0.0005))
    pl = bpy.context.active_object
    pl.scale = (SW, SH, 1)
    bpy.ops.object.transform_apply(scale=True)
    img = bpy.data.images.new('shadow_bake', 1024, 512, alpha=False, float_buffer=True)
    pm = principled('ShadowBake', (0.8, 0.8, 0.8))
    tn = pm.node_tree.nodes.new('ShaderNodeTexImage')
    tn.image = img
    pm.node_tree.nodes.active = tn
    pl.data.materials.append(pm)
    bpy.ops.object.select_all(action='DESELECT')
    pl.select_set(True)
    bpy.context.view_layer.objects.active = pl
    bpy.ops.object.bake(type='AO', margin=2)
    a = np.empty(1024 * 512 * 4, np.float32)
    img.pixels.foreach_get(a)
    ao = a.reshape(512, 1024, 4)[::-1, :, 0]
    sh = np.clip(1 - ao, 0, 1)
    soft = blur(np.pad(sh, 32, mode='constant'), 10)[32:-32, 32:-32]
    sh = np.clip(0.75 * sh + 0.6 * soft, 0, 1) ** 1.1
    # fade the plane borders to zero
    yy, xx = np.mgrid[0:512, 0:1024]
    edge = np.minimum(np.minimum(xx, 1023 - xx) / 60, np.minimum(yy, 511 - yy) / 40).clip(0, 1)
    sh *= edge
    out = bpy.data.images.new('shadow', 1024, 512, alpha=False)
    rgba_ = np.stack([sh, sh, sh, np.ones_like(sh)], -1)[::-1]
    out.pixels.foreach_set(np.ascontiguousarray(rgba_, np.float32).ravel())
    out.filepath_raw = os.path.join(TEX, 'shadow.webp')
    out.file_format = 'WEBP'
    out.save()
    bpy.data.objects.remove(pl)
    print('baked contact shadow (%.0fs): plane %.1f x %.1f m centred at x=%.2f' % (time.time() - t0, SW, SH, CX))

# =============================================================== preview renders
if VIEWS:
    use_gpu()
    scene.cycles.samples = SAMPLES
    scene.view_settings.view_transform = 'AgX'
    try:
        scene.view_settings.look = 'AgX - Medium High Contrast'
    except Exception:
        pass
    world = bpy.data.worlds.new('Studio')
    scene.world = world
    world.use_nodes = True
    world.node_tree.nodes['Background'].inputs['Color'].default_value = (0.004, 0.0045, 0.006, 1)
    # seamless cove: floor sweeping up into a back wall
    bm = bmesh.new()
    prof = [(-30, 0.0)] + [(8 + 3 * math.sin(a), 3 - 3 * math.cos(a)) for a in np.linspace(0, math.pi / 2, 12)] + [(11, 12)]
    L = [[bm.verts.new((x, -12 + 24 * k / 1, z)) for x, z in prof] for k in (0, 1)]
    for i in range(len(prof) - 1):
        bm.faces.new((L[0][i], L[0][i + 1], L[1][i + 1], L[1][i]))
    cove = mesh_from_bm('Cove', bm, [principled('Studio_Floor', (0.006, 0.0065, 0.008), 0.0, 0.5, 0.35, 0.2)], sharp=0)
    cove.rotation_euler = (0, 0, math.radians(200))

    def area(name, loc, size, energy, color=(1, 1, 1), shape='RECTANGLE', aim=(0.3, 0, 0.3)):
        L_ = bpy.data.lights.new(name, 'AREA')
        L_.shape = shape
        L_.size, L_.size_y = size
        L_.energy = energy
        L_.color = color
        o = bpy.data.objects.new(name, L_)
        o.location = loc
        o.rotation_euler = (Vector(aim) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
        col.objects.link(o)
        return o
    # Car-launch studio: one huge soft overhead diffusion box (broad roll-off), two thin hard strips for crisp
    # specular lines, big dim side fills so the satin black keeps its form, a cool blue kicker from behind.
    area('Softbox', (0.3, 0, 4.6), (10.0, 4.5), 1400)
    area('StripL', (0.3, 3.0, 3.4), (9.0, 0.10), 260, aim=(0.3, 0.8, 0))
    area('StripR', (0.3, -3.0, 3.4), (9.0, 0.10), 200, aim=(0.3, -0.8, 0))
    area('FillL', (0.3, 6.0, 1.3), (8.0, 2.5), 260, (0.92, 0.95, 1.0), aim=(0.3, 0, 0.4))
    area('FillR', (0.3, -6.0, 1.3), (8.0, 2.5), 180, (0.92, 0.95, 1.0), aim=(0.3, 0, 0.4))
    area('Kicker', (-5.0, -3.5, 1.4), (0.8, 4.0), 700, BLUE, aim=(0, 0, 0.4))
    area('Front', (7.0, 1.0, 1.2), (3.0, 3.0), 120, (1.0, 1.0, 1.0), aim=(0.3, 0, 0.4))
    cam = bpy.data.objects.new('Cam', bpy.data.cameras.new('Cam'))
    col.objects.link(cam)
    scene.camera = cam
    scene.render.resolution_x, scene.render.resolution_y = 1400, 788
    CAMS = {  # location, target, lens
        '3q': ((5.6, 4.4, 1.7), (0.35, 0, 0.35), 50),
        'side': ((0.3, 9.5, 0.55), (0.3, 0, 0.42), 50),
        'nose': ((3.9, 1.6, 1.05), (1.6, 0, 0.45), 45),
        'front': ((8.0, 0.0, 0.62), (0.3, 0, 0.45), 60),
        'rear': ((-5.5, -3.6, 1.9), (-0.6, 0, 0.45), 50),
        'top': ((0.3, 0.0, 12.0), (0.3, 0, 0), 50),
        'detail': ((-1.2, 2.2, 1.6), (0.3, 0.3, 0.6), 50),
        'inlet': ((2.3, 1.9, 0.95), (0.7, 0.5, 0.48), 55),
        'endplate': ((-2.1, 2.6, 0.95), (-2.25, 0.48, 0.72), 50),
        'nosetop': ((3.4, 0.9, 1.5), (1.9, 0, 0.45), 50),
        'hero': ((-3.4, 5.6, 1.35), (0.2, 0, 0.42), 45),
    }
    for v in VIEWS:
        loc, tgt, lens = CAMS[v]
        cam.location = loc
        cam.data.lens = lens
        cam.rotation_euler = (Vector(tgt) - Vector(loc)).to_track_quat('-Z', 'Y').to_euler()
        scene.render.filepath = os.path.join(ROOT, 'blender', 'preview_%s.png' % v)
        bpy.ops.render.render(write_still=True)
        print('rendered', v)
