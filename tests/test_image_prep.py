import io

from PIL import Image

from app.services.image_prep import prepare_for_model


def _jpeg(width: int, height: int, orientation: int | None = None) -> bytes:
    image = Image.new("RGB", (width, height), "white")
    out = io.BytesIO()
    if orientation:
        exif = Image.Exif()
        exif[0x0112] = orientation
        image.save(out, format="JPEG", exif=exif)
    else:
        image.save(out, format="JPEG")
    return out.getvalue()


def test_large_photo_is_scaled_to_max_side() -> None:
    out = Image.open(io.BytesIO(prepare_for_model(_jpeg(3000, 4000), 1024)))
    assert out.size == (768, 1024) and out.format == "JPEG"


def test_small_photo_keeps_its_size() -> None:
    assert Image.open(io.BytesIO(prepare_for_model(_jpeg(600, 800), 1024))).size == (600, 800)


def test_exif_rotation_is_applied() -> None:
    # Orientation 6 = rotate 90°: a 400x300 sensor image is a portrait photo.
    assert Image.open(io.BytesIO(prepare_for_model(_jpeg(400, 300, orientation=6), 1024))).size == (300, 400)


def test_png_with_alpha_becomes_rgb_jpeg() -> None:
    out = io.BytesIO()
    Image.new("RGBA", (50, 50)).save(out, format="PNG")
    assert Image.open(io.BytesIO(prepare_for_model(out.getvalue(), 1024))).mode == "RGB"
