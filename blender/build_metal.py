"""
Builds the anodised brushed-aluminium material used by metal.js.

Run from the repo root (Blender 5.2, headless):
    blender --background --python blender/build_metal.py
    blender --background --python blender/build_metal.py -- --only surface,fastener,env,reference

Stages
  surface    Tileable brush height field (numpy inside Blender), converted to a tangent-space
             normal map, roughness map and albedo/tint map at 2048^2 and 1024^2.
             Brush direction is +X (image rows). Normals are OpenGL convention (+Y = image up).
  fastener   Cycles render of a countersunk Torx titanium bolt head: normal (+alpha) and
             albedo (+AO) sprites at 128^2.
  env        Cycles equirect render of the studio (overhead softbox, strip lights, blue kicker
             below) as seen from the plate: a sharp 1024x512 map and a pre-blurred 256x128 map.
             Radiance is stored Reinhard-encoded: e = (c/(1+c))^(1/2.2); decode c = x/(1-x), x = e^2.2.
  reference  Cycles beauty shot of a 600x300 px plate using the maps above, in the same studio
             -> blender/metal_reference.png (not shipped).

Cycles runs on the Metal GPU when available, with OpenImageDenoise on beauty renders.
Scale convention: 1 Blender unit = 100 CSS px. Screen space maps to Blender as
x -> X, screen-up -> Z, toward-viewer -> -Y.
"""
import bpy, bmesh, math, os, sys, subprocess, tempfile
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'assets', 'metal')
WORK = os.path.join(tempfile.gettempdir(), 'pk_metal_build')
os.makedirs(OUT, exist_ok=True)
os.makedirs(WORK, exist_ok=True)

argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
ONLY = set(argv[argv.index('--only') + 1].split(',')) if '--only' in argv else {'surface', 'fastener', 'env', 'reference'}
CWEBP = '/opt/homebrew/bin/cwebp'
rng = np.random.default_rng(20260922)


# ---------------------------------------------------------------- io helpers
def save_png(path, arr):
    """arr: HxW or HxWxC float in 0..1, top row first. Written as 8-bit, no colour transform."""
    a = np.asarray(arr, dtype=np.float32)
    if a.ndim == 2:
        a = a[..., None]
    h, w, c = a.shape
    rgba = np.ones((h, w, 4), np.float32)
    if c == 1:
        rgba[..., :3] = a
    else:
        rgba[..., :c] = a
    img = bpy.data.images.new('io', w, h, alpha=True, float_buffer=False)
    img.colorspace_settings.name = 'Non-Color'
    img.pixels.foreach_set(np.clip(np.flipud(rgba), 0, 1).ravel())
    img.filepath_raw = path
    img.file_format = 'PNG'
    img.save()
    bpy.data.images.remove(img)


def load_pixels(path):
    img = bpy.data.images.load(path, check_existing=False)
    w, h = img.size
    px = np.empty(w * h * 4, np.float32)
    img.pixels.foreach_get(px)
    bpy.data.images.remove(img)
    return np.flipud(px.reshape(h, w, 4))


def webp(png, name, lossless, q=90):
    dst = os.path.join(OUT, name)
    args = [CWEBP, '-quiet', '-mt', '-exact', '-metadata', 'none']
    args += ['-lossless', '-z', '9'] if lossless else ['-q', str(q), '-m', '6', '-sharp_yuv', '-af']
    subprocess.run(args + [png, '-o', dst], check=True)
    print(f'  {name}: {os.path.getsize(dst) / 1024:.0f} KB')
    return dst


def tile_preview(arr, name, crop=None):
    """2x2 tiling for seam inspection (written to WORK, not shipped)."""
    t = np.tile(arr, (2, 2, 1) if arr.ndim == 3 else (2, 2))
    if crop:
        n = arr.shape[0]
        t = t[n - crop:n + crop, n - crop:n + crop]
    save_png(os.path.join(WORK, name), t)


# ---------------------------------------------------------------- cycles setup
def reset_scene():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = 'CYCLES'
    prefs = bpy.context.preferences.addons['cycles'].preferences
    gpu = False
    try:
        prefs.compute_device_type = 'METAL'
        prefs.get_devices()
        for d in prefs.devices:
            d.use = d.type == 'METAL'
            gpu = gpu or d.use
    except Exception as e:
        print('  Metal unavailable:', e)
    sc.cycles.device = 'GPU' if gpu else 'CPU'
    print('  Cycles device:', sc.cycles.device)
    sc.view_settings.view_transform = 'Standard'
    sc.view_settings.look = 'None'
    sc.render.film_transparent = False
    sc.cycles.use_adaptive_sampling = True
    return sc


def node_mat(name):
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:
        pass
    nt = m.node_tree
    nt.nodes.clear()
    out = nt.nodes.new('ShaderNodeOutputMaterial')
    return m, nt, out


def emit_mat(name, color, strength):
    m, nt, out = node_mat(name)
    e = nt.nodes.new('ShaderNodeEmission')
    e.inputs['Color'].default_value = (*color, 1)
    e.inputs['Strength'].default_value = strength
    nt.links.new(e.outputs[0], out.inputs['Surface'])
    return m, nt, e


