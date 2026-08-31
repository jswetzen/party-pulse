"""QR code rendering for the printable/big-screen sign — see qr_page() in
pulse/views.py and PLAN.md "Guest entry QR sign".

Server-side generation (the `qrcode` package, pure Python — no PIL/Pillow) rather than a
JS QR library or a third-party image API: the sign has to work with zero network trips
(hung on a wall, or the venue's internet is down) and a single well-tested encoder beats
hand-rolling Reed-Solomon in JS. SVG output only — this is a print/display sign, not
something guests interact with, so there is no case for a raster format here.
"""

import re

import qrcode
import qrcode.image.svg

# 4-module quiet zone is the QR spec's own recommendation for reliable scanning (a phone
# camera in a dim, crowded room is a worse-case reader than a desk scanner) -- worth the
# extra margin even though the sign template also pads the code with real CSS padding.
_BORDER_MODULES = 4

_MM_DIMENSION_RE = re.compile(r' (?:width|height)="[\d.]+mm"')


def render_qr_svg(data: str) -> str:
    """Render `data` (typically the guest entry URL) as a standalone <svg> markup
    string: black modules on a transparent background, sized only by its viewBox (the
    mm width/height the library bakes in are stripped) so it drops into a template and
    scales with CSS instead of being pinned to a physical size chosen at generation
    time. Black-on-transparent, not the site's rose/gold palette, is deliberate --
    scanners need real contrast, so this one piece stays print-safe over on-brand; see
    qr_page.html for how it sits on a white card to get that contrast either way."""
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, border=_BORDER_MODULES)
    svg = img.to_string(encoding="unicode")
    return _MM_DIMENSION_RE.sub("", svg, count=2)
