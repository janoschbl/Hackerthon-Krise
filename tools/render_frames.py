"""Pre-render the 10 poses + 10 morph-transitions + 10 idle loops from
assets/animation.svg into PNG sequences under assets/frames/, since the Pi's
TFT can't play the SVG's native SMIL/CSS animations directly.

Run once on a development machine (NOT on the Pi):
    pip install -r tools/requirements.txt
    playwright install chromium
    python tools/render_frames.py
"""

import math
import os
import sys

from playwright.sync_api import sync_playwright

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generate_svg import pose_params  # noqa: E402

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SVG_PATH = os.path.join(ROOT_DIR, "assets", "animation.svg")
OUTPUT_DIR = os.path.join(ROOT_DIR, "assets", "frames")

NUM_POSES = 10
TOTAL_DURATION = 16.0  # matches dur="16s" in the SVG's <animate> elements
SEGMENT_DURATION = TOTAL_DURATION / NUM_POSES  # 1.6s per pose
HOLD_DURATION = 1.0  # static portion of each segment (see keyTimes spacing)
TRANSITION_DURATION = SEGMENT_DURATION - HOLD_DURATION  # 0.6s morph
TRANSITION_FPS = 15
VIEWPORT_SCALE = 4  # render at higher res, display lib downsamples on load

# Idle loop played while resting on a pose: the SVG's hold phase is perfectly
# static, so idle motion (blink + bob) is synthesized here by poking the
# eye/face DOM nodes directly after freezing the SMIL clock. Character scales
# with mood: calm stages (low k) get a slow single blink and a gentle single
# bob; furious stages (high k) get quick, sharp multi-blinks, more bounces
# per loop, and a jittery tremor.
IDLE_FPS = 20
IDLE_LOOP_SECONDS = 4.0
BLINK_CLOSE_FACTOR = 1.0  # how much the eye height shrinks at full blink (1 = fully closed)
TREMOR_FREQ = 6.0  # Hz, high-frequency shake used only at angrier stages

# Rare, one-off "personality" animations that occasionally interrupt the
# plain blink+bob loop - currently only while resting at the calmest stage
# (0), where the character has room to be playful.
IDLE_EVENT_TYPES = {0: ["wink", "look"]}
EVENT_PAN_AMOUNT = 6.0  # how far the eyes shift sideways during the "look" event


def idle_params(k):
    """Per-stage idle character: calm/slow at k=0 -> twitchy/sharp at k=9."""
    t = k / (NUM_POSES - 1)
    blink_count = 1 + round(3 * t)  # 1 slow blink -> 4 quick blinks per loop
    return {
        "blink_centers": [
            IDLE_LOOP_SECONDS * (i + 0.5) / blink_count for i in range(blink_count)
        ],
        "blink_duration": 0.34 - 0.16 * t,  # 0.34s lazy blink -> 0.18s sharp blink
        "bob_amplitude": 0.5 + 0.7 * t,  # 0.5 -> 1.2, calm sway -> agitated bounce
        "bob_cycles": 1 + round(2 * t),  # 1 -> 3 bounces per loop
        "tremor_amplitude": 1.4 * t,  # 0 -> 1.4, high-freq shake only kicks in when angry
    }


def _ease(d, half):
    """Cosine falloff (1 at d=0, 0 at d>=half) - shared shape for blinks/pulses."""
    if d >= half:
        return 0.0
    return 0.5 * (1 + math.cos(math.pi * d / half))


def _interp(waypoints, t):
    """Piecewise cosine-eased interpolation through (time, value) waypoints."""
    for (t0, v0), (t1, v1) in zip(waypoints, waypoints[1:]):
        if t0 <= t <= t1:
            if t1 == t0:
                return v1
            frac = (t - t0) / (t1 - t0)
            eased = 0.5 - 0.5 * math.cos(math.pi * frac)
            return v0 + (v1 - v0) * eased
    return waypoints[-1][1]


def _build_wink_frames():
    """One eye (left) closes briefly while the other stays open."""
    duration = 1.0
    count = round(duration * IDLE_FPS)
    center, half = 0.4, 0.22
    frames = []
    for k in range(count):
        t = k / IDLE_FPS
        amount = _ease(abs(t - center), half)
        frames.append({"blink": {"left": amount, "right": 0.0}, "pan": 0.0})
    return frames