def mesh_obj(name, verts, faces, mat=None):
    me = bpy.data.meshes.new(name)
    me.from_pydata(verts, [], faces)
    me.update()
    ob = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(ob)
    if mat:
        me.materials.append(mat)
    return ob


# ================================================================ 1. surface
def spectral(n, shape):
    """Periodic Gaussian field (unit std) = white noise filtered by shape(kx, ky) in cycles/texel."""
    f = np.fft.rfft2(rng.standard_normal((n, n)).astype(np.float32))
    ky = np.fft.fftfreq(n)[:, None]
    kx = np.fft.rfftfreq(n)[None, :]
    f *= shape(kx, ky)
    out = np.fft.irfft2(f, s=(n, n)).astype(np.float32)
    return out / (out.std() + 1e-12)


def warp_rows(h, d):
    """Sample h at (y + d(x,y), x) with wrap and linear interpolation along y (brush waviness)."""
    n = h.shape[0]
    yy = np.arange(n, dtype=np.float32)[:, None] + d
    y0 = np.floor(yy).astype(np.int64)
    t = yy - y0
    xs = np.arange(n)[None, :]
    return h[y0 % n, xs] * (1 - t) + h[(y0 + 1) % n, xs] * t


def splat_scratch(h, n, x0, y0, length, angle, depth, width, bow):
    """Stray scratch: tapered V groove along a slightly curved, slightly tilted line (wraps)."""
    xs = np.arange(0, length, 0.5, dtype=np.float32)
    u = xs / length
    yc = y0 + np.tan(angle) * xs + bow * (4 * u * (1 - u)) + 0.35 * np.sin(xs / 37.0 + x0)
    env = np.sin(np.pi * u) ** 0.6 * (0.7 + 0.3 * np.sin(xs / 91.0 + y0))
    xi = np.floor(x0 + xs).astype(np.int64) % n
    for dy in range(-3, 4):
        yi = np.floor(yc).astype(np.int64) + dy
        dist = yi + 0.5 - yc
        prof = np.exp(-(dist / width) ** 2)
        # a little burr on each side of the cut
        prof -= 0.18 * np.exp(-((np.abs(dist) - 1.6 * width) / (0.6 * width)) ** 2)
        np.add.at(h, (yi % n, xi), -0.5 * depth * env * prof)


def splat_pits(h, n, count):
    cx = rng.uniform(0, n, count)
    cy = rng.uniform(0, n, count)
    r = rng.uniform(0.5, 1.6, count)
    dep = rng.lognormal(0, 0.5, count) * 0.9
    ix0, iy0 = np.floor(cx).astype(np.int64), np.floor(cy).astype(np.int64)
    for oy in range(-3, 4):
        for ox in range(-3, 4):
            px, py = ix0 + ox, iy0 + oy
            d2 = ((px + 0.5 - cx) ** 2 + (py + 0.5 - cy) ** 2) / r ** 2
            np.add.at(h, (py % n, px % n), -dep * np.exp(-d2))


def normals_from_height(h, slope_std_y):
    """OpenGL tangent-space normals. Height scaled so the across-brush slope has the given std."""
    gx = (np.roll(h, -1, 1) - np.roll(h, 1, 1)) * 0.5
    gy = (np.roll(h, -1, 0) - np.roll(h, 1, 0)) * 0.5      # image-down derivative
    k = slope_std_y / (gy.std() + 1e-12)
    nx, ny, nz = -gx * k, gy * k, np.ones_like(h)          # +Y up => n.y = +dh/dy_down
    inv = 1 / np.sqrt(nx * nx + ny * ny + 1)
    return np.stack([nx * inv, ny * inv, nz * inv], -1), k


def box_down(a):
    return 0.25 * (a[0::2, 0::2] + a[1::2, 0::2] + a[0::2, 1::2] + a[1::2, 1::2])


