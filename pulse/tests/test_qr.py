from pulse.qr import render_qr_svg


def test_render_qr_svg_returns_svg_markup_without_a_fixed_physical_size():
    svg = render_qr_svg("https://partypuls.example.com/")

    assert svg.startswith("<svg")
    assert svg.endswith("</svg>")
    # The library's default mm width/height must be stripped so the sign template can
    # size the code with CSS instead -- see render_qr_svg()'s docstring.
    assert "mm" not in svg
    assert "viewBox" in svg


def test_render_qr_svg_is_deterministic_for_the_same_input():
    assert render_qr_svg("https://partypuls.example.com/") == render_qr_svg("https://partypuls.example.com/")


def test_render_qr_svg_differs_for_different_input():
    assert render_qr_svg("https://a.example.com/") != render_qr_svg("https://b.example.com/")
