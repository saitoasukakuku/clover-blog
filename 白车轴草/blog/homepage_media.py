import hashlib
import json
import os
import re
from urllib.parse import quote

from django.conf import settings
from django.http import Http404
from django.urls import reverse
from PIL import Image, ImageOps, UnidentifiedImageError


HOMEPAGE_IMAGE_DIR_NAME = 'index_img'
HOMEPAGE_IMAGE_CACHE_DIR_NAME = 'index_img_cache'
HOMEPAGE_IMAGE_COPY_FILE_NAME = 'index_img_copy.json'
HOMEPAGE_IMAGE_SETTINGS_FILE_NAME = 'index_img_settings.json'
HOMEPAGE_ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
HOMEPAGE_IMAGE_CACHE_MAX_SIZE = (1920, 1280)
HOMEPAGE_IMAGE_CACHE_QUALITY = 82
HOMEPAGE_COPY_FIELDS = ('kicker', 'headline', 'lead', 'card_title', 'card_text')
HOMEPAGE_THEME_PRESETS = [
    {
        'accent': '#5f8fc8',
        'accent_strong': '#2c5f96',
        'accent_soft': 'rgba(95, 143, 200, .18)',
    },
    {
        'accent': '#c8893e',
        'accent_strong': '#8a5726',
        'accent_soft': 'rgba(200, 137, 62, .20)',
    },
    {
        'accent': '#d06a54',
        'accent_strong': '#9a3e31',
        'accent_soft': 'rgba(208, 106, 84, .20)',
    },
    {
        'accent': '#8aa0b8',
        'accent_strong': '#4f657c',
        'accent_soft': 'rgba(138, 160, 184, .22)',
    },
]
HOMEPAGE_COPY_PRESETS = [
    {
        'kicker': '今日风景',
        'headline': '在新的画面里，慢慢打开今天的阅读。',
        'lead': '首页会从站点图库里轮换背景，先给今天的阅读留出一个安静开场。',
        'card_title': '今天的阅读氛围',
        'card_text': '背景负责定调，最近更新和探索入口集中放在下方。',
        'moods': ['阅读', '随笔', '片刻'],
    },
    {
        'kicker': '小站入口',
        'headline': '换一张背景，也换一种进入网站的心情。',
        'lead': '每次停留都可以从不同画面开始，真正的内容仍然清楚地排在下方。',
        'card_title': '清爽一点的首页',
        'card_text': '首屏只留下氛围和主要动作，具体路径交给下方探索区。',
        'moods': ['入口', '近况', '回看'],
    },
    {
        'kicker': '白车轴草',
        'headline': '把看到的、想到的和正在做的事，都留在这里。',
        'lead': '背景只是开场，真正的内容仍然是生活记录、技术笔记和那些值得回看的片段。',
        'card_title': '新的阅读开场',
        'card_text': '从一个安静的开场进入文章，再把喜欢的主题慢慢收藏起来。',
        'moods': ['生活', '技术', '记录'],
    },
    {
        'kicker': '随手翻看',
        'headline': '不急着搜索，也可以顺着画面随手翻一翻。',
        'lead': '如果没有明确目标，就先从最近更新看起，再慢慢找到感兴趣的方向。',
        'card_title': '顺着兴趣继续',
        'card_text': '首页先保留停留感，检索和回看入口集中在后面的内容区。',
        'moods': ['停留', '探索', '慢读'],
    },
]


def get_homepage_image_settings_file_path():
    return os.path.join(settings.MEDIA_ROOT, HOMEPAGE_IMAGE_SETTINGS_FILE_NAME)


def normalize_homepage_image_settings(raw_image_settings):
    if not isinstance(raw_image_settings, dict):
        return {}

    raw_images = raw_image_settings.get('images')
    if not isinstance(raw_images, dict):
        return {}

    settings_by_file_name = {}
    for image_file_name, raw_image_config in raw_images.items():
        if not isinstance(image_file_name, str):
            continue
        if not is_homepage_image_file_name_allowed(image_file_name):
            continue
        if not isinstance(raw_image_config, dict):
            continue

        sort_order = raw_image_config.get('sort_order')
        if sort_order in ('', None):
            normalized_sort_order = None
        else:
            try:
                normalized_sort_order = int(sort_order)
            except (TypeError, ValueError):
                normalized_sort_order = None

        settings_by_file_name[image_file_name] = {
            'sort_order': normalized_sort_order,
            'is_hidden': bool(raw_image_config.get('is_hidden')),
        }
    return settings_by_file_name


