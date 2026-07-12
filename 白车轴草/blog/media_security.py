import os
import warnings
from io import BytesIO

from PIL import Image, UnidentifiedImageError


MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_IMAGE_DIMENSION = 12000
MAX_IMAGE_PIXELS = 40_000_000
MAX_REMOTE_IMAGE_BYTES = 8 * 1024 * 1024
SUPPORTED_IMAGE_FORMATS = {
    'JPEG': 'jpg',
    'PNG': 'png',
    'WEBP': 'webp',
}


def validate_image_dimensions(width, height):
    if (
        width < 1
        or height < 1
        or width > MAX_IMAGE_DIMENSION
        or height > MAX_IMAGE_DIMENSION
        or width * height > MAX_IMAGE_PIXELS
    ):
        raise ValueError('图片像素尺寸过大，请缩小后重试。')


def inspect_image(image_source):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error', Image.DecompressionBombWarning)
            with Image.open(image_source) as image:
                validate_image_dimensions(*image.size)
                image_extension = SUPPORTED_IMAGE_FORMATS.get(image.format)
                if image_extension is None:
                    raise ValueError('不支持这种图片格式。')
                image.verify()
    except (
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
        UnidentifiedImageError,
        OSError,
        ValueError,
    ) as error:
        raise ValueError('请上传有效且尺寸合理的图片文件。') from error
    return image_extension


def validate_image_bytes(image_bytes):
    if len(image_bytes) > MAX_IMAGE_UPLOAD_BYTES:
        raise ValueError('图片文件不能超过 5MB。')
    return inspect_image(BytesIO(image_bytes))


def validate_uploaded_image(uploaded_file):
    if uploaded_file.size > MAX_IMAGE_UPLOAD_BYTES:
        raise ValueError('图片文件不能超过 5MB。')
    try:
        uploaded_file.seek(0)
        image_extension = inspect_image(uploaded_file)
        uploaded_file.seek(0)
    except (OSError, ValueError):
        try:
            uploaded_file.seek(0)
        except OSError:
            pass
        raise
    original_file_stem = os.path.splitext(os.path.basename(uploaded_file.name))[0]
    uploaded_file.name = f'{original_file_stem}.{image_extension}'
    return uploaded_file


def read_limited_response(response, maximum_bytes=MAX_REMOTE_IMAGE_BYTES):
    content_length = response.headers.get('Content-Length')
    if content_length:
        try:
            if int(content_length) > maximum_bytes:
                raise ValueError('远程图片超过允许大小。')
        except ValueError as error:
            if str(error) == '远程图片超过允许大小。':
                raise

    response_bytes = bytearray()
    while True:
        response_chunk = response.read(64 * 1024)
        if not response_chunk:
            break
        response_bytes.extend(response_chunk)
        if len(response_bytes) > maximum_bytes:
            raise ValueError('远程图片超过允许大小。')
    image_bytes = bytes(response_bytes)
    validate_image_bytes(image_bytes)
    return image_bytes


def has_valid_audio_signature(file_source, audio_extension):
    should_close = False
    if isinstance(file_source, (str, os.PathLike)):
        file_source = open(file_source, 'rb')
        should_close = True
    try:
        original_position = file_source.tell()
        file_source.seek(0)
        header = file_source.read(16)
        file_source.seek(original_position)
    finally:
        if should_close:
            file_source.close()

    if audio_extension == '.flac':
        return header.startswith(b'fLaC')
    if audio_extension == '.wav':
        return header.startswith(b'RIFF') and header[8:12] == b'WAVE'
    if audio_extension == '.ogg':
        return header.startswith(b'OggS')
    if audio_extension == '.m4a':
        return len(header) >= 12 and header[4:8] == b'ftyp'
    if audio_extension == '.mp3':
        return header.startswith(b'ID3') or (
            len(header) >= 2 and header[0] == 0xFF and header[1] & 0xE0 == 0xE0
        )
    return False