def build_surface():
    print('[surface]')
    n = 2048
    hp = lambda ky, p: 1 - np.exp(-(ky * p) ** 2)            # kills slow variation across the brush
    lp = lambda ky: np.exp(-(ky / 0.45) ** 2)                 # soft roll-off before Nyquist

    # Fine brush grooves: three populations of different lengths along +X.
    short = spectral(n, lambda kx, ky: np.exp(-(kx * 70) ** 2) * hp(ky, 5) * lp(ky))
    mid = spectral(n, lambda kx, ky: np.exp(-(kx * 320) ** 2) * hp(ky, 8) * lp(ky))
    long_ = spectral(n, lambda kx, ky: np.exp(-(kx * 1100) ** 2) * hp(ky, 9) * np.exp(-(ky / 0.34) ** 2))
    g = 0.45 * short + 0.55 * mid + 0.5 * long_
    g = np.sign(g) * np.abs(g) ** 1.35                        # a few deeper cuts, many shallow

    # Brush pressure: bands across the sheet where the grain is stronger or weaker.
    band = spectral(n, lambda kx, ky: np.exp(-((kx * 700) ** 2 + (ky * 90) ** 2)))
    g *= 1 + 0.32 * band

    # Waviness: the abrasive never tracks perfectly straight.
    wav = spectral(n, lambda kx, ky: np.exp(-((kx * 260) ** 2 + (ky * 160) ** 2)))
    h = warp_rows(g, 1.2 * wav).astype(np.float32)
    h /= h.std()

    # Occasional deeper stray scratches at small angles.
    for i in range(70):
        deep = i < 12
        splat_scratch(h, n,
                      x0=rng.uniform(0, n), y0=rng.uniform(0, n),
                      length=rng.uniform(300, 1500) if deep else rng.uniform(80, 700),
                      angle=np.radians(rng.normal(0, 2.2 if not deep else 1.2)),
                      depth=rng.uniform(3.0, 5.0) if deep else rng.uniform(1.2, 2.6),
                      width=rng.uniform(0.6, 1.1),
                      bow=rng.normal(0, 3))
    splat_pits(h, n, 2600)

    # Very low-frequency sheet undulation (slope std ~ 0.004 in the grain's height units later).
    und = spectral(n, lambda kx, ky: np.exp(-((kx ** 2 + ky ** 2) * 700 ** 2)))

    nrm_hi, k = normals_from_height(h, slope_std_y=0.16)
    # Add undulation after scaling so its slope is set in absolute terms.
    ug_x = (np.roll(und, -1, 1) - np.roll(und, 1, 1)) * 0.5
    ug_y = (np.roll(und, -1, 0) - np.roll(und, 1, 0)) * 0.5
    us = 0.012 / (np.sqrt((ug_x ** 2 + ug_y ** 2).mean()) + 1e-12)

    def pack(hh, uu, kk):
        gx = (np.roll(hh, -1, 1) - np.roll(hh, 1, 1)) * 0.5 * kk + (np.roll(uu, -1, 1) - np.roll(uu, 1, 1)) * 0.5 * us
        gy = (np.roll(hh, -1, 0) - np.roll(hh, 1, 0)) * 0.5 * kk + (np.roll(uu, -1, 0) - np.roll(uu, 1, 0)) * 0.5 * us
        nx, ny = -gx, gy
        inv = 1 / np.sqrt(nx * nx + ny * ny + 1)
        return np.stack([nx * inv, ny * inv, inv], -1)

    # Roughness: rougher in and around grooves (smeared along the brush), banded like the grain.
    energy = np.abs(h)
    ef = np.fft.rfft2(energy)
    ky = np.fft.fftfreq(n)[:, None]; kx = np.fft.rfftfreq(n)[None, :]
    energy = np.fft.irfft2(ef * np.exp(-((kx * 60) ** 2 + (ky * 1.2) ** 2)), s=(n, n)).astype(np.float32)
    energy = (energy - energy.mean()) / energy.std()
    rough = np.clip(0.42 + 0.07 * energy + 0.06 * band + 0.03 * short, 0.2, 0.7)

    # Albedo: dark gunmetal anodise with a faint blue cast (sRGB-encoded F0 colour).
    blot = spectral(n, lambda kx, ky: np.exp(-((kx ** 2 + ky ** 2) * 220 ** 2)))
    dye = spectral(n, lambda kx, ky: np.exp(-((kx * 500) ** 2 + (ky * 40) ** 2)))
    base = np.array([0.46, 0.50, 0.58], np.float32)
    lum = 1 + 0.035 * blot + 0.02 * band - 0.035 * np.clip(-h / 3, 0, 1)
    alb = base[None, None, :] * lum[..., None]
    alb[..., 2] *= 1 + 0.02 * dye
    alb[..., 0] *= 1 - 0.01 * dye

    for size, tag in ((2048, '2k'), (1024, '1k')):
        if size == 1024:
            hh, uu, rr, aa = box_down(h), box_down(und), box_down(rough), box_down(alb)
            kk = k * 0.85                                     # keep grain slope similar after averaging
        else:
            hh, uu, rr, aa = h, und, rough, alb
            kk = k
        nm = pack(hh, uu, kk)
        enc = np.clip(nm * 0.5 + 0.5, 0, 1)
        p = os.path.join(WORK, f'normal_{tag}.png'); save_png(p, enc); webp(p, f'brushed_normal_{tag}.webp', True)
        p = os.path.join(WORK, f'rough_{tag}.png'); save_png(p, rr); webp(p, f'brushed_rough_{tag}.webp', False, 80)
        p = os.path.join(WORK, f'albedo_{tag}.png'); save_png(p, aa); webp(p, f'brushed_albedo_{tag}.webp', False, 90)
        if size == 2048:
            # seam checks: whole 2x2 (downsampled) and a 1:1 crop around the four-way seam
            tile_preview(box_down(enc), 'tile_normal_2x2.png')
            tile_preview(enc, 'tile_normal_seam.png', crop=256)
            shade = np.clip(0.5 + 2.2 * (nm[..., 1] * 0.8 + nm[..., 0] * 0.2), 0, 1)
            tile_preview(shade, 'tile_shade_seam.png', crop=256)
            tile_preview(box_down(rr), 'tile_rough_2x2.png')