def get_homepage_image_settings_by_file_name():
    try:
        with open(get_homepage_image_settings_file_path(), 'r', encoding='utf-8') as settings_file:
            raw_image_settings = json.load(settings_file)
    except (OSError, json.JSONDecodeError):
        return {}
    return normalize_homepage_image_settings(raw_image_settings)


def save_homepage_image_settings_by_file_name(settings_by_file_name):
    normalized_settings_by_file_name = normalize_homepage_image_settings({
        'images': settings_by_file_name,
    })
    saved_images = {}
    for image_file_name, image_settings in normalized_settings_by_file_name.items():
        if image_settings['sort_order'] is None and not image_settings['is_hidden']:
            continue
        saved_images[image_file_name] = image_settings

    os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
    with open(get_homepage_image_settings_file_path(), 'w', encoding='utf-8') as settings_file:
        json.dump({'images': saved_images}, settings_file, ensure_ascii=False, indent=2, sort_keys=True)


def get_homepage_image_file_names(include_hidden=False):
    image_directory = os.path.join(settings.MEDIA_ROOT, HOMEPAGE_IMAGE_DIR_NAME)
    try:
        image_file_names = sorted(os.listdir(image_directory), key=str.lower)
    except OSError:
        return []

    settings_by_file_name = get_homepage_image_settings_by_file_name()
    allowed_file_names = []
    for image_file_name in image_file_names:
        image_file_path = os.path.join(image_directory, image_file_name)
        _, image_extension = os.path.splitext(image_file_name)
        if image_extension.lower() not in HOMEPAGE_ALLOWED_IMAGE_EXTENSIONS:
            continue
        if not os.path.isfile(image_file_path):
            continue
        image_settings = settings_by_file_name.get(image_file_name, {})
        if not include_hidden and image_settings.get('is_hidden'):
            continue
        allowed_file_names.append(image_file_name)

    def homepage_image_sort_key(image_file_name):
        image_settings = settings_by_file_name.get(image_file_name, {})
        sort_order = image_settings.get('sort_order')
        if sort_order is None:
            return (1, 0, image_file_name.lower())
        return (0, sort_order, image_file_name.lower())

    return sorted(allowed_file_names, key=homepage_image_sort_key)


def is_homepage_image_file_name_allowed(image_file_name):
    if not image_file_name:
        return False
    if image_file_name != os.path.basename(image_file_name):
        return False
    _, image_extension = os.path.splitext(image_file_name)
    return image_extension.lower() in HOMEPAGE_ALLOWED_IMAGE_EXTENSIONS


def get_homepage_image_file_path(image_file_name):
    if not is_homepage_image_file_name_allowed(image_file_name):
        raise Http404('Homepage image was not found.')

    image_file_path = os.path.join(
        settings.MEDIA_ROOT,
        HOMEPAGE_IMAGE_DIR_NAME,
        image_file_name,
    )
    if not os.path.isfile(image_file_path):
        raise Http404('Homepage image was not found.')
    return image_file_path


def build_homepage_cache_file_name(image_file_name, image_file_path):
    image_stat = os.stat(image_file_path)
    cache_key = f'{image_file_name}:{image_stat.st_mtime_ns}:{image_stat.st_size}'
    cache_digest = hashlib.sha256(cache_key.encode('utf-8')).hexdigest()[:16]
    raw_file_stem, _ = os.path.splitext(image_file_name)
    safe_file_stem = re.sub(r'[^A-Za-z0-9._-]+', '-', raw_file_stem).strip('-._')
    safe_file_stem = safe_file_stem[:56] or 'homepage-image'
    return f'{safe_file_stem}-{cache_digest}.webp'


def optimize_homepage_image(source_image_path, cached_image_path):
    image_resampling = getattr(Image, 'Resampling', Image)
    resampling_filter = getattr(image_resampling, 'LANCZOS', None)
    if resampling_filter is None:
        resampling_filter = getattr(Image, 'LANCZOS', Image.BICUBIC)
    with Image.open(source_image_path) as source_image:
        optimized_image = ImageOps.exif_transpose(source_image)
        optimized_image.thumbnail(HOMEPAGE_IMAGE_CACHE_MAX_SIZE, resampling_filter)
        if optimized_image.mode != 'RGB':
            optimized_image = optimized_image.convert('RGB')
        optimized_image.save(
            cached_image_path,
            format='WEBP',
            quality=HOMEPAGE_IMAGE_CACHE_QUALITY,
            method=4,
        )


