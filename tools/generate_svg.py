"""Generate assets/animation.svg: a simple two-eye + mouth robot face that
morphs through 10 mood stages (0 = calm/neutral, 9 = furious), including a
color shift from calm blue to angry red.

The same 16s / 10-pose / 1.0s-hold / 0.6s-transition timeline used by
tools/render_frames.py is encoded directly as SMIL <animate> keyframes, so
that script's time-slicing (pose screenshots, transition sweeps) keeps
working unchanged. Re-run this script whenever the mood design or timeline
constants change, then re-run tools/render_frames.py to re-bake the PNGs.
"""

import colorsys
import os

NUM_POSES = 10
TOTAL_DURATION = 16.0
SEGMENT_DURATION = TOTAL_DURATION / NUM_POSES  # 1.6s
HOLD_DURATION = 1.0
HOLD_FRACTION = HOLD_DURATION / TOTAL_DURATION  # 0.0625

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_PATH = os.path.join(ROOT_DIR, "assets", "animation.svg")

# Eye/mouth base geometry, taken from the reference design.
EYE_Y = 38
EYE_W = 28
EYE_H = 28
EYE_RX = 8
EYE_LEFT_X = 37
EYE_RIGHT_X = 95
MOUTH_X = 65
MOUTH_Y = 80
MOUTH_W = 30
MOUTH_H = 13
MOUTH_RX = 5
MOUTH_CENTER_Y = MOUTH_Y + MOUTH_H / 2  # fixed vertical anchor as height grows


def mood_color(k):
    """Blue (calm) -> teal -> yellow -> orange -> red (furious) across k=0..9."""
    hue = 200 - (200 * k / (NUM_POSES - 1))  # 200deg -> 0deg
    r, g, b = colorsys.hls_to_rgb(hue / 360, 0.62, 0.85)
    return "#{:02x}{:02x}{:02x}".format(round(r * 255), round(g * 255), round(b * 255))


def pose_params(k):
    """Per-stage geometry/color for k in 0..NUM_POSES-1 (0=calm, 9=furious).

    Deltas are deliberately large so each stage reads clearly from shape
    alone (squint, tilt, convergence, mouth size/sharpness) - the color
    shift is a secondary cue, not the only one.
    """
    shift = k * 1.5  # eyes creep toward each other (furrowed look)
    eye_h = EYE_H - k * 2.0
    eye_y = EYE_Y + (EYE_H - eye_h) / 2  # keeps vertical eye-center fixed at 52
    angle = k * 4.5  # degrees, slants the eyes inward-down like angry brows
    color = mood_color(k)

    mouth_w = MOUTH_W + k * 2.4
    mouth_h = MOUTH_H + k * 3.2
    mouth_x = MOUTH_X + MOUTH_W / 2 - mouth_w / 2  # keeps mouth centered at x=80
    mouth_y = MOUTH_CENTER_Y - mouth_h / 2  # keeps mouth centered at y=86.5
    mouth_rx = max(1.0, MOUTH_RX - k * 0.65)

    left_x = EYE_LEFT_X + shift
    right_x = EYE_RIGHT_X - shift

    return {
        "left": {
            "x": left_x, "y": eye_y, "w": EYE_W, "h": eye_h,
            "cx": left_x + EYE_W / 2, "cy": eye_y + eye_h / 2,
            "angle": angle, "fill": color,
        },
        "right": {
            "x": right_x, "y": eye_y, "w": EYE_W, "h": eye_h,
            "cx": right_x + EYE_W / 2, "cy": eye_y + eye_h / 2,
            "angle": -angle, "fill": color,
        },
        "mouth": {
            "x": mouth_x, "y": mouth_y, "w": mouth_w, "h": mouth_h,
            "rx": mouth_rx, "fill": color,
        },
    }


def _key_times():
    kt = []
    for i in range(NUM_POSES):
        kt.append(i / NUM_POSES)
        kt.append(i / NUM_POSES + HOLD_FRACTION)
    kt.append(1.0)
    return ";".join(f"{v:.4f}" for v in kt)


def _key_splines():
    return ";".join(["0 0 1 1;0.45 0 0.55 1"] * NUM_POSES)


def _values(entries):
    """entries: list of NUM_POSES formatted strings -> 21-entry SMIL values list
    (each stage held twice, looping back to stage 0 at the end)."""
    vals = []
    for v in entries:
        vals.append(v)
        vals.append(v)
    vals.append(entries[0])
    return ";".join(vals)


def _animate(attr_name, entries, fmt="{:.3f}"):
    values = _values([fmt.format(v) if not isinstance(v, str) else v for v in entries])
    return (
        f'<animate attributeName="{attr_name}" begin="0s" dur="{TOTAL_DURATION:g}s" '
        f'repeatCount="indefinite" calcMode="spline" keyTimes="{_key_times()}" '
        f'keySplines="{_key_splines()}" values="{values}"/>'
    )


def _animate_transform(entries):
    values = _values(entries)
    return (
        '<animateTransform attributeName="transform" type="rotate" begin="0s" '
        f'dur="{TOTAL_DURATION:g}s" repeatCount="indefinite" calcMode="spline" '
        f'keyTimes="{_key_times()}" keySplines="{_key_splines()}" values="{values}"/>'
    )


def build_svg():
    stages = [pose_params(k) for k in range(NUM_POSES)]

    def eye_markup(side, base_x):
        rows = [s[side] for s in stages]
        rotate_values = [f"{r['angle']:.3f} {r['cx']:.3f} {r['cy']:.3f}" for r in rows]
        return f"""      <rect id="{side}-eye" class="eye" x="{base_x}" y="{EYE_Y}" width="{EYE_W}" height="{EYE_H}" rx="{EYE_RX}" fill="{rows[0]['fill']}" stroke="#000000" stroke-width="4.5" stroke-linejoin="round">
        {_animate("x", [r['x'] for r in rows])}
        {_animate("y", [r['y'] for r in rows])}
        {_animate("height", [r['h'] for r in rows])}
        {_animate("fill", [r['fill'] for r in rows], fmt="{}")}
        {_animate_transform(rotate_values)}
      </rect>"""

    mouth_rows = [s["mouth"] for s in stages]
    mouth_markup = f"""      <rect id="mouth" x="{MOUTH_X}" y="{mouth_rows[0]['y']:.3f}" width="{MOUTH_W}" height="{MOUTH_H}" rx="{MOUTH_RX}" fill="{mouth_rows[0]['fill']}" stroke="#000000" stroke-width="4.5" stroke-linejoin="round">
        {_animate("x", [r['x'] for r in mouth_rows])}
        {_animate("y", [r['y'] for r in mouth_rows])}
        {_animate("width", [r['w'] for r in mouth_rows])}
        {_animate("height", [r['h'] for r in mouth_rows])}
        {_animate("rx", [r['rx'] for r in mouth_rows])}
        {_animate("fill", [r['fill'] for r in mouth_rows], fmt="{}")}
      </rect>"""

    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="160" height="128" viewBox="0 0 160 128">
  <rect width="160" height="128" rx="16" fill="#0d1117"/>
  <g id="hero-face" transform="translate(0,0)">
{eye_markup('left', EYE_LEFT_X)}
{eye_markup('right', EYE_RIGHT_X)}
{mouth_markup}
  </g>
</svg>
"""


def generate():
    svg = build_svg()
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        f.write(svg)
    print("Wrote", OUTPUT_PATH)


if __name__ == "__main__":
    generate()