# ================================================================ 2. fastener
def revolve(name, profile, segs, mat):
    """Lathe (r, z) profile around Z. profile[0] must have r == 0."""
    verts, faces = [], []
    rings = []
    for (r, z) in profile:
        if r == 0:
            verts.append((0, 0, z)); rings.append([len(verts) - 1] * segs)
            continue
        ring = []
        for s in range(segs):
            a = 2 * math.pi * s / segs
            verts.append((r * math.cos(a), r * math.sin(a), z)); ring.append(len(verts) - 1)
        rings.append(ring)
    for i in range(len(rings) - 1):
        A, B = rings[i], rings[i + 1]
        for s in range(segs):
            s2 = (s + 1) % segs
            f = [A[s], B[s], B[s2], A[s2]]
            f = [v for j, v in enumerate(f) if v not in f[:j]]
            if len(f) >= 3:
                faces.append(f[::-1])
    ob = mesh_obj(name, verts, faces, mat)
    for p in ob.data.polygons:
        p.use_smooth = True
    return ob


def torx_prism(name, r_in, r_out, z0, z1, segs=240):
    pts = []
    for s in range(segs):
        a = 2 * math.pi * s / segs
        c = 0.5 + 0.5 * math.cos(6 * a)
        r = r_in + (r_out - r_in) * (c ** 0.55)
        pts.append((r * math.cos(a), r * math.sin(a)))
    verts = [(x, y, z0) for x, y in pts] + [(x, y, z1) for x, y in pts]
    faces = [list(range(segs))[::-1], list(range(segs, 2 * segs))]
    for s in range(segs):
        s2 = (s + 1) % segs
        faces.append([s, s2, segs + s2, segs + s])
    return mesh_obj(name, verts, faces)


def titanium_material(mode):
    """mode: 'beauty' | 'normal' | 'albedo' | 'ao'. Same bump in every mode so the passes agree."""
    m, nt, out = node_mat('Ti_' + mode)
    N = nt.nodes
    geo = N.new('ShaderNodeNewGeometry')
    tc = N.new('ShaderNodeTexCoord')
    # concentric turning marks on the head
    wave = N.new('ShaderNodeTexWave'); wave.wave_type = 'RINGS'
    wave.inputs['Scale'].default_value = 26.0
    wave.inputs['Distortion'].default_value = 0.6
    wave.inputs['Detail'].default_value = 3.0
    nt.links.new(tc.outputs['Object'], wave.inputs['Vector'])
    bump = N.new('ShaderNodeBump')
    bump.inputs['Strength'].default_value = 0.08
    bump.inputs['Distance'].default_value = 0.004
    nt.links.new(wave.outputs['Fac'], bump.inputs['Height'])
    # cavity darkening in the recess
    ao = N.new('ShaderNodeAmbientOcclusion'); ao.samples = 32
    ao.inputs['Distance'].default_value = 0.25
    col = (0.50, 0.50, 0.53)
    if mode == 'beauty':
        b = N.new('ShaderNodeBsdfPrincipled')
        b.inputs['Base Color'].default_value = (*col, 1)
        b.inputs['Metallic'].default_value = 1.0
        b.inputs['Roughness'].default_value = 0.26
        nt.links.new(bump.outputs['Normal'], b.inputs['Normal'])
        nt.links.new(b.outputs[0], out.inputs['Surface'])
        return m
    e = N.new('ShaderNodeEmission')
    nt.links.new(e.outputs[0], out.inputs['Surface'])
    if mode == 'normal':
        vt = N.new('ShaderNodeVectorTransform')
        vt.vector_type = 'NORMAL'; vt.convert_from = 'WORLD'; vt.convert_to = 'CAMERA'
        nt.links.new(bump.outputs['Normal'], vt.inputs['Vector'])
        mad = N.new('ShaderNodeVectorMath'); mad.operation = 'MULTIPLY_ADD'
        mad.inputs[1].default_value = (0.5, 0.5, 0.5); mad.inputs[2].default_value = (0.5, 0.5, 0.5)
        nt.links.new(vt.outputs[0], mad.inputs[0])
        nt.links.new(mad.outputs[0], e.inputs['Color'])
    elif mode == 'albedo':
        e.inputs['Color'].default_value = (*col, 1)
    elif mode == 'ao':
        nt.links.new(bump.outputs['Normal'], ao.inputs['Normal'])
        nt.links.new(ao.outputs['AO'], e.inputs['Color'])
    return m


def countersink_material(mode):
    m, nt, out = node_mat('Sink_' + mode)
    e = nt.nodes.new('ShaderNodeEmission')
    nt.links.new(e.outputs[0], out.inputs['Surface'])
    if mode == 'normal':
        geo = nt.nodes.new('ShaderNodeNewGeometry')
        vt = nt.nodes.new('ShaderNodeVectorTransform')
        vt.vector_type = 'NORMAL'; vt.convert_from = 'WORLD'; vt.convert_to = 'CAMERA'
        nt.links.new(geo.outputs['Normal'], vt.inputs['Vector'])
        mad = nt.nodes.new('ShaderNodeVectorMath'); mad.operation = 'MULTIPLY_ADD'
        mad.inputs[1].default_value = (0.5, 0.5, 0.5); mad.inputs[2].default_value = (0.5, 0.5, 0.5)
        nt.links.new(vt.outputs[0], mad.inputs[0])
        nt.links.new(mad.outputs[0], e.inputs['Color'])
    elif mode == 'albedo':
        e.inputs['Color'].default_value = (0.03, 0.035, 0.045, 1)   # shadowed gap: albedo ~ black
    elif mode == 'ao':
        e.inputs['Color'].default_value = (0.15, 0.15, 0.15, 1)
    else:
        b = nt.nodes.new('ShaderNodeBsdfPrincipled')
        b.inputs['Base Color'].default_value = (0.2, 0.23, 0.3, 1)
        b.inputs['Metallic'].default_value = 1.0
        b.inputs['Roughness'].default_value = 0.4
        nt.links.remove(e.outputs[0].links[0])
        nt.links.new(b.outputs[0], out.inputs['Surface'])
    return m