def _build_look_frames():
    """Both eyes glance left, then right, then settle back to center."""
    duration = 1.6
    count = round(duration * IDLE_FPS)
    waypoints = [
        (0.0, 0.0), (0.35, -EVENT_PAN_AMOUNT), (0.65, -EVENT_PAN_AMOUNT),
        (1.05, EVENT_PAN_AMOUNT), (1.3, EVENT_PAN_AMOUNT), (1.6, 0.0),
    ]
    frames = []
    for k in range(count):
        t = k / IDLE_FPS
        frames.append({"blink": 0.0, "pan": _interp(waypoints, t)})
    return frames


_EVENT_BUILDERS = {"wink": _build_wink_frames, "look": _build_look_frames}

with open(SVG_PATH, "r", encoding="utf-8") as f:
    SVG_MARKUP = f.read()

HTML_TEMPLATE = """<!doctype html>
<html><head><meta charset="utf-8"><style>
  html,body {{ margin:0; padding:0; background:#ffffff; }}
</style></head>
<body>{svg}</body></html>"""


def _set_time(page, seconds):
    # SVGSVGElement.setCurrentTime() drives the SMIL clock deterministically;
    # document.getAnimations() covers the two CSS @keyframes overlays.
    page.evaluate(
        """(t) => {
            document.querySelectorAll('svg').forEach((svg) => {
                if (svg.pauseAnimations) svg.pauseAnimations();
                if (svg.setCurrentTime) svg.setCurrentTime(t);
            });
            document.getAnimations().forEach((a) => {
                a.pause();
                a.currentTime = t * 1000;
            });
        }""",
        seconds,
    )


def _prepare_idle_eyes(page, pose_index):
    # Freeze this pose's eye geometry/color into plain attributes (removing
    # the SMIL <animate>/<animateTransform> children) so later per-frame
    # setAttribute calls for blink/bob actually stick.
    params = pose_params(pose_index)
    page.evaluate(
        """(params) => {
            ['left', 'right'].forEach((side) => {
                const el = document.getElementById(`${side}-eye`);
                el.querySelectorAll('animate, animateTransform').forEach((a) => a.remove());
                const p = params[side];
                el.setAttribute('x', p.x);
                el.setAttribute('y', p.y);
                el.setAttribute('width', p.w);
                el.setAttribute('height', p.h);
                el.setAttribute('fill', p.fill);
            });
            const face = document.getElementById('hero-face');
            window.__heroBaseTransform = face.getAttribute('transform') || '';
        }""",
        {"left": params["left"], "right": params["right"]},
    )
    return params


def _set_idle_frame(page, params, blink, bob, x_tremor=0.0, eye_pan=0.0):
    """blink: a single float (both eyes) or {'left':.., 'right':..} for asymmetric events."""
    if isinstance(blink, dict):
        scale_left = 1 - blink["left"] * BLINK_CLOSE_FACTOR
        scale_right = 1 - blink["right"] * BLINK_CLOSE_FACTOR
    else:
        scale_left = scale_right = 1 - blink * BLINK_CLOSE_FACTOR
    page.evaluate(
        """([params, scaleLeft, scaleRight, bob, xTremor, eyePan]) => {
            const scales = {left: scaleLeft, right: scaleRight};
            ['left', 'right'].forEach((side) => {
                const el = document.getElementById(`${side}-eye`);
                const p = params[side];
                const scaleY = scales[side];
                el.setAttribute(
                    'transform',
                    `translate(${eyePan} 0) rotate(${p.angle} ${p.cx} ${p.cy}) translate(${p.cx} ${p.cy}) scale(1 ${scaleY}) translate(${-p.cx} ${-p.cy})`
                );
            });
            const face = document.getElementById('hero-face');
            face.setAttribute('transform', window.__heroBaseTransform + ` translate(${xTremor} ${bob})`);
        }""",
        [{"left": params["left"], "right": params["right"]}, scale_left, scale_right, bob, x_tremor, eye_pan],
    )


