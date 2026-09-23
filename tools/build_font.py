"""
Builds PK Wide: an original extended display face in the spirit of motorsport wide grotesks
(flat-sided rounds, squared counters, heavy low-contrast strokes).

    python3 tools/build_font.py          # writes assets/fonts/pk-wide.woff2 + pk-wide-italic.woff2

Every glyph is assembled from three primitives on a 1000-unit em: straight bars, quarter-annulus
corners (two quadratic segments each) and sheared strokes for diagonals. Overlapping contours
all wind clockwise, so they union under the non-zero fill rule; nothing needs boolean ops.
Needs fontTools + brotli.
"""
import math, os
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'assets', 'fonts')

UPM, CAP = 1000, 700
S, T = 150, 128          # vertical stem / horizontal bar thickness (low contrast)
R = 250                  # outer corner radius of rounds: flat sides, squared counters
SB = 46                  # side bearing
K = 1 / math.cos(math.radians(22.5))


# ---------- contour primitives: lists of (x, y, on_curve) ----------
def rect(x0, y0, x1, y1):
    return [(x0, y0, 1), (x0, y1, 1), (x1, y1, 1), (x1, y0, 1)]


def poly(*pts):
    return [(x, y, 1) for x, y in pts]


def arc(cx, cy, rx, ry, a0, a1):
    """Quadratic approximation of an elliptical arc in 45-degree pieces, including both ends."""
    out, n = [], max(1, round(abs(a1 - a0) / 45))
    step = (a1 - a0) / n
    for i in range(n):
        a, m = math.radians(a0 + i * step), math.radians(a0 + (i + 0.5) * step)
        if i == 0:
            out.append((cx + rx * math.cos(a), cy + ry * math.sin(a), 1))
        out.append((cx + rx * K * math.cos(m), cy + ry * K * math.sin(m), 0))
        b = math.radians(a0 + (i + 1) * step)
        out.append((cx + rx * math.cos(b), cy + ry * math.sin(b), 1))
    return out


def corner(q, x0, y0, x1, y1, r):
    """Quarter annulus in the q corner ('tl','tr','bl','br') of box (x0,y0)-(x1,y1)."""
    cx = x0 + r if q[1] == 'l' else x1 - r
    cy = y0 + r if q[0] == 'b' else y1 - r
    a0 = {'tr': 0, 'tl': 90, 'bl': 180, 'br': 270}[q]
    outer = arc(cx, cy, r, r, a0, a0 + 90)
    inner = arc(cx, cy, max(r - S, 12), max(r - T, 12), a0 + 90, a0)
    return outer + inner


def stroke(x0, y0, x1, y1, w):
    """Diagonal stroke from (x0,y0) to (x1,y1) with horizontal width w (a parallelogram)."""
    return poly((x0, y0), (x0 + w, y0), (x1 + w, y1), (x1, y1))


def frame(x0, y0, x1, y1, corners='', edges='lrtb', r=None):
    """A box outline: rounded corners where listed, straight edges where listed."""
    r = min(r or R, (y1 - y0) / 2, (x1 - x0) / 2)
    cs = [corners[i:i + 2] for i in range(0, len(corners), 2)]
    out = [corner(q, x0, y0, x1, y1, r) for q in cs]
    # Edges run a few units into rounded corners so the pieces fuse with no anti-aliasing seams.
    rb = lambda q: r - 4 if q in cs else 0
    if 'l' in edges: out.append(rect(x0, y0 + rb('bl'), x0 + S, y1 - rb('tl')))
    if 'r' in edges: out.append(rect(x1 - S, y0 + rb('br'), x1, y1 - rb('tr')))
    if 't' in edges: out.append(rect(x0 + rb('tl'), y1 - T, x1 - rb('tr'), y1))
    if 'b' in edges: out.append(rect(x0 + rb('bl'), y0, x1 - rb('br'), y0 + T))
    return out


# ---------- glyphs: name -> (body width, [contours]) ----------
H, M = CAP, CAP / 2
W = S * 1.28                         # horizontal width of diagonal strokes