def torx_radius(a, r_in=0.27, r_out=0.38):
    c = 0.5 + 0.5 * math.cos(6 * a)
    return r_in + (r_out - r_in) * (c ** 0.55)


def bolt_head_mesh(mat, segs=240, rings=150):
    """Countersunk head as a displaced polar grid: slightly domed top, rounded rim and a Torx
    recess with steep walls and a conical floor (no booleans, so the mesh is always clean)."""
    def dome(r):
        return 0.075 - 0.035 * r * r - 0.05 * max(0.0, (r - 0.88) / 0.12) ** 2

    def smooth(e0, e1, x):
        t = min(1.0, max(0.0, (x - e0) / (e1 - e0)))
        return t * t * (3 - 2 * t)

    verts, faces, grid = [(0, 0, dome(0) - 0.32)], [], []
    for i in range(1, rings + 1):
        r = i / rings
        row = []
        for s in range(segs):
            a = 2 * math.pi * s / segs
            rt = torx_radius(a)
            inside = 1 - smooth(rt - 0.012, rt + 0.006, r)
            floor = dome(r) - 0.24 - 0.08 * (1 - min(1.0, r / rt))
            z = dome(r) * (1 - inside) + floor * inside
            verts.append((r * math.cos(a), r * math.sin(a), z)); row.append(len(verts) - 1)
        grid.append(row)
    for s in range(segs):
        faces.append([0, grid[0][s], grid[0][(s + 1) % segs]])
    for i in range(rings - 1):
        A, B = grid[i], grid[i + 1]
        for s in range(segs):
            s2 = (s + 1) % segs
            faces.append([A[s], B[s], B[s2], A[s2]])
    lower = []
    for s in range(segs):
        a = 2 * math.pi * s / segs
        verts.append((0.72 * math.cos(a), 0.72 * math.sin(a), -0.3)); lower.append(len(verts) - 1)
    last = grid[-1]
    for s in range(segs):
        s2 = (s + 1) % segs
        faces.append([last[s], lower[s], lower[s2], last[s2]])
    ob = mesh_obj('BoltHead', verts, faces, mat)
    for p in ob.data.polygons:
        p.use_smooth = True
    return ob


def make_bolt(mode, scale=1.0):
    """Countersunk Torx head (radius 1) flush with z = 0, plus the thin countersink gap ring."""
    head = bolt_head_mesh(titanium_material(mode))
    ring = revolve('Sink', [(0, -0.2), (0.98, -0.2), (1.0, -0.035), (1.11, 0.0)],
                   192, countersink_material(mode))
    for ob in (head, ring):
        ob.scale = (scale,) * 3
    return head, ring


