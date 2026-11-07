"""Photo processing: one upload becomes display, blurred background and thumbnail JPEGs."""

import io
import warnings
from dataclasses import dataclass

import pillow_heif
from PIL import Image, ImageCms, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError

pillow_heif.register_heif_opener()

MAX_UPLOAD_BYTES = 40 * 1024 * 1024  # checked by the caller
Image.MAX_IMAGE_PIXELS = 80_000_000

DISPLAY_BOX = (1080, 1920)
THUMB_BOX = (360, 360)
BACKGROUND_SIZE = (1080, 1920)
BLUR_RADIUS = 40
BLUR_DOWNSCALE = 4  # blur at 1/4 size (radius scaled to match): much faster on a Pi 4
BACKGROUND_BRIGHTNESS = 0.55
MATTE = (0x11, 0x11, 0x11)


class UnsupportedImage(Exception):
    """Upload cannot be processed; the message is safe to show to the user."""


@dataclass(frozen=True)
class ProcessedImage:
    display: bytes
    background: bytes
    thumb: bytes
    landscape: bool


def _to_rgb(img: Image.Image) -> Image.Image:
    if img.mode in ("I;16", "I;16L", "I;16B", "I"):
        img = img.point(lambda v: v / 256, "L")  # 16-bit to 8-bit
    has_alpha = img.mode in ("RGBA", "LA", "PA") or "transparency" in img.info
    if has_alpha:
        rgba = img.convert("RGBA")
        base = Image.new("RGBA", rgba.size, MATTE + (255,))
        return Image.alpha_composite(base, rgba).convert("RGB")
    if img.mode == "CMYK" and "icc_profile" in img.info:
        return _icc_to_srgb(img)
    return img.convert("RGB")


def _icc_to_srgb(img: Image.Image) -> Image.Image:
    try:
        src = ImageCms.ImageCmsProfile(io.BytesIO(img.info["icc_profile"]))
        return ImageCms.profileToProfile(img, src, ImageCms.createProfile("sRGB"), outputMode="RGB")
    except (ImageCms.PyCMSError, OSError):
        return img.convert("RGB")


def _fit(img: Image.Image, box: tuple[int, int]) -> Image.Image:
    """Shrink to fit inside box, keeping aspect ratio; never upscale."""
    out = img.copy()
    out.thumbnail(box, Image.Resampling.LANCZOS)
    return out


def _background(img: Image.Image) -> Image.Image:
    cover = ImageOps.fit(img, BACKGROUND_SIZE, Image.Resampling.LANCZOS)  # upscales if needed
    small_size = (BACKGROUND_SIZE[0] // BLUR_DOWNSCALE, BACKGROUND_SIZE[1] // BLUR_DOWNSCALE)
    small = cover.resize(small_size, Image.Resampling.LANCZOS)
    small = small.filter(ImageFilter.GaussianBlur(BLUR_RADIUS / BLUR_DOWNSCALE))
    blurred = small.resize(BACKGROUND_SIZE, Image.Resampling.BICUBIC)
    return ImageEnhance.Brightness(blurred).enhance(BACKGROUND_BRIGHTNESS)


def _jpeg(img: Image.Image, quality: int) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)  # no exif=, no icc_profile: metadata stripped
    return buf.getvalue()


def process_image(data: bytes) -> ProcessedImage:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as img:
                img.seek(0)  # animated/multi-frame: first frame only
                img.load()  # truncated files fail here
                img = ImageOps.exif_transpose(img)
                rgb = _to_rgb(img)
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise UnsupportedImage("Image is too large") from exc
    except UnidentifiedImageError as exc:
        raise UnsupportedImage("Not a supported image") from exc
    except (OSError, SyntaxError, ValueError, EOFError) as exc:
        raise UnsupportedImage("Image could not be read") from exc

    display = _fit(rgb, DISPLAY_BOX)
    thumb = _fit(rgb, THUMB_BOX)
    return ProcessedImage(
        display=_jpeg(display, 85),
        background=_jpeg(_background(rgb), 80),
        thumb=_jpeg(thumb, 80),
        landscape=rgb.width > rgb.height,
    )