def _blink_amount(t_local, centers, duration):
    half = duration / 2
    amount = 0.0
    for center in centers:
        amount = max(amount, _ease(abs(t_local - center), half))
    return amount


def render():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(
            viewport={"width": 160 * VIEWPORT_SCALE, "height": 128 * VIEWPORT_SCALE}
        )
        page.set_content(HTML_TEMPLATE.format(svg=SVG_MARKUP))
        page.wait_for_timeout(100)  # let layout/styles settle before capturing

        svg_element = page.query_selector("svg")

        # Static poses, captured mid-hold so we're not near a transition edge.
        for i in range(NUM_POSES):
            t = i * SEGMENT_DURATION + HOLD_DURATION / 2
            _set_time(page, t)
            svg_element.screenshot(path=os.path.join(OUTPUT_DIR, f"pose_{i:02d}.png"))
            print(f"pose_{i:02d}.png  (t={t:.3f}s)")

        # Morph from pose i to pose (i+1) % NUM_POSES.
        frame_count = max(2, round(TRANSITION_DURATION * TRANSITION_FPS))
        for i in range(NUM_POSES):
            segment_dir = os.path.join(OUTPUT_DIR, f"transition_{i:02d}")
            os.makedirs(segment_dir, exist_ok=True)
            start = i * SEGMENT_DURATION + HOLD_DURATION
            for k in range(frame_count):
                t = start + TRANSITION_DURATION * k / (frame_count - 1)
                _set_time(page, t)
                svg_element.screenshot(path=os.path.join(segment_dir, f"frame_{k:03d}.png"))
            print(f"transition_{i:02d}/  ({frame_count} frames)")

        # Idle loop per pose (blink + gentle bob). Destructive DOM edits, so
        # reload a fresh page for each pose before re-seeking to its hold time.
        idle_frame_count = round(IDLE_LOOP_SECONDS * IDLE_FPS)
        for i in range(NUM_POSES):
            page.set_content(HTML_TEMPLATE.format(svg=SVG_MARKUP))
            page.wait_for_timeout(50)
            svg_element = page.query_selector("svg")

            t_pose = i * SEGMENT_DURATION + HOLD_DURATION / 2
            _set_time(page, t_pose)
            pose_p = _prepare_idle_eyes(page, i)
            ip = idle_params(i)

            idle_dir = os.path.join(OUTPUT_DIR, f"idle_{i:02d}")
            os.makedirs(idle_dir, exist_ok=True)
            for k in range(idle_frame_count):
                t_local = IDLE_LOOP_SECONDS * k / idle_frame_count
                blink_amount = _blink_amount(t_local, ip["blink_centers"], ip["blink_duration"])
                bob = ip["bob_amplitude"] * math.sin(
                    2 * math.pi * ip["bob_cycles"] * t_local / IDLE_LOOP_SECONDS
                )
                tremor = ip["tremor_amplitude"] * math.sin(2 * math.pi * TREMOR_FREQ * t_local)
                _set_idle_frame(page, pose_p, blink_amount, bob, x_tremor=tremor)
                svg_element.screenshot(path=os.path.join(idle_dir, f"frame_{k:03d}.png"))
            print(f"idle_{i:02d}/  ({idle_frame_count} frames)")

            # Rare one-off "personality" events for poses that have them.
            for event_name in IDLE_EVENT_TYPES.get(i, []):
                event_dir = os.path.join(OUTPUT_DIR, f"idle_{i:02d}_event_{event_name}")
                os.makedirs(event_dir, exist_ok=True)
                event_frames = _EVENT_BUILDERS[event_name]()
                for k, ef in enumerate(event_frames):
                    t_local = k / IDLE_FPS
                    bob = 0.5 * math.sin(2 * math.pi * t_local / IDLE_LOOP_SECONDS)
                    _set_idle_frame(page, pose_p, ef["blink"], bob, eye_pan=ef["pan"])
                    svg_element.screenshot(path=os.path.join(event_dir, f"frame_{k:03d}.png"))
                print(f"idle_{i:02d}_event_{event_name}/  ({len(event_frames)} frames)")

        browser.close()

    print("Done. Frames written to", OUTPUT_DIR)


if __name__ == "__main__":
    render()