def build_fastener():
    print('[fastener]')
    passes = {}
    for mode in ('normal', 'albedo', 'ao'):
        sc = reset_scene()
        sc.render.resolution_x = sc.render.resolution_y = 512
        sc.render.film_transparent = True
        sc.cycles.samples = 64 if mode != 'ao' else 256
        sc.cycles.use_denoising = False                 # data passes: no denoiser on raw normals/albedo
        sc.render.image_settings.file_format = 'OPEN_EXR'
        sc.render.image_settings.color_depth = '32'
        sc.render.image_settings.color_mode = 'RGBA'
        world = bpy.data.worlds.new('W'); sc.world = world
        make_bolt(mode)
        cam = bpy.data.cameras.new('C'); cam.type = 'ORTHO'; cam.ortho_scale = 2.3
        co = bpy.data.objects.new('C', cam); co.location = (0, 0, 5)
        sc.collection.objects.link(co); sc.camera = co
        path = os.path.join(WORK, f'bolt_{mode}.exr')
        sc.render.filepath = path
        bpy.ops.render.render(write_still=True)
        passes[mode] = load_pixels(path)

    a = passes['normal'][..., 3]
    safe = np.maximum(a, 1e-4)[..., None]
    nrm = passes['normal'][..., :3] / safe * 2 - 1
    nrm /= np.linalg.norm(nrm, axis=-1, keepdims=True) + 1e-6
    alb = passes['albedo'][..., :3] / safe
    ao = passes['ao'][..., 0] / np.maximum(a, 1e-4)

    def down(x, f=4):
        h, w = x.shape[:2]
        return x.reshape(h // f, f, w // f, f, *x.shape[2:]).mean(axis=(1, 3))

    # Downsample premultiplied, then unpremultiply, so edges stay clean.
    a4 = down(a)
    s4 = np.maximum(a4, 1e-4)
    n4 = down(nrm * a[..., None]) / s4[..., None]
    n4 /= np.linalg.norm(n4, axis=-1, keepdims=True) + 1e-6
    al4 = down(alb * a[..., None]) / s4[..., None]
    ao4 = down(ao * a) / s4
    m = a4 < 1e-3
    n4[m] = (0, 0, 1); al4[m] = 0; ao4[m] = 1

    nimg = np.dstack([n4 * 0.5 + 0.5, a4])
    aimg = np.dstack([np.clip(al4, 0, 1) ** (1 / 2.2), np.clip(ao4, 0, 1)])
    p = os.path.join(WORK, 'fastener_normal.png'); save_png(p, nimg); webp(p, 'fastener_normal.webp', True)
    p = os.path.join(WORK, 'fastener_albedo.png'); save_png(p, aimg); webp(p, 'fastener_albedo.webp', True)
    save_png(os.path.join(WORK, 'fastener_preview.png'),
             np.concatenate([nimg[..., :3], np.repeat(aimg[..., 3:], 3, -1), aimg[..., :3],
                             np.repeat(a4[..., None], 3, -1)], 1))


# ================================================================ 3. studio
def studio(sc):
    """Emitters around the plate (plate at origin, facing -Y). Returns nothing; adds objects."""
    world = bpy.data.worlds.new('Studio'); sc.world = world
    try:
        world.use_nodes = True
    except Exception:
        pass
    bg = world.node_tree.nodes.get('Background') or world.node_tree.nodes.new('ShaderNodeBackground')
    bg.inputs['Color'].default_value = (0.0005, 0.0007, 0.0012, 1)
    bg.inputs['Strength'].default_value = 1.0
    wo = world.node_tree.nodes.get('World Output') or world.node_tree.nodes.new('ShaderNodeOutputWorld')
    if not bg.outputs[0].links:
        world.node_tree.links.new(bg.outputs[0], wo.inputs['Surface'])

    def softbox(name, size, loc, color, strength, look_at=(0, 0, 0), falloff=True):
        m, nt, e = emit_mat(name, color, strength)
        if falloff:   # hot centre, soft edge, like a diffusion panel
            tc = nt.nodes.new('ShaderNodeTexCoord')
            gr = nt.nodes.new('ShaderNodeTexGradient'); gr.gradient_type = 'QUADRATIC_SPHERE'
            mp = nt.nodes.new('ShaderNodeMapping'); mp.inputs['Scale'].default_value = (1.05, 1.05, 1)
            nt.links.new(tc.outputs['Generated'], mp.inputs['Vector'])
            sub = nt.nodes.new('ShaderNodeVectorMath'); sub.operation = 'SUBTRACT'
            sub.inputs[1].default_value = (0.5, 0.5, 0)
            nt.links.new(tc.outputs['Generated'], sub.inputs[0])
            scl = nt.nodes.new('ShaderNodeVectorMath'); scl.operation = 'MULTIPLY'
            scl.inputs[1].default_value = (2.0, 2.0, 0)
            nt.links.new(sub.outputs[0], scl.inputs[0])
            nt.links.new(scl.outputs[0], gr.inputs['Vector'])
            mr = nt.nodes.new('ShaderNodeMapRange')
            mr.inputs['To Min'].default_value = 0.35
            nt.links.new(gr.outputs['Fac'], mr.inputs['Value'])
            mul = nt.nodes.new('ShaderNodeMath'); mul.operation = 'MULTIPLY'
            mul.inputs[1].default_value = strength
            nt.links.new(mr.outputs[0], mul.inputs[0])
            nt.links.new(mul.outputs[0], e.inputs['Strength'])
        w, h = size
        ob = mesh_obj(name, [(-w / 2, -h / 2, 0), (w / 2, -h / 2, 0), (w / 2, h / 2, 0), (-w / 2, h / 2, 0)],
                      [[0, 1, 2, 3]], m)
        ob.location = loc
        d = np.array(look_at, float) - np.array(loc, float)
        from mathutils import Vector
        ob.rotation_euler = Vector(d).to_track_quat('Z', 'Y').to_euler()
        ob.data.uv_layers.new()
        return ob

    # key: large overhead softbox, slightly in front of the plate
    softbox('Key', (9, 5), (0, -5, 9), (1.0, 0.985, 0.96), 7.0)
    # long horizontal strip high in front (the classic product-shot top strip)
    softbox('StripTop', (14, 0.5), (0, -12, 6), (0.95, 0.97, 1.0), 16.0)
    # two vertical strips left/right in front
    softbox('StripL', (0.6, 8), (-9, -9, 1.5), (0.9, 0.94, 1.0), 9.0)
    softbox('StripR', (0.6, 8), (9, -9, 1.5), (0.9, 0.94, 1.0), 9.0)
    # cool blue kicker from below, in front
    softbox('RimBlue', (12, 2.5), (0, -7, -7), (0.17, 0.45, 1.0), 5.0)
    # faint card behind the camera so the black is not perfectly dead
    softbox('Bounce', (20, 12), (0, -22, 0), (0.55, 0.62, 0.8), 0.018, falloff=False)


def build_env():
    print('[env]')
    sc = reset_scene()
    sc.render.resolution_x, sc.render.resolution_y = 1024, 512
    sc.cycles.samples = 256
    sc.cycles.use_denoising = True
    sc.cycles.denoiser = 'OPENIMAGEDENOISE'
    sc.render.image_settings.file_format = 'OPEN_EXR'
    sc.render.image_settings.color_depth = '32'
    studio(sc)
    cam = bpy.data.cameras.new('Pano')
    cam.type = 'PANO'
    try:
        cam.panorama_type = 'EQUIRECTANGULAR'
    except Exception:
        cam.cycles.panorama_type = 'EQUIRECTANGULAR'
    co = bpy.data.objects.new('Pano', cam)
    # Look toward the viewer (-Y) with Z up: image centre = direction back toward the camera.
    co.rotation_euler = (math.radians(90), 0, math.radians(180))
    sc.collection.objects.link(co); sc.camera = co
    path = os.path.join(WORK, 'env.exr')
    sc.render.filepath = path
    bpy.ops.render.render(write_still=True)
    env = load_pixels(path)[..., :3]
    np.save(os.path.join(WORK, 'env.npy'), env)
    print('  env max', env.max(), 'mean', env.mean())

    def enc(c):
        c = np.maximum(c, 0)
        return (c / (1 + c)) ** (1 / 2.2)

    def blur(img, sx, sy):
        h, w = img.shape[:2]
        out = np.empty_like(img)
        kx = np.fft.rfftfreq(w)[None, :]; ky = np.fft.fftfreq(2 * h)[:, None]
        filt = np.exp(-2 * (np.pi ** 2) * ((kx * sx) ** 2 + (ky * sy) ** 2))
        for c in range(img.shape[2]):
            ch = np.concatenate([img[..., c], img[::-1, :, c]], 0)   # mirror in latitude
            out[..., c] = np.fft.irfft2(np.fft.rfft2(ch) * filt, s=ch.shape)[:h]
        return np.maximum(out, 0)

    p = os.path.join(WORK, 'env_sharp.png'); save_png(p, enc(env)); webp(p, 'studio_env.webp', False, 92)
    soft = blur(env, 26, 26)
    soft = box_down(box_down(soft))
    p = os.path.join(WORK, 'env_soft.png'); save_png(p, enc(soft)); webp(p, 'studio_env_soft.webp', False, 92)


# ================================================================ 4. reference
def rounded_rect(w, h, r, segs=20):
    pts = []
    for cx, cy, a0 in ((w / 2 - r, h / 2 - r, 0), (-w / 2 + r, h / 2 - r, 90), (-w / 2 + r, -h / 2 + r, 180),
                       (w / 2 - r, -h / 2 + r, 270)):
        for s in range(segs + 1):
            a = math.radians(a0 + 90 * s / segs)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def build_reference():
    print('[reference]')
    sc = reset_scene()
    sc.render.resolution_x, sc.render.resolution_y = 1400, 900
    sc.cycles.samples = 384
    sc.cycles.use_denoising = True
    sc.cycles.denoiser = 'OPENIMAGEDENOISE'
    sc.view_settings.view_transform = 'AgX'
    studio(sc)
    bpy.data.objects['Bounce'].visible_camera = False   # it sits behind the viewer, i.e. in front of this camera

    W, H, R, T = 6.0, 3.0, 0.28, 0.08          # 600 x 300 px plate, 28 px radius
    pts = rounded_rect(W, H, R)
    bm = bmesh.new()
    top = [bm.verts.new((x, y, T / 2)) for x, y in pts]
    bot = [bm.verts.new((x, y, -T / 2)) for x, y in pts]
    bm.faces.new(top)
    bm.faces.new(bot[::-1])
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new([bot[i], bot[j], top[j], top[i]])
    me = bpy.data.meshes.new('Plate'); bm.to_mesh(me); bm.free()
    plate = bpy.data.objects.new('Plate', me); sc.collection.objects.link(plate)
    # UVs in texel space: 2 texels per CSS px (DPR 2) -> 2048 texels = 10.24 units
    uvl = me.uv_layers.new(name='UVMap')
    for poly in me.polygons:
        for li in poly.loop_indices:
            v = me.vertices[me.loops[li].vertex_index].co
            uvl.data[li].uv = (v.x / 10.24 + 0.13, v.y / 10.24 + 0.37)
    for p in me.polygons:
        p.use_smooth = False
    bev = plate.modifiers.new('roll', 'BEVEL')          # soft rolled edge
    bev.width = 0.11; bev.segments = 10; bev.limit_method = 'ANGLE'; bev.angle_limit = math.radians(40)
    bev.profile = 0.62
    bev2 = plate.modifiers.new('chamfer', 'BEVEL')      # crisp machined chamfer on the new outer edge
    bev2.width = 0.012; bev2.segments = 1; bev2.limit_method = 'ANGLE'; bev2.angle_limit = math.radians(25)
    ws = plate.modifiers.new('ws', 'WEIGHTED_NORMAL'); ws.keep_sharp = True

    m, nt, out = node_mat('Anodised')
    N = nt.nodes
    b = N.new('ShaderNodeBsdfPrincipled')
    nt.links.new(b.outputs[0], out.inputs['Surface'])

    def tex(name, noncolor=True):
        img = bpy.data.images.load(os.path.join(WORK, name))
        img.colorspace_settings.name = 'Non-Color' if noncolor else 'sRGB'
        t = N.new('ShaderNodeTexImage'); t.image = img; t.interpolation = 'Cubic'
        return t
    tn, tr, ta = tex('normal_2k.png'), tex('rough_2k.png'), tex('albedo_2k.png', False)
    nm = N.new('ShaderNodeNormalMap'); nm.space = 'TANGENT'; nm.uv_map = 'UVMap'
    nm.inputs['Strength'].default_value = 0.45
    nt.links.new(tn.outputs['Color'], nm.inputs['Color'])
    nt.links.new(nm.outputs['Normal'], b.inputs['Normal'])
    mr = N.new('ShaderNodeMapRange')
    mr.inputs['From Min'].default_value = 0.0; mr.inputs['From Max'].default_value = 1.0
    mr.inputs['To Min'].default_value = 0.0; mr.inputs['To Max'].default_value = 0.72
    nt.links.new(tr.outputs['Color'], mr.inputs['Value'])
    nt.links.new(mr.outputs[0], b.inputs['Roughness'])
    tint = N.new('ShaderNodeMix'); tint.data_type = 'RGBA'; tint.blend_type = 'MULTIPLY'
    tint.inputs['Factor'].default_value = 1.0
    nt.links.new(ta.outputs['Color'], tint.inputs['A'])
    tint.inputs['B'].default_value = (0.30, 0.34, 0.42, 1)
    nt.links.new(tint.outputs['Result'], b.inputs['Base Color'])
    b.inputs['Metallic'].default_value = 1.0
    b.inputs['Anisotropic'].default_value = 0.75
    tg = N.new('ShaderNodeTangent'); tg.direction_type = 'UV_MAP'; tg.uv_map = 'UVMap'
    nt.links.new(tg.outputs['Tangent'], b.inputs['Tangent'])
    me.materials.append(m)
    plate.rotation_euler = (math.radians(90), 0, 0)      # local +Z (front) -> world -Y

    # fasteners at the corners (6.5 px radius, 22 px inset)
    for sx in (-1, 1):
        for sy in (-1, 1):
            parts = make_bolt('beauty', scale=0.065)
            for ob in parts:
                ob.parent = plate
                ob.location = (sx * (W / 2 - 0.22), sy * (H / 2 - 0.22), T / 2)

    # backdrop the plate sits in front of (the page), 40 px behind it
    bd_m, bd_nt, bd_out = node_mat('Backdrop')
    bb = bd_nt.nodes.new('ShaderNodeBsdfPrincipled')
    bb.inputs['Base Color'].default_value = (0.0004, 0.0005, 0.0007, 1)
    bb.inputs['Roughness'].default_value = 0.9
    bd_nt.links.new(bb.outputs[0], bd_out.inputs['Surface'])
    backdrop = mesh_obj('Backdrop', [(-30, 0.45, -20), (30, 0.45, -20), (30, 0.45, 20), (-30, 0.45, 20)],
                        [[0, 1, 2, 3]], bd_m)

    # pointer strip light (the web version's moving light), parked on the right third
    ld = bpy.data.lights.new('Pointer', 'AREA'); ld.shape = 'RECTANGLE'; ld.size = 0.36; ld.size_y = 1.6
    ld.energy = 900; ld.color = (0.85, 0.92, 1.0)
    lo = bpy.data.objects.new('Pointer', ld); lo.location = (1.4, -2.6, 0.9)
    from mathutils import Vector
    lo.rotation_euler = (Vector((1.4, 0, 0.4)) - Vector(lo.location)).to_track_quat('-Z', 'Y').to_euler()
    sc.collection.objects.link(lo)

    cam = bpy.data.cameras.new('Cam'); cam.sensor_fit = 'HORIZONTAL'
    cam.angle = 2 * math.atan(7.0 / 23.0)
    co = bpy.data.objects.new('Cam', cam); co.location = (0, -23, 0)
    co.rotation_euler = (math.radians(90), 0, 0)
    sc.collection.objects.link(co); sc.camera = co
    sc.render.image_settings.file_format = 'PNG'
    sc.render.filepath = os.path.join(ROOT, 'blender', 'metal_reference.png')
    bpy.ops.render.render(write_still=True)
    # close-up of the top-right corner (edge + fastener) for detail comparison
    cam.angle = 2 * math.atan(0.9 / 23.0)
    co.location = (W / 2 - 0.55, -23, H / 2 - 0.4)
    sc.render.resolution_x, sc.render.resolution_y = 1000, 700
    sc.render.filepath = os.path.join(ROOT, 'blender', 'metal_reference_corner.png')
    bpy.ops.render.render(write_still=True)


if __name__ == '__main__':
    if 'surface' in ONLY:
        build_surface()
    if 'fastener' in ONLY:
        build_fastener()
    if 'env' in ONLY:
        build_env()
    if 'reference' in ONLY:
        build_reference()
    tot = sum(os.path.getsize(os.path.join(OUT, f)) for f in os.listdir(OUT))
    print(f'assets/metal total: {tot / 1024:.0f} KB')