def glyphs():
    g = {}
    B = 860
    g['O'] = (B, frame(0, 0, B, H, 'tltrblbr'))
    g['C'] = (B - 20, frame(0, 0, B - 20, H, 'tltrblbr', 'ltb'))
    g['G'] = (B, frame(0, 0, B, H, 'tltrblbr', 'ltb') + [rect(B - S, R, B, H * 0.46), rect(B * 0.5, H * 0.46 - T, B, H * 0.46)])
    g['D'] = (B, frame(0, 0, B, H, 'trbr'))
    g['Q'] = (B, frame(0, 0, B, H, 'tltrblbr') + [stroke(B * 0.52, H * 0.34, B * 0.86 - W * 0.4, -70, W * 0.9)])
    g['U'] = (B - 40, frame(0, 0, B - 40, H, 'blbr', 'lrb'))
    g['J'] = (720, frame(0, 0, 720, H, 'blbr', 'rb') + [rect(0, R * 0.9, S, H * 0.36)])
    g['A'] = (840, frame(0, 0, 840, H, 'tltr', 'lrt') + [rect(0, 240, 840, 240 + T)])
    g['H'] = (820, [rect(0, 0, S, H), rect(820 - S, 0, 820, H), rect(0, M - T / 2, 820, M + T / 2)])
    g['I'] = (S, [rect(0, 0, S, H)])
    g['L'] = (700, [rect(0, 0, S, H), rect(0, 0, 700, T)])
    g['E'] = (740, [rect(0, 0, S, H), rect(0, H - T, 740, H), rect(0, M - T / 2, 690, M + T / 2), rect(0, 0, 740, T)])
    g['F'] = (720, [rect(0, 0, S, H), rect(0, H - T, 720, H), rect(0, M - T / 2 - 20, 670, M + T / 2 - 20)])
    g['T'] = (780, [rect(0, H - T, 780, H), rect(390 - S / 2, 0, 390 + S / 2, H)])
    g['P'] = (800, [rect(0, 0, S, H)] + frame(0, H * 0.34, 800, H, 'trbr', 'lrtb', r=180))
    g['R'] = (820, [rect(0, 0, S, H)] + frame(0, H * 0.36, 800, H, 'trbr', 'lrtb', r=180) + [stroke(820 - W * 1.1, 0, 820 * 0.40, H * 0.36 + T - 6, W * 1.1)])
    g['B'] = (820, frame(0, 0, 820, M + T / 2, 'trbr', r=170) + frame(0, M - T / 2, 780, H, 'trbr', r=160))
    g['S'] = (820, frame(0, M - T / 2, 820, H, 'tltrbl', 'ltb', r=200) + frame(0, 0, 820, M + T / 2, 'trbrbl', 'rb', r=200))
    g['K'] = (800, [rect(0, 0, S, H), stroke(S * 0.55, H * 0.38, 800 - W * 1.05, H, W * 1.05), stroke(800 - W * 1.05, 0, 800 * 0.36, H * 0.56, W * 1.05)])
    g['M'] = (1000, [rect(0, 0, S, H), rect(1000 - S, 0, 1000, H),
                     stroke(0, H, 500 - W / 2, H * 0.28, W), stroke(1000 - W, H, 500 - W / 2, H * 0.28, W)])
    g['N'] = (840, [rect(0, 0, S, H), rect(840 - S, 0, 840, H), stroke(0, H, 840 - W, 0, W)])
    g['V'] = (860, [stroke(0, H, 430 - W / 2, 0, W), stroke(860 - W, H, 430 - W / 2, 0, W)])
    g['W'] = (1160, [stroke(0, H, 290 - W / 2, 0, W), stroke(580 - W / 2, H * 0.72, 290 - W / 2, 0, W),
                     stroke(580 - W / 2, H * 0.72, 870 - W / 2, 0, W), stroke(1160 - W, H, 870 - W / 2, 0, W)])
    g['X'] = (840, [stroke(0, H, 840 - W, 0, W), stroke(840 - W, H, 0, 0, W)])
    g['Y'] = (840, [stroke(0, H, 420 - W / 2, H * 0.42, W), stroke(840 - W, H, 420 - W / 2, H * 0.42, W), rect(420 - S / 2, 0, 420 + S / 2, H * 0.46)])
    g['Z'] = (780, [rect(0, H - T, 780, H), rect(0, 0, 780, T), stroke(0, T, 780 - W, H - T, W)])
    # figures
    g['zero'] = (760, frame(0, 0, 760, H, 'tltrblbr'))
    g['one'] = (420, [rect(420 - S, 0, 420, H), stroke(0, H * 0.62, 420 - S - 20, H, S * 0.9)])
    g['two'] = (760, frame(0, M - T / 2, 760, H, 'tltr', 'trb', r=190) + [rect(0, M - T / 2, 760 - 190, M + T / 2)] + frame(0, 0, 760, M + T / 2, 'tl', 'lb', r=150))
    g['three'] = (760, frame(0, M - T / 2, 760, H, 'trbr', 'trb', r=170) + frame(0, 0, 760, M + T / 2, 'trbr', 'rb', r=170))
    g['four'] = (780, [rect(0, M - T / 2 - 40, S, H), rect(0, M - T / 2 - 40, 780, M + T / 2 - 40), rect(780 - S - 60, 0, 780 - 60, H)])
    g['five'] = (760, [rect(0, M - T / 2, S, H), rect(0, H - T, 760, H)] + frame(0, 0, 760, M + T / 2, 'trbrbl', 'trb', r=190))
    g['six'] = (780, frame(0, 0, 780, H, 'tltrbl', 'ltb') + frame(0, 0, 780, M + T / 2, 'trbr', 'rt', r=190))
    g['seven'] = (740, [rect(0, H - T, 740, H), stroke(740 * 0.22, 0, 740 - W, H - T, W)])
    g['eight'] = (780, frame(0, 0, 780, M + T / 2, 'tltrblbr', r=200) + frame(20, M - T / 2, 760, H, 'tltrblbr', r=180))
    g['nine'] = (780, frame(0, 0, 780, H, 'trbrbl', 'rtb') + frame(0, M - T / 2, 780, H, 'tlbl', 'lb', r=190))
    # punctuation
    dot = 170
    g['period'] = (dot, [rect(0, 0, dot, dot)])
    g['comma'] = (dot, [rect(0, 0, dot, dot), poly((dot - 70, 10), (dot, 10), (dot - 40, -190), (dot - 110, -190))])
    g['hyphen'] = (460, [rect(0, M - 70 - T / 2, 460, M - 70 + T / 2)])
    g['colon'] = (dot, [rect(0, 0, dot, dot), rect(0, 380, dot, 380 + dot)])
    g['periodcentered'] = (dot, [rect(0, M - dot / 2, dot, M + dot / 2)])
    g['slash'] = (560, [stroke(0, -60, 560 - W, H + 40, W)])
    g['exclam'] = (dot, [rect(0, 0, dot, dot), rect(10, 250, dot - 10, H)])
    g['quotesingle'] = (150, [rect(0, 430, 150, H)])
    g['quoteright'] = (dot, [stroke(20, 420, 40, H - dot, dot - 60), rect(0, H - dot, dot, H)])
    g['quoteleft'] = (dot, [rect(0, 430, dot, 430 + dot), stroke(40, 430 + dot - 10, 20, H, dot - 60)])
    g['quotedblleft'] = (dot * 2 + 70, g['quoteleft'][1] + [[(x + dot + 70, y, o) for x, y, o in c] for c in g['quoteleft'][1]])
    g['quotedblright'] = (dot * 2 + 70, g['quoteright'][1] + [[(x + dot + 70, y, o) for x, y, o in c] for c in g['quoteright'][1]])
    g['ampersand'] = (860, frame(0, 0, 700, M + T / 2, 'tlblbr', 'lb', r=190) + frame(40, M - T / 2, 600, H, 'tltr', 'lrt', r=170)
                      + [stroke(80, M + T / 2 - 20, 860 - W, 0, W), rect(560, 0, 860, T)])
    g['space'] = (260, [])
    return g


