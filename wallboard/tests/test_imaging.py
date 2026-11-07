import io
import random

import pytest
from PIL import Image, ImageCms

from wallboard.imaging import UnsupportedImage, process_image


def _encode(img: Image.Image, fmt: str = "JPEG", **kw) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kw)
    return buf.getvalue()


def _open(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data))


def _jpeg_with_orientation(size, orientation: int) -> bytes:
    exif = Image.Exif()
    exif[0x0112] = orientation
    return _encode(Image.new("RGB", size, (90, 120, 200)), exif=exif.tobytes())


def test_orientation_6_rotated():
    out = process_image(_jpeg_with_orientation((4000, 3000), 6))
    assert _open(out.display).size == (1080, 1440)
    assert out.landscape is False


def test_orientation_8_portrait():
    out = process_image(_jpeg_with_orientation((800, 600), 8))
    assert _open(out.display).size == (600, 800)
    assert out.landscape is False


def test_no_gps_in_output():
    exif = Image.Exif()
    exif[0x0112] = 6
    exif[0x8825] = {1: "N", 2: (50.0, 5.0, 1.0)}
    data = _encode(Image.new("RGB", (400, 300), "red"), exif=exif.tobytes())
    assert 0x8825 in _open(data).getexif()
    out = process_image(data)
    for blob in (out.display, out.background, out.thumb):
        img = _open(blob)
        assert 0x8825 not in img.getexif()
        assert 0x0112 not in img.getexif()
        assert "icc_profile" not in img.info


def test_png_alpha():
    img = Image.new("RGBA", (200, 100), (0, 0, 0, 0))
    out = process_image(_encode(img, "PNG"))
    disp = _open(out.display)
    assert disp.mode == "RGB"
    assert all(abs(c - 0x11) <= 3 for c in disp.getpixel((100, 50)))
    assert out.landscape is True


def test_palette_png_with_transparency():
    img = Image.new("P", (120, 90), 0)
    img.putpalette([255, 0, 0] + [0, 0, 0] * 255)
    out = process_image(_encode(img, "PNG", transparency=0))
    assert all(abs(c - 0x11) <= 3 for c in _open(out.display).getpixel((60, 45)))


@pytest.mark.parametrize("mode,fmt", [("L", "PNG"), ("LA", "PNG"), ("CMYK", "JPEG"), ("I;16", "PNG")])
def test_other_modes(mode, fmt):
    out = process_image(_encode(Image.new(mode, (100, 60)), fmt))
    assert _open(out.display).mode == "RGB"


def test_heic_accepted(heic_bytes):
    out = process_image(heic_bytes)
    assert _open(out.display).size == (64, 48)
    assert out.landscape is True


def test_garbage_rejected():
    with pytest.raises(UnsupportedImage, match="Not a supported image"):
        process_image(b"not an image")


def test_truncated_jpeg_rejected():
    rnd = random.Random(1)
    img = Image.frombytes("RGB", (300, 300), rnd.randbytes(270000))
    data = _encode(img)
    with pytest.raises(UnsupportedImage, match="Image could not be read"):
        process_image(data[: len(data) // 2])


def test_pixel_limit_rejected():
    big = _encode(Image.new("1", (20000, 10000)), "PNG")  # 200 MP
    with pytest.raises(UnsupportedImage, match="Image is too large"):
        process_image(big)


def test_background_size():
    out = process_image(_encode(Image.new("RGB", (4000, 3000), "blue")))
    assert _open(out.background).size == (1080, 1920)
    assert _open(out.thumb).size == (360, 270)
    assert out.landscape is True


def test_background_is_darkened():
    out = process_image(_encode(Image.new("RGB", (800, 600), (200, 200, 200))))
    px = _open(out.background).getpixel((540, 960))
    assert all(abs(c - 110) <= 4 for c in px)


def test_tiny_image_not_upscaled():
    out = process_image(_encode(Image.new("RGB", (100, 80), "green"), "PNG"))
    assert _open(out.display).size == (100, 80)
    assert _open(out.thumb).size == (100, 80)
    assert _open(out.background).size == (1080, 1920)


def test_animated_gif_uses_first_frame():
    frames = [Image.new("RGB", (80, 60), c) for c in ("red", "blue")]
    data = _encode(frames[0], "GIF", save_all=True, append_images=frames[1:])
    r, _, b = _open(process_image(data).display).getpixel((40, 30))
    assert r > 200 and b < 60


def _swapped_primaries_profile() -> bytes:
    """A valid non-sRGB RGB profile: sRGB with its red and green colorants swapped.

    Pillow cannot create a wide-gamut (Display P3) profile, so edit the colorant tags of the
    generated sRGB profile instead.
    """
    raw = bytearray(ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes())
    count = int.from_bytes(raw[128:132], "big")
    tags = {}
    for i in range(count):
        sig, off, size = (raw[132 + 12 * i : 144 + 12 * i][:4], *(
            int.from_bytes(raw[136 + 12 * i + j : 140 + 12 * i + j], "big") for j in (0, 4)))
        tags[bytes(sig)] = (off, size)
    (ro, rs), (go, gs) = tags[b"rXYZ"], tags[b"gXYZ"]
    assert rs == gs and ro != go
    raw[ro : ro + rs], raw[go : go + gs] = raw[go : go + gs], raw[ro : ro + rs]
    return bytes(raw)


def test_icc_profile_converted_and_stripped():
    plain = process_image(_encode(Image.new("RGB", (200, 150), (200, 30, 30)), "PNG"))
    tagged = process_image(
        _encode(Image.new("RGB", (200, 150), (200, 30, 30)), "PNG", icc_profile=_swapped_primaries_profile())
    )
    p, t = _open(plain.display).getpixel((100, 75)), _open(tagged.display).getpixel((100, 75))
    assert max(abs(a - b) for a, b in zip(p, t)) > 50
    for blob in (tagged.display, tagged.background, tagged.thumb):
        assert "icc_profile" not in _open(blob).info


def test_broken_icc_profile_falls_back():
    out = process_image(_encode(Image.new("RGB", (50, 40), "red"), "PNG", icc_profile=b"garbage"))
    assert _open(out.display).getpixel((25, 20))[0] > 200


@pytest.mark.filterwarnings("ignore::PIL.Image.DecompressionBombWarning")
def test_pixel_limit_between_1x_and_2x_rejected():
    data = _encode(Image.new("1", (9000, 9000)), "PNG")  # 81 MP, over the 80 MP limit
    with pytest.raises(UnsupportedImage, match="Image is too large"):
        process_image(data)


def test_large_jpeg_decoded_reduced():
    out = process_image(_encode(Image.new("RGB", (6000, 4000), "gray"), quality=50))
    assert _open(out.display).size == (1080, 720)