def get_or_create_homepage_cached_image_url(image_file_name):
    image_file_path = get_homepage_image_file_path(image_file_name)
    cache_directory = os.path.join(settings.MEDIA_ROOT, HOMEPAGE_IMAGE_CACHE_DIR_NAME)
    os.makedirs(cache_directory, exist_ok=True)

    cache_file_name = build_homepage_cache_file_name(image_file_name, image_file_path)
    cached_image_path = os.path.join(cache_directory, cache_file_name)
    if not os.path.exists(cached_image_path):
        try:
            optimize_homepage_image(image_file_path, cached_image_path)
        except (OSError, UnidentifiedImageError) as error:
            raise Http404('Homepage image could not be opened.') from error

    return f"{settings.MEDIA_URL.rstrip('/')}/{HOMEPAGE_IMAGE_CACHE_DIR_NAME}/{quote(cache_file_name)}"


def normalize_homepage_slide_copy(raw_slide_copy):
    if not isinstance(raw_slide_copy, dict):
        return None

    normalized_copy = {}
    for field_name in HOMEPAGE_COPY_FIELDS:
        field_value = raw_slide_copy.get(field_name)
        if not isinstance(field_value, str) or not field_value.strip():
            return None
        normalized_copy[field_name] = field_value.strip()

    raw_moods = raw_slide_copy.get('moods')
    if not isinstance(raw_moods, list):
        return None
    normalized_moods = []
    for raw_mood in raw_moods:
        if not isinstance(raw_mood, str):
            continue
        normalized_mood = raw_mood.strip()
        if normalized_mood and normalized_mood not in normalized_moods:
            normalized_moods.append(normalized_mood[:12])
        if len(normalized_moods) == 3:
            break
    if not normalized_moods:
        return None
    normalized_copy['moods'] = normalized_moods
    return normalized_copy


def get_homepage_image_copy_file_path():
    return os.path.join(settings.MEDIA_ROOT, HOMEPAGE_IMAGE_COPY_FILE_NAME)


def get_homepage_ai_copy_by_file_name():
    try:
        with open(get_homepage_image_copy_file_path(), 'r', encoding='utf-8') as copy_file:
            raw_copy_by_file_name = json.load(copy_file)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw_copy_by_file_name, dict):
        return {}

    copy_by_file_name = {}
    for image_file_name, raw_slide_copy in raw_copy_by_file_name.items():
        if not isinstance(image_file_name, str):
            continue
        if not is_homepage_image_file_name_allowed(image_file_name):
            continue
        normalized_copy = normalize_homepage_slide_copy(raw_slide_copy)
        if normalized_copy:
            copy_by_file_name[image_file_name] = normalized_copy
    return copy_by_file_name


def save_homepage_ai_copy_by_file_name(copy_by_file_name):
    normalized_copy_by_file_name = {}
    for image_file_name, raw_slide_copy in copy_by_file_name.items():
        if not isinstance(image_file_name, str):
            continue
        if not is_homepage_image_file_name_allowed(image_file_name):
            continue
        normalized_copy = normalize_homepage_slide_copy(raw_slide_copy)
        if normalized_copy:
            normalized_copy_by_file_name[image_file_name] = normalized_copy

    os.makedirs(settings.MEDIA_ROOT, exist_ok=True)
    with open(get_homepage_image_copy_file_path(), 'w', encoding='utf-8') as copy_file:
        json.dump(normalized_copy_by_file_name, copy_file, ensure_ascii=False, indent=2, sort_keys=True)


def build_homepage_slide_copy(image_index, image_file_name, ai_copy_by_file_name):
    fallback_copy = HOMEPAGE_COPY_PRESETS[image_index % len(HOMEPAGE_COPY_PRESETS)].copy()
    ai_slide_copy = ai_copy_by_file_name.get(image_file_name)
    if ai_slide_copy:
        fallback_copy.update(ai_slide_copy)
    return fallback_copy


def build_homepage_carousel_slides():
    carousel_slides = []
    image_file_names = get_homepage_image_file_names()
    ai_copy_by_file_name = get_homepage_ai_copy_by_file_name()

    for image_index, image_file_name in enumerate(image_file_names):
        theme_preset = HOMEPAGE_THEME_PRESETS[image_index % len(HOMEPAGE_THEME_PRESETS)]
        copy_preset = build_homepage_slide_copy(image_index, image_file_name, ai_copy_by_file_name)
        carousel_slides.append({
            'image_url': reverse('homepage_carousel_image', args=[image_file_name]),
            'file_name': image_file_name,
            'accent': theme_preset['accent'],
            'accent_strong': theme_preset['accent_strong'],
            'accent_soft': theme_preset['accent_soft'],
            **copy_preset,
        })
    return carousel_slides