CMAP = {**{c: c for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'},
        '0': 'zero', '1': 'one', '2': 'two', '3': 'three', '4': 'four', '5': 'five', '6': 'six', '7': 'seven',
        '8': 'eight', '9': 'nine', '.': 'period', ',': 'comma', '-': 'hyphen', ':': 'colon', '·': 'periodcentered',
        '/': 'slash', '!': 'exclam', "'": 'quotesingle', '’': 'quoteright', '‘': 'quoteleft', '“': 'quotedblleft',
        '”': 'quotedblright', '&': 'ampersand', ' ': 'space', ' ': 'space'}
CMAP.update({c.lower(): c for c in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'})   # the site sets display text in caps


_OPEN_R = ['T', 'V', 'W', 'Y', 'F', 'P']          # letters with space at the lower right
_OPEN_L = ['A', 'V', 'W', 'Y', 'J']
KERN = {}
for l in ['A']:
    for r in ['V', 'W', 'Y', 'T']:
        KERN[(l, r)] = -70 if r != 'T' else -40
for l in ['V', 'W', 'Y']:
    for r in ['A', 'period', 'comma']:
        KERN[(l, r)] = -80 if r == 'A' else -110
    KERN[(l, 'O')] = -20
for l in ['T']:
    for r in ['A', 'period', 'comma', 'O']:
        KERN[(l, r)] = -60 if r != 'O' else -20
for l in ['P', 'F']:
    for r in ['A', 'period', 'comma']:
        KERN[(l, r)] = -60 if r == 'A' else -90
KERN[('L', 'T')] = -60; KERN[('L', 'V')] = -70; KERN[('L', 'Y')] = -70
KERN[('X', 'comma')] = -40; KERN[('X', 'period')] = -40
KERN[('R', 'Y')] = -20; KERN[('R', 'V')] = -20


def area(c):
    return sum(c[i][0] * c[(i + 1) % len(c)][1] - c[(i + 1) % len(c)][0] * c[i][1] for i in range(len(c))) / 2


def draw(contours, shear):
    pen = TTGlyphPen(None)
    for c in contours:
        c = [(x + y * shear, y, o) for x, y, o in c]
        if area(c) > 0:                      # TrueType outer contours wind clockwise
            c = c[::-1]
        start = next(i for i, p in enumerate(c) if p[2])
        c = c[start:] + c[:start]
        pen.moveTo((round(c[0][0]), round(c[0][1])))
        off = []
        for x, y, on in c[1:] + [c[0]]:
            pt = (round(x), round(y))
            if on:
                pen.qCurveTo(*off, pt) if off else pen.lineTo(pt)
                off = []
            else:
                off.append(pt)
        pen.closePath()
    return pen.glyph()


def build(italic):
    shear = math.tan(math.radians(11)) if italic else 0
    gl = glyphs()
    order = ['.notdef'] + list(gl)
    glyf, metrics = {'.notdef': draw([rect(0, 0, 500, H)], 0)}, {'.notdef': (600, 50)}
    for name, (bw, cs) in gl.items():
        shifted = [[(x + SB, y, o) for x, y, o in c] for c in cs]
        glyf[name] = draw(shifted, shear)
        metrics[name] = (round(bw + 2 * SB), SB)
    style = 'Italic' if italic else 'Regular'
    fb = FontBuilder(UPM, isTTF=True)
    fb.setupGlyphOrder(order)
    fb.setupCharacterMap({ord(k): v for k, v in CMAP.items()})
    fb.setupGlyf(glyf)
    for n, g in glyf.items():                # recompute left side bearings from real outlines
        g.recalcBounds(fb.font['glyf'])
        metrics[n] = (metrics[n][0], getattr(g, 'xMin', 0))
    fb.setupHorizontalMetrics(metrics)
    fb.setupHorizontalHeader(ascent=900, descent=-220)
    fb.setupNameTable({'familyName': 'PK Wide', 'styleName': style, 'uniqueFontIdentifier': f'PKWide-{style}',
                       'fullName': f'PK Wide {style}', 'psName': f'PKWide-{style}', 'version': 'Version 1.0',
                       'copyright': 'Original typeface for pranav-website, 2026'})
    fb.setupOS2(sTypoAscender=900, sTypoDescender=-220, sTypoLineGap=0, usWinAscent=940, usWinDescent=260,
                sCapHeight=CAP, sxHeight=CAP, usWeightClass=900, usWidthClass=9, fsSelection=0x01 if italic else 0x40)
    fb.setupPost(italicAngle=-11 if italic else 0)
    if italic:
        fb.font['head'].macStyle |= 0x02
    # Kerning for the open diagonal and overhanging shapes (units of 1000/em).
    fea = 'languagesystem DFLT dflt;\nfeature kern {\n' + ''.join(
        f'  pos {a} {b} {v};\n' for (a, b), v in KERN.items()) + '} kern;\n'
    from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
    addOpenTypeFeaturesFromString(fb.font, fea)
    fb.font.flavor = 'woff2'
    path = os.path.join(OUT, 'pk-wide-italic.woff2' if italic else 'pk-wide.woff2')
    fb.save(path)
    print('wrote', os.path.relpath(path, ROOT), os.path.getsize(path), 'bytes')


if __name__ == '__main__':
    os.makedirs(OUT, exist_ok=True)
    build(False)
    build(True)
