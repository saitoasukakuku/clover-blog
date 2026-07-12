from django import forms
from django.shortcuts import get_object_or_404, render, redirect
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core import signing
from django.core.paginator import Paginator
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import connection, transaction
from django.db.models import Case, Count, F, IntegerField, Max, Prefetch, Q, Sum, When
from django.http import Http404, HttpResponse, HttpResponseForbidden, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST
from django.core.management.base import CommandError
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.html import conditional_escape, strip_tags
from django.utils.safestring import mark_safe
from django.utils.xmlutils import SimplerXMLGenerator
from django.templatetags.static import static as static_asset
from PIL import Image, UnidentifiedImageError
from blog.forms import (
    ChineseAuthenticationForm,
    ChineseUserCreationForm,
    CommentForm,
    CompleteRegistrationForm,
    PrivateMessageForm,
    RegistrationRequestForm,
    UserCenterForm,
)
from blog.management.commands.create_startup_post import (
    DEFAULT_DEEPSEEK_MODEL,
    Command as StartupPostCommand,
)
from blog.management.commands.prepare_music_playback import get_web_playback_file_name
from blog.context_processors import (
    MUSIC_AUDIO_EXTENSIONS,
    MUSIC_COVER_EXTENSIONS,
    MUSIC_DIR_NAME,
    MUSIC_LYRICS_EXTENSIONS,
    build_music_track,
    find_same_name_file,
    split_music_file_name,
)
from blog.homepage_media import (
    HOMEPAGE_COPY_FIELDS,
    HOMEPAGE_IMAGE_DIR_NAME,
    build_homepage_carousel_slides,
    get_homepage_ai_copy_by_file_name,
    get_homepage_image_file_names,
    get_homepage_image_file_path,
    get_homepage_image_settings_by_file_name,
    get_or_create_homepage_cached_image_url,
    normalize_homepage_slide_copy,
    save_homepage_ai_copy_by_file_name,
    save_homepage_image_settings_by_file_name,
)
from blog.media_security import (
    MAX_IMAGE_UPLOAD_BYTES,
    has_valid_audio_signature,
    validate_image_bytes as validate_secure_image_bytes,
    validate_uploaded_image,
)
from blog.models import (
    BackgroundTask,
    Comment,
    FriendRequest,
    Friendship,
    Notification,
    Post,
    PostFavorite,
    PostImage,
    PostLike,
    PostReaction,
    PostRevision,
    PrivateMessage,
    RegistrationRequest,
    Tag,
    UserBlock,
    UserProfile,
)
from blog.post_media import (
    build_inline_file_response,
    can_read_post_image,
    find_protected_or_legacy_file,
    sync_post_body_images,
)
from blog.post_editor import CUSTOM_CATEGORY_VALUE, parse_post_submission
from blog.request_throttling import consume_rate_limit
from blog.registration_approval import (
    RegistrationRequestAlreadyReviewed,
    RegistrationRequestCannotResend,
    approve_registration_request as approve_registration_request_service,
    reject_registration_request as reject_registration_request_service,
    resend_registration_code as resend_registration_code_service,
)
from blog.site_owner import get_site_owner_profile
from collections import Counter
from io import BytesIO, StringIO
import base64
import binascii
import json
import logging
import os
import re
import time
import uuid
from urllib.parse import quote, urlparse


logger = logging.getLogger(__name__)


@require_GET
def health_check(request):
    with connection.cursor() as database_cursor:
        database_cursor.execute('SELECT 1')
        database_cursor.fetchone()
    response = JsonResponse({'status': 'ok'})
    response['Cache-Control'] = 'no-store'
    return response


@require_GET
def legacy_private_media_not_found(request, file_name):
    raise Http404('旧媒体地址已停用。')


AI_GENERATION_COOLDOWN_SECONDS = 60
AI_GENERATION_HOURLY_LIMIT = 10
AI_COVER_TOKEN_SALT = 'blog.ai-cover'
AI_COVER_TOKEN_MAX_AGE_SECONDS = 7200
MAX_MUSIC_UPLOAD_BYTES = 200 * 1024 * 1024
MAX_MUSIC_UPLOAD_CHUNK_BYTES = 8 * 1024 * 1024
MAX_LYRICS_UPLOAD_BYTES = 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {
    'jpeg': 'jpg',
    'jpg': 'jpg',
    'png': 'png',
    'webp': 'webp',
}
MENTION_USERNAME_PATTERN = re.compile(r'@([\w.@+-]{1,150})')
PWA_CACHE_VERSION = '2026-07-10-2'
PWA_THEME_COLOR = '#2e7d32'
REACTION_ICON_MAP = {
    'useful': 'fas fa-lightbulb',
    'resonate': 'fas fa-heart',
    'inspired': 'fas fa-seedling',
    'fun': 'fas fa-star',
}
RECENTLY_READ_SESSION_KEY = 'recently_read_post_ids'
POST_VIEW_SESSION_KEY = 'counted_post_views'
POST_VIEW_COOLDOWN_SECONDS = 30 * 60
INVALID_AUDIO_FILE_MESSAGE = (
    '音频内容与文件扩展名不匹配，'
    '请上传有效的 MP3、FLAC、WAV、M4A 或 OGG 文件。'
)
OVERSIZED_AUDIO_MESSAGE = '音频文件不能超过 200MB。'
INVALID_LYRICS_FILE_MESSAGE = '歌词文件只支持 LRC 或 TXT。'
OVERSIZED_LYRICS_MESSAGE = '歌词文件不能超过 1MB。'
INVALID_IMAGE_DATA_MESSAGE = '图片数据无效，请重新选择图片。'
INVALID_IMAGE_FILE_MESSAGE = '请上传有效的图片文件。'
OVERSIZED_IMAGE_MESSAGE = '图片文件不能超过 5MB。'


def get_friendships_for_user(user):
    return Friendship.objects.filter(
        Q(user_low=user) | Q(user_high=user)
    ).select_related(
        'user_low__profile',
        'user_high__profile',
    )


def get_friends_for_user(user):
    friends = []
    for friendship in get_friendships_for_user(user):
        friend = (
            friendship.user_high
            if friendship.user_low_id == user.id
            else friendship.user_low
        )
        friends.append(friend)
    return friends


def are_friends(first_user, second_user):
    if first_user.id == second_user.id:
        return False
    user_low_id, user_high_id = sorted((first_user.id, second_user.id))
    return Friendship.objects.filter(
        user_low_id=user_low_id,
        user_high_id=user_high_id,
    ).exists()


def user_has_blocked(blocker, blocked):
    if not blocker.is_authenticated or blocker.id == blocked.id:
        return False
    return UserBlock.objects.filter(blocker=blocker, blocked=blocked).exists()


def users_are_blocked_between(first_user, second_user):
    if first_user.id == second_user.id:
        return False
    return UserBlock.objects.filter(
        Q(blocker=first_user, blocked=second_user)
        | Q(blocker=second_user, blocked=first_user)
    ).exists()


def delete_friendship_between(first_user, second_user):
    user_low_id, user_high_id = sorted((first_user.id, second_user.id))
    Friendship.objects.filter(
        user_low_id=user_low_id,
        user_high_id=user_high_id,
    ).delete()


def get_relationship_status(current_user, target_user):
    if not current_user.is_authenticated:
        return 'guest'
    if current_user.id == target_user.id:
        return 'self'
    if user_has_blocked(current_user, target_user):
        return 'blocked'
    if user_has_blocked(target_user, current_user):
        return 'blocked_by'
    if are_friends(current_user, target_user):
        return 'friend'

    pending_request = FriendRequest.objects.filter(
        Q(sender=current_user, receiver=target_user)
        | Q(sender=target_user, receiver=current_user),
        status='pending',
    ).first()
    if pending_request is None:
        return 'none'
    if pending_request.sender_id == current_user.id:
        return 'outgoing'
    return 'incoming'


def get_safe_post_next_url(request, fallback_url):
    next_url = request.POST.get('next') or fallback_url
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=not settings.DEBUG,
    ):
        return fallback_url
    return next_url


def get_category_context(post=None):
    category = getattr(post, 'category', '') if post else ''
    is_custom_category = bool(category) and category not in Post.CATEGORY_LABELS
    return {
        'categories': Post.CATEGORY_CHOICES,
        'custom_category_value': CUSTOM_CATEGORY_VALUE,
        'is_custom_category': is_custom_category,
        'custom_category': category if is_custom_category else '',
    }


BOUNDED_QUERY_PARAMETERS = {
    'author': 150,
    'category': 50,
    'q': 100,
    'tag': 50,
}


def get_normalized_query_params(request):
    query_params = request.GET.copy()
    for query_name, maximum_length in BOUNDED_QUERY_PARAMETERS.items():
        if query_name in query_params:
            query_params[query_name] = query_params.get(query_name, '').strip()[
                :maximum_length
            ]
    return query_params


def get_clear_query(request, parameter_name):
    query_params = get_normalized_query_params(request)
    query_params.pop(parameter_name, None)
    query_params.pop('page', None)
    return query_params.urlencode()


def get_bounded_query_value(request, parameter_name, maximum_length):
    return request.GET.get(parameter_name, '').strip()[:maximum_length]


def build_active_filter_chips(
    search_query,
    selected_category,
    selected_category_label,
    selected_tag,
    selected_author,
    selected_author_label,
    clear_search_query,
    clear_category_query,
    clear_tag_query,
    clear_author_query,
):
    active_filter_chips = []
    if search_query:
        active_filter_chips.append({
            'label': '搜索',
            'value': search_query,
            'clear_label': '清除搜索',
            'clear_query': clear_search_query,
            'icon': 'fas fa-search',
        })
    if selected_category:
        active_filter_chips.append({
            'label': '分类',
            'value': selected_category_label,
            'clear_label': '清除分类',
            'clear_query': clear_category_query,
            'icon': 'fas fa-folder-open',
        })
    if selected_tag:
        active_filter_chips.append({
            'label': '标签',
            'value': selected_tag,
            'clear_label': '清除标签',
            'clear_query': clear_tag_query,
            'icon': 'fas fa-tags',
        })
    if selected_author:
        active_filter_chips.append({
            'label': '作者',
            'value': selected_author_label,
            'clear_label': '清除作者',
            'clear_query': clear_author_query,
            'icon': 'fas fa-user',
        })
    return active_filter_chips


def get_currently_published_query():
    return Q(status='published') & (
        Q(scheduled_publish_at__isnull=True)
        | Q(scheduled_publish_at__lte=timezone.now())
    )


def normalize_image_extension(raw_extension):
    extension = raw_extension.lower().strip()
    normalized_extension = ALLOWED_IMAGE_EXTENSIONS.get(extension)
    if normalized_extension is None:
        raise ValueError(INVALID_IMAGE_FILE_MESSAGE)
    return normalized_extension


def validate_image_bytes(image_bytes):
    try:
        return validate_secure_image_bytes(image_bytes)
    except ValueError as error:
        raise ValueError(INVALID_IMAGE_FILE_MESSAGE) from error


def build_image_file_from_data_url(data_url, file_prefix):
    try:
        image_format, image_data = data_url.split(';base64,', 1)
    except ValueError as error:
        raise ValueError(INVALID_IMAGE_DATA_MESSAGE) from error

    if not image_format.startswith('data:image/'):
        raise ValueError(INVALID_IMAGE_DATA_MESSAGE)

    raw_extension = image_format.rsplit('/', 1)[-1]
    normalize_image_extension(raw_extension)
    try:
        image_bytes = base64.b64decode(image_data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(INVALID_IMAGE_DATA_MESSAGE) from error

    extension = validate_image_bytes(image_bytes)
    file_name = f'{file_prefix}_{uuid.uuid4().hex[:8]}.{extension}'
    return ContentFile(image_bytes, name=file_name)


def validate_uploaded_image_file(uploaded_file):
    try:
        return validate_uploaded_image(uploaded_file)
    except ValueError as error:
        raise ValueError(INVALID_IMAGE_FILE_MESSAGE) from error


@login_required
@require_POST
def upload_post_image(request):
    retry_after = consume_rate_limit(
        request,
        'post-image-upload',
        limit=30,
        window_seconds=3600,
        block_seconds=900,
    )
    if retry_after:
        response = JsonResponse({'error': '图片上传过于频繁，请稍后重试。'}, status=429)
        response['Retry-After'] = str(retry_after)
        return response

    uploaded_image = request.FILES.get('image')
    if uploaded_image is None:
        return JsonResponse({'error': INVALID_IMAGE_FILE_MESSAGE}, status=400)

    try:
        validated_image = validate_uploaded_image_file(uploaded_image)
        raw_extension = os.path.splitext(uploaded_image.name)[1].lstrip('.') or 'jpg'
        image_extension = normalize_image_extension(raw_extension)
    except ValueError as error:
        return JsonResponse({'error': str(error)}, status=400)

    raw_alt_text = os.path.splitext(os.path.basename(uploaded_image.name))[0].strip()
    image_alt_text = re.sub(r'[\[\]\r\n]+', ' ', raw_alt_text).strip() or '图片'
    post_image = PostImage(
        owner=request.user,
        original_name=os.path.basename(uploaded_image.name)[:255],
    )
    post_image.image.save(
        f'{uuid.uuid4().hex[:16]}.{image_extension}',
        validated_image,
        save=True,
    )
    image_url = reverse('post_image_file', args=[post_image.public_id])
    return JsonResponse({
        'url': image_url,
        'markdown': f'![{image_alt_text}]({image_url})',
    })


def build_post_image_library_items(user):
    return [
        {
            'file_name': post_image.original_name or post_image.image.name,
            'alt': (
                os.path.splitext(post_image.original_name)[0]
                if post_image.original_name
                else '图片'
            ),
            'url': reverse('post_image_file', args=[post_image.public_id]),
        }
        for post_image in PostImage.objects.filter(owner=user).only(
            'public_id',
            'original_name',
            'image',
        )[:100]
    ]


@login_required
@require_GET
def post_image_library(request):
    return JsonResponse({'images': build_post_image_library_items(request.user)})


@require_GET
def post_image_file(request, public_id):
    post_image = get_object_or_404(PostImage, public_id=public_id)
    if not can_read_post_image(post_image, request.user):
        raise Http404('图片不存在。')
    file_path = find_protected_or_legacy_file(post_image.image)
    is_public = post_image.owner_id != getattr(request.user, 'id', None)
    return build_inline_file_response(file_path, public=is_public)


@require_GET
def post_cover(request, post_id):
    post = get_object_or_404(Post, id=post_id)
    is_owner = (
        request.user.is_authenticated
        and (request.user.is_superuser or post.author_id == request.user.id)
    )
    is_public = (
        post.status == 'published'
        and post.visibility == 'public'
        and (
            post.scheduled_publish_at is None
            or post.scheduled_publish_at <= timezone.now()
        )
    )
    if not is_owner and not is_public:
        raise Http404('封面不存在。')
    file_path = find_protected_or_legacy_file(post.cover)
    return build_inline_file_response(file_path, public=is_public)


@require_GET
def pwa_manifest(request):
    manifest = {
        'name': '白车轴草',
        'short_name': '白车轴草',
        'description': '一个用于阅读、写作、图片和音乐收藏的个人小站。',
        'start_url': reverse('home'),
        'scope': '/',
        'display': 'standalone',
        'background_color': '#f5f7f5',
        'theme_color': PWA_THEME_COLOR,
        'orientation': 'portrait-primary',
        'icons': [
            {
                'src': static_asset('img/favicon_v4.png'),
                'sizes': '640x640',
                'type': 'image/png',
                'purpose': 'any maskable',
            },
        ],
    }
    return JsonResponse(
        manifest,
        json_dumps_params={'ensure_ascii': False},
        content_type='application/manifest+json',
    )


@require_GET
def music_track_lyrics(request, audio_file_name):
    safe_audio_file_name = os.path.basename(audio_file_name)
    if safe_audio_file_name != audio_file_name:
        raise Http404('歌曲不存在。')
    music_directory = os.path.join(settings.MEDIA_ROOT, MUSIC_DIR_NAME)
    audio_file_path = os.path.join(music_directory, safe_audio_file_name)
    audio_extension = os.path.splitext(safe_audio_file_name)[1].lower()
    if (
        audio_extension not in MUSIC_AUDIO_EXTENSIONS
        or not os.path.isfile(audio_file_path)
    ):
        raise Http404('歌曲不存在。')
    track = build_music_track(
        music_directory,
        safe_audio_file_name,
        include_lyrics=True,
    )
    response = JsonResponse({'lyrics_lines': track['lyrics_lines']})
    response['Cache-Control'] = 'public, max-age=300'
    return response


@require_GET
def service_worker(request):
    shell_urls = [
        static_asset('img/favicon_v4.png'),
        static_asset('js/jquery-3.7.1.js'),
        static_asset('js/site-music-player.js'),
        static_asset('css/site-music-player.css'),
        static_asset('plugins/bootstrap-5.3.8-dist/css/bootstrap.min.css'),
        static_asset('plugins/bootstrap-5.3.8-dist/js/bootstrap.bundle.min.js'),
        static_asset('plugins/fontawesome-free-7.1.0-web/css/all.min.css'),
    ]
    shell_urls_json = json.dumps(shell_urls, ensure_ascii=False)
    cache_name = f'clover-blog-shell-v{PWA_CACHE_VERSION}'
    service_worker_source = f"""
const CACHE_NAME = {json.dumps(cache_name)};
const SHELL_URLS = {shell_urls_json};

self.addEventListener('install', (event) => {{
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => cache.addAll(SHELL_URLS))
            .then(() => self.skipWaiting())
    );
}});

self.addEventListener('activate', (event) => {{
    event.waitUntil(
        caches.keys()
            .then((cacheNames) => Promise.all(
                cacheNames
                    .filter((cacheName) => cacheName.startsWith('clover-blog-shell-v') && cacheName !== CACHE_NAME)
                    .map((cacheName) => caches.delete(cacheName))
            ))
            .then(() => self.clients.claim())
    );
}});

self.addEventListener('fetch', (event) => {{
    if (event.request.method !== 'GET') return;
    if (!event.request.url.startsWith(self.location.origin)) return;
    const requestUrl = new URL(event.request.url);
    if (!requestUrl.pathname.startsWith('/static/')) return;

    event.respondWith(
        caches.match(event.request).then((cachedResponse) => {{
            if (cachedResponse) return cachedResponse;
            return fetch(event.request).then((networkResponse) => {{
                if (!networkResponse || !networkResponse.ok) return networkResponse;
                const responseToCache = networkResponse.clone();
                event.waitUntil(
                    caches.open(CACHE_NAME).then((cache) => cache.put(event.request, responseToCache))
                );
                return networkResponse;
            }});
        }})
    );
}});
"""
    response = HttpResponse(service_worker_source.strip(), content_type='application/javascript; charset=utf-8')
    response['Service-Worker-Allowed'] = '/'
    response['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    return response


@require_GET
def sitemap_xml(request):
    sitemap_entries = [
        {
            'loc': request.build_absolute_uri(reverse('home')),
            'changefreq': 'daily',
            'priority': '1.0',
        },
        {
            'loc': request.build_absolute_uri(reverse('index')),
            'changefreq': 'daily',
            'priority': '0.9',
        },
        {
            'loc': request.build_absolute_uri(reverse('archive')),
            'changefreq': 'weekly',
            'priority': '0.6',
        },
        {
            'loc': request.build_absolute_uri(reverse('tags')),
            'changefreq': 'weekly',
            'priority': '0.6',
        },
    ]
    public_posts = Post.objects.filter(
        get_currently_published_query(),
        visibility='public',
    ).order_by('-updated_at')
    for post in public_posts:
        sitemap_entries.append({
            'loc': request.build_absolute_uri(reverse('post_detail', args=[post.id])),
            'lastmod': timezone.localtime(post.updated_at).date().isoformat(),
            'changefreq': 'monthly',
            'priority': '0.7',
        })

    output = StringIO()
    xml = SimplerXMLGenerator(output, 'utf-8')
    xml.startDocument()
    xml.startElement('urlset', {'xmlns': 'http://www.sitemaps.org/schemas/sitemap/0.9'})
    for sitemap_entry in sitemap_entries:
        xml.startElement('url', {})
        xml.addQuickElement('loc', sitemap_entry['loc'])
        if sitemap_entry.get('lastmod'):
            xml.addQuickElement('lastmod', sitemap_entry['lastmod'])
        xml.addQuickElement('changefreq', sitemap_entry['changefreq'])
        xml.addQuickElement('priority', sitemap_entry['priority'])
        xml.endElement('url')
    xml.endElement('urlset')
    xml.endDocument()
    return HttpResponse(output.getvalue(), content_type='application/xml; charset=utf-8')


def get_upload_file_extension(uploaded_file):
    return os.path.splitext(os.path.basename(uploaded_file.name or ''))[1].lower()


def build_safe_media_file_stem(raw_file_name, fallback_stem):
    raw_file_stem = os.path.splitext(os.path.basename(raw_file_name or ''))[0]
    safe_file_stem = re.sub(r'[\\/:*?"<>|\r\n]+', '-', raw_file_stem).strip(' .-_')
    safe_file_stem = re.sub(r'\s+', '-', safe_file_stem)
    return safe_file_stem[:80] or fallback_stem


def save_media_upload(directory_name, file_name, uploaded_file):
    os.makedirs(os.path.join(settings.MEDIA_ROOT, directory_name), exist_ok=True)
    saved_relative_path = default_storage.save(f'{directory_name}/{file_name}', uploaded_file)
    return os.path.basename(saved_relative_path)


def build_homepage_image_upload_name(uploaded_image, image_extension):
    safe_file_stem = build_safe_media_file_stem(uploaded_image.name, 'homepage-image')
    return f'{safe_file_stem}-{uuid.uuid4().hex[:8]}.{image_extension}'


def validate_uploaded_music_file(uploaded_file):
    if uploaded_file.size > MAX_MUSIC_UPLOAD_BYTES:
        raise ValueError(OVERSIZED_AUDIO_MESSAGE)
    audio_extension = get_upload_file_extension(uploaded_file)
    if audio_extension not in MUSIC_AUDIO_EXTENSIONS:
        raise ValueError(INVALID_AUDIO_FILE_MESSAGE)
    if not has_valid_audio_signature(uploaded_file, audio_extension):
        raise ValueError(INVALID_AUDIO_FILE_MESSAGE)
    return audio_extension


def validate_uploaded_lyrics_file(uploaded_file):
    if uploaded_file.size > MAX_LYRICS_UPLOAD_BYTES:
        raise ValueError(OVERSIZED_LYRICS_MESSAGE)
    lyrics_extension = get_upload_file_extension(uploaded_file)
    if lyrics_extension not in MUSIC_LYRICS_EXTENSIONS:
        raise ValueError(INVALID_LYRICS_FILE_MESSAGE)
    return lyrics_extension


def build_homepage_media_items():
    copy_by_file_name = get_homepage_ai_copy_by_file_name()
    settings_by_file_name = get_homepage_image_settings_by_file_name()
    media_items = []
    for image_file_name in get_homepage_image_file_names(include_hidden=True):
        image_file_path = get_homepage_image_file_path(image_file_name)
        image_stat = os.stat(image_file_path)
        slide_copy = copy_by_file_name.get(image_file_name, {})
        image_settings = settings_by_file_name.get(image_file_name, {})
        media_items.append({
            'file_name': image_file_name,
            'file_size': image_stat.st_size,
            'updated_at': timezone.datetime.fromtimestamp(
                image_stat.st_mtime,
                tz=timezone.get_current_timezone(),
            ),
            'image_url': reverse('homepage_carousel_image', args=[image_file_name]),
            'has_copy': image_file_name in copy_by_file_name,
            'sort_order': image_settings.get('sort_order'),
            'is_hidden': bool(image_settings.get('is_hidden')),
            'copy_kicker': slide_copy.get('kicker', ''),
            'copy_headline': slide_copy.get('headline', ''),
            'copy_lead': slide_copy.get('lead', ''),
            'copy_card_title': slide_copy.get('card_title', ''),
            'copy_card_text': slide_copy.get('card_text', ''),
            'copy_moods_text': '，'.join(slide_copy.get('moods', [])),
        })
    return media_items


def build_music_media_items():
    music_directory = os.path.join(settings.MEDIA_ROOT, MUSIC_DIR_NAME)
    try:
        music_file_names = sorted(os.listdir(music_directory), key=str.lower)
    except OSError:
        return []

    source_file_stems = set()
    for music_file_name in music_file_names:
        music_file_path = os.path.join(music_directory, music_file_name)
        file_stem, audio_extension, is_web_playback_file = split_music_file_name(music_file_name)
        if audio_extension.lower() not in MUSIC_AUDIO_EXTENSIONS:
            continue
        if not os.path.isfile(music_file_path):
            continue
        if not is_web_playback_file:
            source_file_stems.add(file_stem.casefold())

    media_items = []
    for music_file_name in music_file_names:
        music_file_path = os.path.join(music_directory, music_file_name)
        file_stem, audio_extension, is_web_playback_file = split_music_file_name(music_file_name)
        if audio_extension.lower() not in MUSIC_AUDIO_EXTENSIONS:
            continue
        if not os.path.isfile(music_file_path):
            continue
        if is_web_playback_file and file_stem.casefold() in source_file_stems:
            continue

        web_playback_file_name = get_web_playback_file_name(music_file_name)
        web_playback_path = os.path.join(music_directory, web_playback_file_name)
        cover_file_name, _ = find_same_name_file(
            music_directory,
            file_stem,
            MUSIC_COVER_EXTENSIONS,
        )
        lyrics_file_name, _ = find_same_name_file(
            music_directory,
            file_stem,
            MUSIC_LYRICS_EXTENSIONS,
        )
        music_file_stat = os.stat(music_file_path)
        media_items.append({
            'file_name': music_file_name,
            'file_stem': file_stem,
            'file_size': music_file_stat.st_size,
            'updated_at': timezone.datetime.fromtimestamp(
                music_file_stat.st_mtime,
                tz=timezone.get_current_timezone(),
            ),
            'web_playback_file_name': web_playback_file_name,
            'has_web_playback': os.path.exists(web_playback_path),
            'cover_file_name': cover_file_name,
            'lyrics_file_name': lyrics_file_name,
        })
    return media_items


def read_music_media_item_lyrics(music_item):
    lyrics_file_name = music_item.get('lyrics_file_name')
    if not lyrics_file_name:
        return ''
    lyrics_file_path = os.path.join(
        settings.MEDIA_ROOT,
        MUSIC_DIR_NAME,
        lyrics_file_name,
    )
    try:
        with open(lyrics_file_path, 'rb') as lyrics_file:
            lyrics_bytes = lyrics_file.read(MAX_LYRICS_UPLOAD_BYTES + 1)
    except OSError:
        return ''
    bounded_lyrics_bytes = lyrics_bytes[:MAX_LYRICS_UPLOAD_BYTES]
    for encoding_name in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            return bounded_lyrics_bytes.decode(encoding_name)
        except UnicodeDecodeError:
            continue
    return bounded_lyrics_bytes.decode('utf-8', errors='replace')


def redirect_to_music_manager():
    return redirect(f'{reverse("media_manager")}?tab=music')


def get_music_media_item_by_file_name(file_name):
    normalized_file_name = os.path.basename(file_name or '')
    if normalized_file_name != file_name:
        return None
    return next(
        (
            music_item
            for music_item in build_music_media_items()
            if music_item['file_name'] == normalized_file_name
        ),
        None,
    )


def get_existing_music_asset_file_names(music_item):
    asset_file_names = [music_item['file_name']]
    for optional_file_name in (
        music_item['web_playback_file_name'] if music_item['has_web_playback'] else None,
        music_item['cover_file_name'],
        music_item['lyrics_file_name'],
    ):
        if optional_file_name:
            asset_file_names.append(optional_file_name)
    return asset_file_names


def build_music_asset_rename_plan(music_item, target_file_stem):
    original_file_name = music_item['file_name']
    original_file_stem = os.path.splitext(original_file_name)[0]
    return [
        (
            source_file_name,
            f'{target_file_stem}{source_file_name[len(original_file_stem):]}',
        )
        for source_file_name in get_existing_music_asset_file_names(music_item)
    ]


def music_rename_plan_has_collision(music_directory, rename_plan):
    source_file_names = {
        source_file_name.casefold()
        for source_file_name, _ in rename_plan
    }
    try:
        current_file_names = {
            current_file_name.casefold()
            for current_file_name in os.listdir(music_directory)
        }
    except OSError:
        current_file_names = set()
    return any(
        target_file_name.casefold() in current_file_names
        and target_file_name.casefold() not in source_file_names
        for _, target_file_name in rename_plan
    )


def move_music_assets(music_directory, rename_plan):
    completed_moves = []
    try:
        for source_file_name, target_file_name in rename_plan:
            if source_file_name == target_file_name:
                continue
            source_file_path = os.path.join(music_directory, source_file_name)
            target_file_path = os.path.join(music_directory, target_file_name)
            os.replace(source_file_path, target_file_path)
            completed_moves.append((source_file_path, target_file_path))
    except OSError:
        for source_file_path, target_file_path in reversed(completed_moves):
            if os.path.isfile(target_file_path) and not os.path.exists(source_file_path):
                try:
                    os.replace(target_file_path, source_file_path)
                except OSError:
                    logger.exception(
                        'Music asset rollback failed: %s -> %s',
                        target_file_path,
                        source_file_path,
                    )
        raise


def remove_music_asset_file(music_directory, file_name):
    if not file_name:
        return
    file_path = os.path.join(music_directory, file_name)
    if os.path.isfile(file_path):
        os.remove(file_path)


def find_music_asset_file_name(music_directory, file_stem, extensions):
    file_name, _ = find_same_name_file(music_directory, file_stem, extensions)
    return file_name


def build_admin_dashboard_stats():
    post_counts = Post.objects.aggregate(
        total_posts=Count('id'),
        published_posts=Count('id', filter=Q(status='published')),
        draft_posts=Count('id', filter=Q(status='draft')),
        total_views=Sum('views_count'),
    )
    return {
        'total_posts': post_counts['total_posts'] or 0,
        'published_posts': post_counts['published_posts'] or 0,
        'draft_posts': post_counts['draft_posts'] or 0,
        'total_views': post_counts['total_views'] or 0,
        'total_users': User.objects.count(),
        'pending_registration_requests': RegistrationRequest.objects.filter(
            status=RegistrationRequest.STATUS_PENDING,
        ).count(),
    }


def build_music_playback_summary():
    music_items = build_music_media_items()
    ready_tracks = sum(1 for music_item in music_items if music_item['has_web_playback'])
    total_tracks = len(music_items)
    return {
        'total_tracks': total_tracks,
        'ready_tracks': ready_tracks,
        'pending_tracks': total_tracks - ready_tracks,
        'recent_items': music_items[:8],
    }


@login_required
@require_GET
def admin_dashboard(request):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    return render(request, 'admin_dashboard.html', {
        'dashboard_stats': build_admin_dashboard_stats(),
        'music_playback_summary': build_music_playback_summary(),
    })


@login_required
@require_GET
def media_manager(request):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    music_media_items = build_music_media_items()
    return render(request, 'media_manager.html', {
        'homepage_media_items': build_homepage_media_items(),
        'music_media_items': music_media_items,
        'active_media_tab': (
            'music'
            if request.GET.get('tab') == 'music'
            else 'homepage'
        ),
        'recent_background_tasks': BackgroundTask.objects.select_related(
            'requested_by',
        )[:10],
    })


@login_required
@require_GET
def media_manager_music_details(request):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    music_item = get_music_media_item_by_file_name(
        request.GET.get('file_name', '').strip()
    )
    if music_item is None:
        return JsonResponse({'error': '音乐文件不存在。'}, status=404)
    return JsonResponse({
        'file_name': music_item['file_name'],
        'lyrics_text': read_music_media_item_lyrics(music_item),
    })


@login_required
@require_POST
def media_manager_upload_homepage_image(request):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    uploaded_image = request.FILES.get('image')
    if uploaded_image is None:
        messages.error(request, INVALID_IMAGE_FILE_MESSAGE)
        return redirect('media_manager')

    try:
        validated_image = validate_uploaded_image_file(uploaded_image)
        image_extension = normalize_image_extension(
            get_upload_file_extension(uploaded_image).lstrip('.') or 'jpg',
        )
    except ValueError as error:
        messages.error(request, str(error))
        return redirect('media_manager')

    image_file_name = build_homepage_image_upload_name(uploaded_image, image_extension)
    saved_image_file_name = save_media_upload(
        HOMEPAGE_IMAGE_DIR_NAME,
        image_file_name,
        validated_image,
    )
    try:
        get_or_create_homepage_cached_image_url(saved_image_file_name)
    except Http404:
        logger.warning('Homepage image cache could not be prepared for %s.', saved_image_file_name)
    messages.success(request, f'已上传首页图片：{saved_image_file_name}')
    return redirect('media_manager')


def build_homepage_copy_from_post(post_data):
    copy_field_values = {
        field_name: post_data.get(field_name, '').strip()
        for field_name in HOMEPAGE_COPY_FIELDS
    }
    moods_text = post_data.get('moods', '').strip()
    has_copy_input = any(copy_field_values.values()) or bool(moods_text)
    if not has_copy_input:
        return None

    raw_moods = [
        mood_text.strip()
        for mood_text in re.split(r'[,，\n]+', moods_text)
        if mood_text.strip()
    ]
    raw_slide_copy = {
        **copy_field_values,
        'moods': raw_moods,
    }
    return normalize_homepage_slide_copy(raw_slide_copy)


@login_required
@require_POST
def media_manager_update_homepage_image(request):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    image_file_name = request.POST.get('file_name', '').strip()
    if image_file_name not in get_homepage_image_file_names(include_hidden=True):
        messages.error(request, '首页图片不存在。')
        return redirect('media_manager')

    sort_order_text = request.POST.get('sort_order', '').strip()
    if sort_order_text:
        try:
            sort_order = int(sort_order_text)
        except ValueError:
            messages.error(request, '首页图片排序只能填写整数。')
            return redirect('media_manager')
    else:
        sort_order = None

    homepage_image_copy = build_homepage_copy_from_post(request.POST)
    has_copy_input = any(
        request.POST.get(field_name, '').strip()
        for field_name in HOMEPAGE_COPY_FIELDS
    ) or bool(request.POST.get('moods', '').strip())
    if has_copy_input and homepage_image_copy is None:
        messages.error(request, '请完整填写首页图片文案和 1 到 3 个氛围标签。')
        return redirect('media_manager')

    settings_by_file_name = get_homepage_image_settings_by_file_name()
    settings_by_file_name[image_file_name] = {
        'sort_order': sort_order,
        'is_hidden': request.POST.get('is_hidden') == 'on',
    }
    save_homepage_image_settings_by_file_name(settings_by_file_name)

    if homepage_image_copy is not None:
        copy_by_file_name = get_homepage_ai_copy_by_file_name()
        copy_by_file_name[image_file_name] = homepage_image_copy
        save_homepage_ai_copy_by_file_name(copy_by_file_name)

    messages.success(request, f'已更新首页图片：{image_file_name}')
    return redirect('media_manager')


@login_required
@require_POST
def media_manager_upload_music(request):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    uploaded_audio = request.FILES.get('audio')
    if uploaded_audio is None:
        messages.error(request, INVALID_AUDIO_FILE_MESSAGE)
        return redirect_to_music_manager()

    try:
        audio_extension = validate_uploaded_music_file(uploaded_audio)
    except ValueError as error:
        messages.error(request, str(error))
        return redirect_to_music_manager()

    uploaded_cover = request.FILES.get('cover')
    validated_cover = None
    cover_extension = ''
    if uploaded_cover is not None:
        try:
            validated_cover = validate_uploaded_image_file(uploaded_cover)
            cover_extension = normalize_image_extension(
                get_upload_file_extension(uploaded_cover).lstrip('.') or 'jpg',
            )
        except ValueError as error:
            messages.error(request, str(error))
            return redirect_to_music_manager()

    uploaded_lyrics = request.FILES.get('lyrics')
    lyrics_extension = ''
    if uploaded_lyrics is not None:
        try:
            lyrics_extension = validate_uploaded_lyrics_file(uploaded_lyrics)
        except ValueError as error:
            messages.error(request, str(error))
            return redirect_to_music_manager()

    audio_file_stem = build_safe_media_file_stem(uploaded_audio.name, 'music-track')
    audio_file_name = f'{audio_file_stem}{audio_extension}'
    saved_audio_file_name = save_media_upload(MUSIC_DIR_NAME, audio_file_name, uploaded_audio)
    saved_audio_file_stem, _ = os.path.splitext(saved_audio_file_name)

    if validated_cover is not None:
        save_media_upload(MUSIC_DIR_NAME, f'{saved_audio_file_stem}.{cover_extension}', validated_cover)

    if uploaded_lyrics is not None:
        save_media_upload(MUSIC_DIR_NAME, f'{saved_audio_file_stem}{lyrics_extension}', uploaded_lyrics)

    messages.success(request, f'已上传音乐：{saved_audio_file_name}')
    return redirect_to_music_manager()


def parse_music_chunk_integer(raw_value, field_name):
    try:
        parsed_value = int(raw_value)
    except (TypeError, ValueError) as error:
        raise ValueError(f'{field_name}无效。') from error
    if parsed_value < 0:
        raise ValueError(f'{field_name}无效。')
    return parsed_value


def build_music_chunk_upload_path(user_id, upload_id):
    if not re.fullmatch(r'[a-f0-9]{32}', upload_id or ''):
        raise ValueError('上传标识无效，请重新选择文件。')
    upload_directory = os.path.join(settings.MEDIA_ROOT, 'music_uploads')
    os.makedirs(upload_directory, exist_ok=True)
    return os.path.join(upload_directory, f'{user_id}.part')


@login_required
@require_POST
def media_manager_upload_music_chunk(request):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    uploaded_chunk = request.FILES.get('chunk')
    if uploaded_chunk is None or uploaded_chunk.size > MAX_MUSIC_UPLOAD_CHUNK_BYTES:
        return JsonResponse({'error': '音乐分片缺失或过大，请重试。'}, status=400)

    raw_file_name = os.path.basename(request.POST.get('file_name', '').strip())
    audio_extension = os.path.splitext(raw_file_name)[1].lower()
    if audio_extension not in MUSIC_AUDIO_EXTENSIONS:
        return JsonResponse({'error': INVALID_AUDIO_FILE_MESSAGE}, status=400)

    try:
        expected_file_size = parse_music_chunk_integer(request.POST.get('file_size'), '文件大小')
        chunk_start = parse_music_chunk_integer(request.POST.get('chunk_start'), '分片位置')
        upload_path = build_music_chunk_upload_path(
            request.user.id,
            request.POST.get('upload_id', '').strip().lower(),
        )
    except ValueError as error:
        return JsonResponse({'error': str(error)}, status=400)

    if expected_file_size <= 0 or expected_file_size > MAX_MUSIC_UPLOAD_BYTES:
        return JsonResponse({'error': OVERSIZED_AUDIO_MESSAGE}, status=400)
    if chunk_start + uploaded_chunk.size > expected_file_size:
        return JsonResponse({'error': '音乐分片超过文件声明大小，请重新上传。'}, status=400)

    uploaded_cover = request.FILES.get('cover')
    validated_cover = None
    cover_extension = ''
    if uploaded_cover is not None:
        try:
            validated_cover = validate_uploaded_image_file(uploaded_cover)
            cover_extension = normalize_image_extension(
                get_upload_file_extension(uploaded_cover).lstrip('.') or 'jpg',
            )
        except ValueError as error:
            return JsonResponse({'error': str(error)}, status=400)

    uploaded_lyrics = request.FILES.get('lyrics')
    lyrics_extension = ''
    if uploaded_lyrics is not None:
        try:
            lyrics_extension = validate_uploaded_lyrics_file(uploaded_lyrics)
        except ValueError as error:
            return JsonResponse({'error': str(error)}, status=400)

    current_upload_size = os.path.getsize(upload_path) if os.path.isfile(upload_path) else 0
    if chunk_start == 0:
        upload_mode = 'wb'
    elif current_upload_size == chunk_start:
        upload_mode = 'ab'
    else:
        return JsonResponse({
            'error': '上传进度不一致，请重新选择文件后重试。',
            'received_bytes': current_upload_size,
        }, status=409)

    with open(upload_path, upload_mode) as upload_file:
        for chunk_bytes in uploaded_chunk.chunks():
            upload_file.write(chunk_bytes)

    received_file_size = os.path.getsize(upload_path)
    is_final_chunk = request.POST.get('is_final') == 'true'
    if not is_final_chunk:
        return JsonResponse({
            'complete': False,
            'received_bytes': received_file_size,
        })

    if received_file_size != expected_file_size:
        return JsonResponse({
            'error': '音乐文件大小校验失败，请重新上传。',
            'received_bytes': received_file_size,
        }, status=400)

    if not has_valid_audio_signature(upload_path, audio_extension):
        return JsonResponse({'error': INVALID_AUDIO_FILE_MESSAGE}, status=400)

    audio_file_stem = build_safe_media_file_stem(raw_file_name, 'music-track')
    audio_file_name = f'{audio_file_stem}{audio_extension}'
    available_relative_path = default_storage.get_available_name(
        f'{MUSIC_DIR_NAME}/{audio_file_name}',
    )
    available_audio_file_name = os.path.basename(available_relative_path)
    available_audio_file_stem = os.path.splitext(available_audio_file_name)[0]
    audio_file_path = os.path.join(settings.MEDIA_ROOT, available_relative_path)
    os.makedirs(os.path.dirname(audio_file_path), exist_ok=True)
    os.replace(upload_path, audio_file_path)

    if validated_cover is not None:
        save_media_upload(
            MUSIC_DIR_NAME,
            f'{available_audio_file_stem}.{cover_extension}',
            validated_cover,
        )
    if uploaded_lyrics is not None:
        save_media_upload(
            MUSIC_DIR_NAME,
            f'{available_audio_file_stem}{lyrics_extension}',
            uploaded_lyrics,
        )

    return JsonResponse({
        'complete': True,
        'file_name': available_audio_file_name,
        'message': f'已上传音乐：{available_audio_file_name}',
        'redirect_url': f'{reverse("media_manager")}?tab=music',
    })


@login_required
@require_POST
def media_manager_update_music(request):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    original_file_name = request.POST.get('original_file_name', '').strip()
    music_item = get_music_media_item_by_file_name(original_file_name)
    if music_item is None:
        messages.error(request, '音乐文件不存在。')
        return redirect_to_music_manager()

    target_file_stem = build_safe_media_file_stem(
        request.POST.get('track_name', ''),
        os.path.splitext(original_file_name)[0],
    )
    music_directory = os.path.join(settings.MEDIA_ROOT, MUSIC_DIR_NAME)
    rename_plan = build_music_asset_rename_plan(music_item, target_file_stem)
    if music_rename_plan_has_collision(music_directory, rename_plan):
        messages.error(request, '同名音乐资源已经存在，请换一个名称。')
        return redirect_to_music_manager()

    uploaded_cover = request.FILES.get('cover')
    validated_cover = None
    cover_extension = ''
    if uploaded_cover is not None:
        try:
            validated_cover = validate_uploaded_image_file(uploaded_cover)
            cover_extension = normalize_image_extension(
                get_upload_file_extension(uploaded_cover).lstrip('.') or 'jpg',
            )
        except ValueError as error:
            messages.error(request, str(error))
            return redirect_to_music_manager()

    uploaded_lyrics = request.FILES.get('lyrics')
    lyrics_extension = ''
    if uploaded_lyrics is not None:
        try:
            lyrics_extension = validate_uploaded_lyrics_file(uploaded_lyrics)
        except ValueError as error:
            messages.error(request, str(error))
            return redirect_to_music_manager()

    lyrics_text = request.POST.get('lyrics_text', '')
    if len(lyrics_text.encode('utf-8')) > MAX_LYRICS_UPLOAD_BYTES:
        messages.error(request, OVERSIZED_LYRICS_MESSAGE)
        return redirect_to_music_manager()

    try:
        move_music_assets(music_directory, rename_plan)
    except OSError:
        logger.exception('Music asset rename failed for %s.', original_file_name)
        messages.error(request, '音乐资源重命名失败，原文件已尽量恢复，请稍后重试。')
        return redirect_to_music_manager()

    current_cover_file_name = find_music_asset_file_name(
        music_directory,
        target_file_stem,
        MUSIC_COVER_EXTENSIONS,
    )
    remove_cover_requested = request.POST.get('remove_cover') == 'on'
    if remove_cover_requested or validated_cover is not None:
        remove_music_asset_file(music_directory, current_cover_file_name)
    if validated_cover is not None and not remove_cover_requested:
        save_media_upload(
            MUSIC_DIR_NAME,
            f'{target_file_stem}.{cover_extension}',
            validated_cover,
        )

    current_lyrics_file_name = find_music_asset_file_name(
        music_directory,
        target_file_stem,
        MUSIC_LYRICS_EXTENSIONS,
    )
    remove_lyrics_requested = request.POST.get('remove_lyrics') == 'on'
    should_replace_lyrics = (
        not remove_lyrics_requested
        and (uploaded_lyrics is not None or bool(lyrics_text.strip()))
    )
    if remove_lyrics_requested or should_replace_lyrics:
        remove_music_asset_file(music_directory, current_lyrics_file_name)
    if uploaded_lyrics is not None and not remove_lyrics_requested:
        save_media_upload(
            MUSIC_DIR_NAME,
            f'{target_file_stem}{lyrics_extension}',
            uploaded_lyrics,
        )
    elif lyrics_text.strip() and not remove_lyrics_requested:
        save_media_upload(
            MUSIC_DIR_NAME,
            f'{target_file_stem}.lrc',
            ContentFile(lyrics_text.encode('utf-8')),
        )

    messages.success(request, f'已更新音乐：{target_file_stem}')
    return redirect_to_music_manager()


@login_required
@require_POST
def media_manager_run_action(request):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    action = request.POST.get('action')
    allowed_actions = {
        BackgroundTask.TYPE_PREPARE_MUSIC,
        BackgroundTask.TYPE_GENERATE_HOMEPAGE_COPY,
    }
    if action not in allowed_actions:
        messages.error(request, '未知的媒体管理操作。')
        return redirect('media_manager')

    has_active_task = BackgroundTask.objects.filter(
        task_type=action,
        status__in={
            BackgroundTask.STATUS_PENDING,
            BackgroundTask.STATUS_RUNNING,
        },
    ).exists()
    if has_active_task:
        messages.info(request, '同类任务已经在等待或执行中。')
        return redirect('media_manager')

    BackgroundTask.objects.create(
        task_type=action,
        requested_by=request.user,
    )
    messages.success(request, '任务已加入后台队列。')
    return redirect('media_manager')


def build_post_form_context(
    title,
    category,
    tags,
    content,
    visibility,
    series_title='',
    series_order=None,
    scheduled_publish_at=None,
):
    post = Post(
        title=title or '',
        category=category or '',
        tags=tags or '',
        series_title=series_title or '',
        series_order=series_order,
        scheduled_publish_at=scheduled_publish_at,
        content=content or '',
        visibility=visibility or 'private',
    )
    context = {'post': post}
    context.update(get_category_context(post))
    return context


def build_edit_post_form_context(post, updated_post_values=None):
    if updated_post_values:
        for field_name, field_value in updated_post_values.items():
            setattr(post, field_name, field_value)
    context = {'post': post}
    context.update(get_category_context(post))
    return context


def get_ai_cover_data(ai_cover_token):
    if not ai_cover_token:
        return None

    try:
        cover_data = signing.loads(
            ai_cover_token,
            salt=AI_COVER_TOKEN_SALT,
            max_age=AI_COVER_TOKEN_MAX_AGE_SECONDS,
        )
    except (signing.BadSignature, signing.SignatureExpired):
        return None

    image_url = cover_data.get('image_url', '')
    parsed_image_url = urlparse(image_url)
    if parsed_image_url.scheme != 'https' or parsed_image_url.hostname != 'images.pexels.com':
        return None
    return cover_data


def filter_readable_posts(posts, request_user):
    currently_published_query = get_currently_published_query()
    if request_user.is_authenticated:
        return posts.filter(
            Q(currently_published_query, visibility='public')
            | Q(author=request_user) & currently_published_query
        ).distinct()

    return posts.filter(
        currently_published_query,
        visibility='public',
    )


def append_ai_cover_attribution(content, cover_data):
    photographer = cover_data.get('photographer', '').strip()
    photo_url = cover_data.get('photo_url', '').strip()
    photographer_url = cover_data.get('photographer_url', '').strip()
    if not photographer or not photo_url:
        return content

    attribution = f'封面图：Photo by {photographer} on Pexels。'
    if photographer_url:
        attribution += f'\n摄影师主页：{photographer_url}'
    attribution += f'\n图片来源：{photo_url}'
    return f'{content}\n\n{attribution}'


def get_readable_published_posts(request_user):
    return filter_readable_posts(
        Post.objects.all(),
        request_user,
    ).order_by('-created_at')


def highlight_search_text(value, search_query):
    raw_value = str(value or '')
    cleaned_search_query = (search_query or '').strip()
    if not cleaned_search_query:
        return conditional_escape(raw_value)

    highlighted_parts = []
    previous_end = 0
    for match in re.finditer(re.escape(cleaned_search_query), raw_value, re.IGNORECASE):
        highlighted_parts.append(conditional_escape(raw_value[previous_end:match.start()]))
        highlighted_parts.append(
            '<mark class="search-highlight">'
            f'{conditional_escape(match.group(0))}'
            '</mark>'
        )
        previous_end = match.end()
    highlighted_parts.append(conditional_escape(raw_value[previous_end:]))
    return mark_safe(''.join(str(part) for part in highlighted_parts))


def build_search_excerpt(content, search_query, radius=56):
    plain_content = strip_tags(content or '').replace('\r\n', '\n').replace('\r', '\n')
    plain_content = re.sub(r'\s+', ' ', plain_content).strip()
    cleaned_search_query = (search_query or '').strip()
    if not cleaned_search_query:
        return highlight_search_text(plain_content[:100], '')

    match_index = plain_content.lower().find(cleaned_search_query.lower())
    if match_index < 0:
        return highlight_search_text(plain_content[:100], cleaned_search_query)

    start_index = max(0, match_index - radius)
    end_index = min(len(plain_content), match_index + len(cleaned_search_query) + radius)
    excerpt = plain_content[start_index:end_index]
    if start_index > 0:
        excerpt = f'...{excerpt}'
    if end_index < len(plain_content):
        excerpt = f'{excerpt}...'
    return highlight_search_text(excerpt, cleaned_search_query)


def prepare_post_card_display(post, search_query):
    post.card_display_tags = get_display_tags(post)[:3]
    post.card_title_html = highlight_search_text(post.title, search_query)
    post.card_excerpt_html = build_search_excerpt(post.content, search_query)
    return post


def record_recently_read_post(request, post):
    recent_post_ids = request.session.get(RECENTLY_READ_SESSION_KEY, [])
    normalized_post_ids = []
    for recent_post_id in recent_post_ids:
        try:
            normalized_post_id = int(recent_post_id)
        except (TypeError, ValueError):
            continue
        if normalized_post_id != post.id and normalized_post_id not in normalized_post_ids:
            normalized_post_ids.append(normalized_post_id)
    request.session[RECENTLY_READ_SESSION_KEY] = [post.id] + normalized_post_ids[:5]
    request.session.modified = True


def record_post_view(request, post):
    if post.status != 'published':
        return False

    current_timestamp = int(time.time())
    counted_post_views = request.session.get(POST_VIEW_SESSION_KEY, {})
    post_key = str(post.id)
    last_counted_timestamp = int(counted_post_views.get(post_key, 0) or 0)
    if current_timestamp - last_counted_timestamp < POST_VIEW_COOLDOWN_SECONDS:
        return False

    Post.objects.filter(id=post.id).update(views_count=F('views_count') + 1)
    post.views_count += 1
    counted_post_views[post_key] = current_timestamp
    if len(counted_post_views) > 100:
        counted_post_views = dict(
            sorted(
                counted_post_views.items(),
                key=lambda item: item[1],
                reverse=True,
            )[:100]
        )
    request.session[POST_VIEW_SESSION_KEY] = counted_post_views
    request.session.modified = True
    return True


def get_recently_read_posts(request):
    recent_post_ids = request.session.get(RECENTLY_READ_SESSION_KEY, [])
    normalized_post_ids = []
    for recent_post_id in recent_post_ids:
        try:
            normalized_post_id = int(recent_post_id)
        except (TypeError, ValueError):
            continue
        if normalized_post_id not in normalized_post_ids:
            normalized_post_ids.append(normalized_post_id)
    if not normalized_post_ids:
        return []

    readable_posts = filter_readable_posts(
        Post.objects.filter(id__in=normalized_post_ids),
        request.user,
    ).select_related('author', 'author__profile')
    post_by_id = {post.id: post for post in readable_posts}
    return [
        post_by_id[post_id]
        for post_id in normalized_post_ids
        if post_id in post_by_id
    ]


def build_elided_page_range(page_obj):
    return page_obj.paginator.get_elided_page_range(
        page_obj.number,
        on_each_side=1,
        on_ends=1,
    )


def get_user_display_name(user):
    if hasattr(user, 'profile'):
        return user.profile.display_name
    return user.username


def get_user_post_stats(user):
    user_posts = Post.objects.filter(author=user)
    return {
        'published_count': user_posts.filter(status='published').count(),
        'draft_count': user_posts.filter(status='draft').count(),
        'total_count': user_posts.count(),
    }


def create_notification(
    recipient,
    actor,
    notification_type,
    message,
    target_url='',
    post=None,
    comment=None,
    private_message=None,
    friend_request=None,
):
    if recipient is None:
        return None
    if actor and recipient.id == actor.id:
        return None

    return Notification.objects.create(
        recipient=recipient,
        actor=actor,
        notification_type=notification_type,
        message=message[:255],
        target_url=target_url[:255],
        post=post,
        comment=comment,
        private_message=private_message,
        friend_request=friend_request,
    )


def extract_mentioned_usernames(content):
    mentioned_usernames = []
    for username_match in MENTION_USERNAME_PATTERN.finditer(content or ''):
        mentioned_username = username_match.group(1).strip()
        if mentioned_username and mentioned_username not in mentioned_usernames:
            mentioned_usernames.append(mentioned_username)
    return mentioned_usernames


def notify_mentioned_users(comment, post, actor, excluded_user_ids=None):
    mentioned_usernames = extract_mentioned_usernames(comment.content)
    if not mentioned_usernames:
        return

    excluded_user_ids = set(excluded_user_ids or [])
    mentioned_users = User.objects.filter(username__in=mentioned_usernames).select_related('profile')
    notification_target_url = reverse('post_detail', args=[post.id])
    for mentioned_user in mentioned_users:
        if mentioned_user.id in excluded_user_ids:
            continue
        if users_are_blocked_between(actor, mentioned_user):
            continue
        can_read_post = filter_readable_posts(
            Post.objects.filter(id=post.id),
            mentioned_user,
        ).exists()
        if not can_read_post:
            continue
        create_notification(
            recipient=mentioned_user,
            actor=actor,
            notification_type='mention',
            message=f'{get_user_display_name(actor)} 在文章《{post.title}》的评论里提到了你。',
            target_url=notification_target_url,
            post=post,
            comment=comment,
        )


def get_readable_post_or_404(post_id, user):
    readable_posts = filter_readable_posts(
        Post.objects.filter(id=post_id),
        user,
    )
    return get_object_or_404(readable_posts)


def get_post_interaction_context(post, request_user):
    reaction_counts = {
        reaction_type: 0
        for reaction_type, _ in PostReaction.REACTION_CHOICES
    }
    reaction_rows = PostReaction.objects.filter(
        post=post,
    ).values(
        'reaction_type',
    ).annotate(
        count=Count('id'),
    )
    for reaction_row in reaction_rows:
        reaction_counts[reaction_row['reaction_type']] = reaction_row['count']

    user_reaction_type = ''
    if request_user.is_authenticated:
        user_reaction_type = (
            PostReaction.objects.filter(user=request_user, post=post)
            .values_list('reaction_type', flat=True)
            .first()
            or ''
        )

    return {
        'like_count': post.likes.count(),
        'is_liked': (
            request_user.is_authenticated
            and PostLike.objects.filter(user=request_user, post=post).exists()
        ),
        'reaction_options': [
            {
                'value': reaction_type,
                'label': reaction_label,
                'count': reaction_counts.get(reaction_type, 0),
                'icon_class': REACTION_ICON_MAP.get(reaction_type, 'fas fa-circle'),
                'is_active': user_reaction_type == reaction_type,
            }
            for reaction_type, reaction_label in PostReaction.REACTION_CHOICES
        ],
        'user_reaction_type': user_reaction_type,
    }


def can_moderate_comment(user, comment):
    return (
        user.is_authenticated
        and (
            user.is_superuser
            or comment.post.author_id == user.id
        )
    )


def get_category_counts(posts):
    counter = Counter({
        row['category']: row['count']
        for row in posts.exclude(category='').values('category').annotate(count=Count('id'))
    })
    categories = [
        {'value': value, 'name': label, 'count': counter[value]}
        for value, label in Post.CATEGORY_CHOICES
        if counter[value]
    ]

    known_categories = {value for value, _ in Post.CATEGORY_CHOICES}
    categories.extend(
        {'value': value, 'name': value, 'count': count}
        for value, count in counter.most_common()
        if value not in known_categories
    )
    return categories


def build_archive_groups(posts):
    archive_groups = []
    group_lookup = {}

    for post in posts:
        local_created_at = timezone.localtime(post.created_at)
        group_key = (local_created_at.year, local_created_at.month)
        if group_key not in group_lookup:
            archive_group = {
                'year': local_created_at.year,
                'month': local_created_at.month,
                'label': f'{local_created_at.year} 年 {local_created_at.month} 月',
                'posts': [],
            }
            group_lookup[group_key] = archive_group
            archive_groups.append(archive_group)

        group_lookup[group_key]['posts'].append(post)

    return archive_groups


def build_tag_counts(posts):
    return [
        {'name': row['name'], 'count': row['count']}
        for row in Tag.objects.filter(posts__in=posts)
        .exclude(normalized_name__startswith='daily:')
        .values('name')
        .annotate(count=Count('posts', distinct=True))
        .order_by('-count', 'normalized_name')
    ]


def normalize_managed_tag(raw_tag):
    return (raw_tag or '').strip()


def is_invalid_managed_tag(tag):
    return len(tag) > 50 or bool(re.search(r'[,，;；\s]', tag))


def is_reserved_system_tag(tag):
    return tag.casefold().startswith('daily:')


def replace_post_tag(post, source_tag, target_tag):
    new_tags = []
    normalized_new_tags = set()
    changed = False
    normalized_source_tag = Tag.normalize_name(source_tag)

    for current_tag in post.tag_list:
        if Tag.normalize_name(current_tag) == normalized_source_tag:
            next_tag = target_tag
            changed = True
        else:
            next_tag = current_tag

        normalized_next_tag = Tag.normalize_name(next_tag)
        if next_tag and normalized_next_tag not in normalized_new_tags:
            new_tags.append(next_tag)
            normalized_new_tags.add(normalized_next_tag)

    if not changed:
        return False

    updated_tags = ','.join(new_tags)
    if len(updated_tags) > Post._meta.get_field('tags').max_length:
        raise ValueError('合并后的文章标签总长度超过 200 个字符。')
    post.tags = updated_tags
    post.save(update_fields=['tags'])
    return True


def merge_post_tag(source_tag, target_tag):
    updated_post_count = 0
    with transaction.atomic():
        candidate_posts = Post.objects.select_for_update().filter(
            tag_links__tag__normalized_name=Tag.normalize_name(source_tag),
        ).distinct()
        for post in candidate_posts:
            if replace_post_tag(post, source_tag, target_tag):
                updated_post_count += 1
    return updated_post_count


def get_display_tags(post):
    display_tags = []
    for tag in post.tag_list:
        if tag.casefold().startswith('daily:'):
            continue
        if tag not in display_tags:
            display_tags.append(tag)
    return display_tags


def filter_posts_by_tag(posts, selected_tag):
    if not selected_tag:
        return posts
    return posts.filter(
        tag_links__tag__normalized_name=Tag.normalize_name(selected_tag),
    ).distinct()


def get_related_posts(post, request_user, limit=3):
    source_tag_ids = list(
        post.tag_objects.exclude(normalized_name__startswith='daily:')
        .values_list('id', flat=True)
    )
    if not source_tag_ids:
        return []

    return list(
        get_readable_published_posts(request_user).exclude(
            id=post.id,
        ).filter(
            tag_links__tag_id__in=source_tag_ids,
        ).annotate(
            shared_tag_count=Count('tag_links__tag', distinct=True),
        ).select_related(
        'author',
        'author__profile',
        ).order_by(
            '-shared_tag_count',
            '-created_at',
        )[:limit]
    )


def get_series_posts(post, request_user):
    if not post.series_title:
        return []
    return list(
        filter_readable_posts(
            Post.objects.filter(series_title=post.series_title),
            request_user,
        ).select_related(
            'author',
            'author__profile',
        ).order_by(
            'series_order',
            'created_at',
            'id',
        )
    )


@require_GET
def homepage_carousel_image(request, image_file_name):
    cached_image_url = get_or_create_homepage_cached_image_url(image_file_name)
    return HttpResponseRedirect(cached_image_url)


@require_GET
def home(request):
    owner, owner_profile = get_site_owner_profile()
    readable_posts = get_readable_published_posts(request.user).select_related(
        'author',
        'author__profile',
    )
    recent_posts = list(readable_posts[:3])
    for recent_post in recent_posts:
        recent_post.card_display_tags = get_display_tags(recent_post)[:3]

    if request.user.is_authenticated:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
    else:
        profile = owner_profile

    featured_post = recent_posts[0] if recent_posts else None
    carousel_slides = build_homepage_carousel_slides()
    return render(request, 'home.html', {
        'carousel_slides': carousel_slides,
        'recent_posts': recent_posts,
        'featured_post': featured_post,
        'profile': profile,
        'owner': owner,
    })


@require_GET
def index(request):
    owner, owner_profile = get_site_owner_profile()
    all_posts = get_readable_published_posts(request.user)
    if request.user.is_authenticated:
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        about_posts = Post.objects.filter(
            author=request.user,
            status='published',
        )
    else:
        profile = owner_profile
        about_posts = Post.objects.filter(
            author=owner,
            status='published',
            visibility='public',
        ) if owner else Post.objects.none()

    selected_category = get_bounded_query_value(request, 'category', 50)
    selected_author = get_bounded_query_value(request, 'author', 150)
    selected_tag = get_bounded_query_value(request, 'tag', 50)
    search_query = get_bounded_query_value(request, 'q', 100)
    selected_category_label = Post.CATEGORY_LABELS.get(selected_category, selected_category)
    selected_author_post = all_posts.filter(
        author__username=selected_author
    ).select_related('author__profile').first() if selected_author else None
    selected_author_label = (
        selected_author_post.author.profile.display_name
        if selected_author_post and hasattr(selected_author_post.author, 'profile')
        else selected_author
    )
    author_posts = (
        all_posts.filter(author__username=selected_author)
        if selected_author
        else all_posts
    )
    category_counts = get_category_counts(author_posts)
    posts = author_posts
    published_count = all_posts.count()

    if search_query:
        matched_categories = [
            value for value, label in Post.CATEGORY_CHOICES
            if search_query.lower() in value.lower() or search_query.lower() in label.lower()
        ]
        search_filter = (
            Q(title__icontains=search_query)
            | Q(content__icontains=search_query)
            | Q(tags__icontains=search_query)
            | Q(category__icontains=search_query)
        )
        if matched_categories:
            search_filter |= Q(category__in=matched_categories)
        posts = posts.filter(search_filter)

    if selected_category:
        posts = posts.filter(category=selected_category)

    posts = filter_posts_by_tag(posts, selected_tag)
    result_count = len(posts) if isinstance(posts, list) else posts.count()

    pagination_params = request.GET.copy()
    pagination_params.pop('page', None)
    pagination_params.pop('date', None)
    pagination_query = pagination_params.urlencode()
    pagination_prefix = f'{pagination_query}&' if pagination_query else ''

    clear_category_query = get_clear_query(request, 'category')
    clear_search_query = get_clear_query(request, 'q')
    clear_tag_query = get_clear_query(request, 'tag')
    clear_author_query = get_clear_query(request, 'author')
    active_filter_chips = build_active_filter_chips(
        search_query,
        selected_category,
        selected_category_label,
        selected_tag,
        selected_author,
        selected_author_label,
        clear_search_query,
        clear_category_query,
        clear_tag_query,
        clear_author_query,
    )

    paginator = Paginator(posts, 6)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
    page_posts = list(page_obj.object_list)
    for post in page_posts:
        prepare_post_card_display(post, search_query)
    page_obj.object_list = page_posts
    popular_posts = list(author_posts.order_by('-views_count', '-created_at')[:5])
    recently_read_posts = get_recently_read_posts(request)
    return render(request, 'index.html', {
        'posts': page_obj,
        'page_obj': page_obj,
        'pagination_page_range': build_elided_page_range(page_obj),
        'selected_category': selected_category,
        'selected_category_label': selected_category_label,
        'selected_author': selected_author,
        'selected_author_label': selected_author_label,
        'is_my_posts_filter': (
            request.user.is_authenticated
            and selected_author == request.user.username
        ),
        'search_query': search_query,
        'selected_tag': selected_tag,
        'pagination_prefix': pagination_prefix,
        'result_count': result_count,
        'clear_category_query': clear_category_query,
        'clear_search_query': clear_search_query,
        'clear_tag_query': clear_tag_query,
        'clear_author_query': clear_author_query,
        'active_filter_chips': active_filter_chips,
        'category_counts': category_counts,
        'top_categories': category_counts[:10],
        'profile': profile,
        'published_count': about_posts.count(),
        'total_views': about_posts.aggregate(total=Sum('views_count'))['total'] or 0,
        'recent_posts': author_posts[:5],
        'popular_posts': popular_posts,
        'recently_read_posts': recently_read_posts,
    })


@require_GET
def author_profile(request, username):
    author = get_object_or_404(
        User.objects.select_related('profile'),
        username=username,
    )
    profile, _ = UserProfile.objects.get_or_create(user=author)
    readable_posts = filter_readable_posts(
        Post.objects.filter(author=author),
        request.user,
    ).select_related(
        'author',
        'author__profile',
    ).order_by('-created_at')

    paginator = Paginator(readable_posts, 6)
    page_obj = paginator.get_page(request.GET.get('page'))
    page_posts = list(page_obj.object_list)
    for post in page_posts:
        post.card_display_tags = get_display_tags(post)[:3]
    page_obj.object_list = page_posts

    return render(request, 'author_profile.html', {
        'author_profile_user': author,
        'author_profile_data': profile,
        'relationship_status': get_relationship_status(request.user, author),
        'posts': page_obj,
        'page_obj': page_obj,
        'pagination_page_range': build_elided_page_range(page_obj),
        'published_count': readable_posts.count(),
        'total_views': readable_posts.aggregate(total=Sum('views_count'))['total'] or 0,
    })


@require_GET
def archive_view(request):
    posts = get_readable_published_posts(request.user).select_related(
        'author',
        'author__profile',
    )
    archive_groups = build_archive_groups(posts)
    return render(request, 'archive.html', {
        'archive_groups': archive_groups,
    })


@require_GET
def tags_view(request):
    posts = get_readable_published_posts(request.user)
    tag_search_query = get_bounded_query_value(request, 'q', 100)
    tag_sort = request.GET.get('sort', 'count').strip()
    selected_tag = get_bounded_query_value(request, 'selected', 50)
    if tag_sort not in {'count', 'name'}:
        tag_sort = 'count'

    tag_counts = build_tag_counts(posts)
    if tag_search_query:
        normalized_search_query = tag_search_query.lower()
        tag_counts = [
            tag_count
            for tag_count in tag_counts
            if normalized_search_query in tag_count['name'].lower()
        ]
    if tag_sort == 'name':
        tag_counts = sorted(
            tag_counts,
            key=lambda tag_count: tag_count['name'].lower(),
        )

    return render(request, 'tags.html', {
        'tag_counts': tag_counts,
        'tag_search_query': tag_search_query,
        'tag_sort': tag_sort,
        'selected_tag': selected_tag,
    })


@login_required
def tag_manager(request):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    if request.method == 'POST':
        source_tag = normalize_managed_tag(request.POST.get('source_tag', ''))
        target_tag = normalize_managed_tag(request.POST.get('target_tag', ''))

        if not source_tag or not target_tag:
            messages.error(request, '请选择旧标签，并填写要合并成的新标签。')
        elif Tag.normalize_name(source_tag) == Tag.normalize_name(target_tag):
            messages.info(request, '旧标签和新标签相同，不需要合并。')
        elif is_reserved_system_tag(source_tag) or is_reserved_system_tag(target_tag):
            messages.error(request, '系统标签不能在这里合并。')
        elif is_invalid_managed_tag(source_tag) or is_invalid_managed_tag(target_tag):
            messages.error(request, '标签名不能超过 50 个字符，也不能包含空格、逗号或分号。')
        else:
            try:
                updated_post_count = merge_post_tag(source_tag, target_tag)
            except ValueError as error:
                messages.error(request, str(error))
            else:
                if updated_post_count:
                    messages.success(
                        request,
                        f'已把 {updated_post_count} 篇文章里的“{source_tag}”合并为“{target_tag}”。',
                    )
                else:
                    messages.info(request, '没有文章包含这个旧标签。')

        return redirect('tag_manager')

    all_posts = Post.objects.all()
    tag_counts = build_tag_counts(all_posts)
    return render(request, 'tag_manager.html', {
        'tag_counts': tag_counts,
        'tag_total': len(tag_counts),
    })


def register(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        form = RegistrationRequestForm(request.POST)
        retry_after = consume_rate_limit(
            request,
            'register-request',
            limit=5,
            window_seconds=900,
            block_seconds=1800,
        )
        if retry_after:
            form.add_error(None, f'提交过于频繁，请在 {retry_after} 秒后重试。')
        elif form.is_valid():
            email = form.cleaned_data['email']
            if User.objects.filter(email__iexact=email).exists():
                messages.success(request, '申请已收到；符合条件时会发送后续邮件。')
                return redirect('register')
            registration_request, created = RegistrationRequest.objects.get_or_create(
                email=email,
            )
            if not created:
                if (
                    registration_request.status in {
                        RegistrationRequest.STATUS_REJECTED,
                        RegistrationRequest.STATUS_USED,
                    }
                    or registration_request.is_code_expired
                ):
                    registration_request.reopen()
                    registration_request.save()
                messages.success(request, '申请已收到；符合条件时会发送后续邮件。')
                return redirect('register')
            messages.success(request, '申请已收到；符合条件时会发送后续邮件。')
            return redirect('register')
    else:
        form = RegistrationRequestForm()

    return render(request, 'auth_form.html', {
        'form': form,
        'page_title': '申请注册',
        'page_description': '先提交邮箱，审核通过后会收到一次性注册码。',
        'submit_text': '提交申请',
        'submit_icon': 'fas fa-paper-plane',
        'switch_text': '已经收到注册码？',
        'switch_url_name': 'complete_registration',
        'switch_link_text': '去完成注册',
    })


def complete_registration(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        form = CompleteRegistrationForm(request.POST)
        retry_after = consume_rate_limit(
            request,
            'complete-registration',
            limit=10,
            window_seconds=900,
            block_seconds=1800,
        )
        if retry_after:
            form.add_error(None, f'验证过于频繁，请在 {retry_after} 秒后重试。')
        elif form.is_valid():
            try:
                user = form.save()
            except forms.ValidationError as validation_error:
                form.add_error(None, validation_error)
            else:
                login(request, user)
                messages.success(request, '注册成功，欢迎来到白车轴草。')
                return redirect('index')
    else:
        form = CompleteRegistrationForm()

    return render(request, 'auth_form.html', {
        'form': form,
        'page_title': '完成注册',
        'page_description': '输入邮件里的注册码，再设置账号信息。',
        'submit_text': '完成注册',
        'submit_icon': 'fas fa-user-check',
        'switch_text': '还没有注册码？',
        'switch_url_name': 'register',
        'switch_link_text': '先申请注册',
    })


def require_superuser(request):
    if request.user.is_authenticated and request.user.is_superuser:
        return None
    return HttpResponseForbidden('只有超级用户可以访问注册审核。')


@login_required
@require_GET
def registration_requests(request):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    requests_by_status = {}
    for status_value, _ in RegistrationRequest.STATUS_CHOICES:
        requests_by_status[status_value] = RegistrationRequest.objects.filter(
            status=status_value,
        ).select_related(
            'approved_by',
        )
    pending_count = requests_by_status[RegistrationRequest.STATUS_PENDING].count()

    return render(request, 'registration_requests.html', {
        'requests_by_status': requests_by_status,
        'pending_count': pending_count,
        'status_choices': RegistrationRequest.STATUS_CHOICES,
    })


@login_required
@require_POST
def approve_registration_request(request, request_id):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    registration_request = get_object_or_404(RegistrationRequest, id=request_id)
    if registration_request.status != RegistrationRequest.STATUS_PENDING:
        messages.info(request, '只有待审核申请可以通过。')
        return redirect('registration_requests')

    completion_url = request.build_absolute_uri(reverse('complete_registration'))
    try:
        approve_registration_request_service(
            registration_request,
            request.user,
            completion_url,
        )
    except RegistrationRequestAlreadyReviewed:
        messages.info(request, '只有待审核申请可以通过。')
        return redirect('registration_requests')
    except Exception:
        logger.exception('Registration approval email failed for request %s.', request_id)
        messages.error(request, '审批已保存，但邮件发送失败；请使用重发注册码。')
        return redirect('registration_requests')

    messages.success(request, '已通过并发送注册码。')
    return redirect('registration_requests')


@login_required
@require_POST
def resend_registration_code(request, request_id):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    registration_request = get_object_or_404(RegistrationRequest, id=request_id)
    if registration_request.status != RegistrationRequest.STATUS_APPROVED:
        messages.info(request, '只有已通过且未使用的申请可以重发注册码。')
        return redirect('registration_requests')

    completion_url = request.build_absolute_uri(reverse('complete_registration'))
    try:
        resend_registration_code_service(
            registration_request,
            request.user,
            completion_url,
        )
    except RegistrationRequestCannotResend:
        messages.info(request, '只有已通过且未使用的申请可以重发注册码。')
        return redirect('registration_requests')
    except Exception:
        logger.exception('Registration code resend failed for request %s.', request_id)
        messages.error(request, '注册码已更新，但邮件发送失败；可以再次重发。')
        return redirect('registration_requests')

    messages.success(request, '已重新发送注册码。')
    return redirect('registration_requests')


@login_required
@require_POST
def reject_registration_request(request, request_id):
    forbidden_response = require_superuser(request)
    if forbidden_response is not None:
        return forbidden_response

    registration_request = get_object_or_404(RegistrationRequest, id=request_id)
    if registration_request.status != RegistrationRequest.STATUS_PENDING:
        messages.info(request, '只有待审核申请可以拒绝。')
        return redirect('registration_requests')

    try:
        reject_registration_request_service(registration_request, request.user)
    except RegistrationRequestAlreadyReviewed:
        messages.info(request, '只有待审核申请可以拒绝。')
        return redirect('registration_requests')

    messages.success(request, '已拒绝这个注册申请。')
    return redirect('registration_requests')


def login_view(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        form = ChineseAuthenticationForm(request, data=request.POST)
        retry_after = consume_rate_limit(
            request,
            'login',
            limit=20,
            window_seconds=900,
            block_seconds=900,
        )
        if retry_after:
            form.add_error(None, f'登录尝试过多，请在 {retry_after} 秒后重试。')
        elif form.is_valid():
            login(request, form.get_user())
            messages.success(request, '登录成功，欢迎回来。')
            next_url = request.GET.get('next')
            if not url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=not settings.DEBUG,
            ):
                next_url = 'index'
            return redirect(next_url)
    else:
        form = ChineseAuthenticationForm()

    return render(request, 'auth_form.html', {
        'form': form,
        'page_title': '登录账号',
        'submit_text': '登录',
        'switch_text': '还没有账号？',
        'switch_url_name': 'register',
        'switch_link_text': '去注册',
    })

@login_required
@require_POST
def logout_view(request):
    logout(request)
    messages.success(request, '已退出登录。')
    return redirect('home')

@login_required
def user_center(request):
    profile, _ = UserProfile.objects.get_or_create(user=request.user)

    if request.method == 'POST':
        form = UserCenterForm(request.POST, request.FILES, instance=profile, user=request.user)
        if form.is_valid():
            profile = form.save(commit=False)
            cropped_avatar_data = request.POST.get('cropped_avatar')
            if cropped_avatar_data:
                try:
                    profile.avatar = build_image_file_from_data_url(
                        cropped_avatar_data,
                        'avatar',
                    )
                except ValueError as error:
                    messages.error(request, str(error))
                    return render(request, 'user_center.html', {
                        'form': form,
                        'profile': profile,
                        'stats': get_user_post_stats(request.user),
                    })
            elif request.POST.get('clear_avatar') == 'true':
                profile.avatar = None
            request.user.email = form.cleaned_data.get('email', '')
            request.user.save(update_fields=['email'])
            profile.save()
            messages.success(request, '用户资料已保存。')
            return redirect('user_center')
    else:
        form = UserCenterForm(instance=profile, user=request.user)

    stats = get_user_post_stats(request.user)
    return render(request, 'user_center.html', {
        'form': form,
        'profile': profile,
        'stats': stats,
    })


@login_required
@require_GET
def friends_view(request):
    search_query = get_bounded_query_value(request, 'q', 100)
    friends = get_friends_for_user(request.user)
    incoming_requests = FriendRequest.objects.filter(
        receiver=request.user,
        status='pending',
    ).select_related('sender__profile')
    outgoing_requests = FriendRequest.objects.filter(
        sender=request.user,
        status='pending',
    ).select_related('receiver__profile')

    search_results = []
    if search_query:
        matched_users = User.objects.filter(
            Q(username__icontains=search_query)
            | Q(profile__nickname__icontains=search_query)
        ).exclude(id=request.user.id).select_related('profile').distinct()[:30]
        for matched_user in matched_users:
            matched_user.relationship_status = get_relationship_status(request.user, matched_user)
            search_results.append(matched_user)

    return render(request, 'friends.html', {
        'friends': friends,
        'incoming_requests': incoming_requests,
        'outgoing_requests': outgoing_requests,
        'search_query': search_query,
        'search_results': search_results,
    })


@login_required
@require_POST
def send_friend_request(request, user_id):
    redirect_url = get_safe_post_next_url(request, reverse('friends'))
    retry_after = consume_rate_limit(
        request,
        'friend-request',
        limit=30,
        window_seconds=3600,
        block_seconds=900,
    )
    if retry_after:
        messages.error(
            request,
            f'好友申请发送过于频繁，请在 {retry_after} 秒后重试。',
        )
        return redirect(redirect_url)

    target_user = get_object_or_404(User, id=user_id)
    if target_user == request.user:
        messages.error(request, '不能添加自己为好友。')
        return redirect(redirect_url)
    if users_are_blocked_between(request.user, target_user):
        messages.error(request, '你们之间已存在屏蔽关系，不能发送好友申请。')
        return redirect(redirect_url)
    if are_friends(request.user, target_user):
        messages.info(request, '你们已经是好友。')
        return redirect(redirect_url)
    if FriendRequest.objects.filter(
        sender=target_user,
        receiver=request.user,
        status='pending',
    ).exists():
        messages.info(request, '对方已向你发送好友申请，请在待处理申请中操作。')
        return redirect(redirect_url)

    friend_request, created = FriendRequest.objects.get_or_create(
        sender=request.user,
        receiver=target_user,
        defaults={'status': 'pending'},
    )
    if not created:
        friend_request.status = 'pending'
        friend_request.save(update_fields=['status', 'updated_at'])
    create_notification(
        recipient=target_user,
        actor=request.user,
        notification_type='friend_request_received',
        message=f'{get_user_display_name(request.user)} 向你发送了好友申请。',
        target_url=reverse('friends'),
        friend_request=friend_request,
    )
    messages.success(request, '好友申请已发送。')
    return redirect(redirect_url)


@login_required
@require_POST
def accept_friend_request(request, request_id):
    with transaction.atomic():
        friend_request = get_object_or_404(
            FriendRequest.objects.select_for_update(),
            id=request_id,
            receiver=request.user,
            status='pending',
        )
        if users_are_blocked_between(friend_request.sender, friend_request.receiver):
            friend_request.status = 'rejected'
            friend_request.save(update_fields=['status', 'updated_at'])
            messages.error(request, '你们之间已存在屏蔽关系，不能接受好友申请。')
            return redirect('friends')
        Friendship.connect(friend_request.sender, friend_request.receiver)
        friend_request.status = 'accepted'
        friend_request.save(update_fields=['status', 'updated_at'])
        FriendRequest.objects.filter(
            sender=request.user,
            receiver=friend_request.sender,
            status='pending',
        ).update(status='accepted', updated_at=timezone.now())
    create_notification(
        recipient=friend_request.sender,
        actor=request.user,
        notification_type='friend_request_accepted',
        message=f'{get_user_display_name(request.user)} 接受了你的好友申请。',
        target_url=reverse('friends'),
        friend_request=friend_request,
    )
    messages.success(request, '好友申请已接受。')
    return redirect('friends')


@login_required
@require_POST
def reject_friend_request(request, request_id):
    friend_request = get_object_or_404(
        FriendRequest,
        id=request_id,
        receiver=request.user,
        status='pending',
    )
    friend_request.status = 'rejected'
    friend_request.save(update_fields=['status', 'updated_at'])
    messages.info(request, '好友申请已拒绝。')
    return redirect('friends')


@login_required
@require_POST
def cancel_friend_request(request, request_id):
    friend_request = get_object_or_404(
        FriendRequest,
        id=request_id,
        sender=request.user,
        status='pending',
    )
    friend_request.status = 'cancelled'
    friend_request.save(update_fields=['status', 'updated_at'])
    messages.info(request, '好友申请已取消。')
    return redirect('friends')


@login_required
@require_POST
def remove_friend(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    deleted_count, _ = Friendship.objects.filter(
        user_low_id=min(request.user.id, target_user.id),
        user_high_id=max(request.user.id, target_user.id),
    ).delete()
    if deleted_count:
        messages.success(request, '好友已删除。')
    else:
        messages.error(request, '当前用户不是你的好友。')
    return redirect('friends')


@login_required
@require_POST
def block_user(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    fallback_url = reverse('author_profile', args=[target_user.username])
    redirect_url = get_safe_post_next_url(request, fallback_url)
    if target_user == request.user:
        messages.error(request, '不能屏蔽自己。')
        return redirect(redirect_url)

    UserBlock.objects.get_or_create(blocker=request.user, blocked=target_user)
    delete_friendship_between(request.user, target_user)
    FriendRequest.objects.filter(
        Q(sender=request.user, receiver=target_user)
        | Q(sender=target_user, receiver=request.user),
        status='pending',
    ).update(status='cancelled', updated_at=timezone.now())
    messages.success(request, '已屏蔽这个用户，并停止你们之间的互动。')
    return redirect(redirect_url)


@login_required
@require_POST
def unblock_user(request, user_id):
    target_user = get_object_or_404(User, id=user_id)
    fallback_url = reverse('author_profile', args=[target_user.username])
    redirect_url = get_safe_post_next_url(request, fallback_url)
    UserBlock.objects.filter(blocker=request.user, blocked=target_user).delete()
    messages.success(request, '已解除屏蔽。')
    return redirect(redirect_url)


@login_required
@require_GET
def conversations_view(request):
    friends = get_friends_for_user(request.user)
    friend_ids = [friend.id for friend in friends]
    friend_by_id = {friend.id: friend for friend in friends}
    conversation_messages = PrivateMessage.objects.filter(
        Q(sender=request.user, recipient_id__in=friend_ids)
        | Q(sender_id__in=friend_ids, recipient=request.user)
    ).annotate(
        counterpart_id=Case(
            When(sender=request.user, then=F('recipient_id')),
            default=F('sender_id'),
            output_field=IntegerField(),
        )
    )
    last_message_ids = list(
        conversation_messages.values('counterpart_id')
        .annotate(last_message_id=Max('id'))
        .values_list('last_message_id', flat=True)
    )
    last_messages = {
        (
            message.recipient_id
            if message.sender_id == request.user.id
            else message.sender_id
        ): message
        for message in PrivateMessage.objects.filter(id__in=last_message_ids)
    }
    unread_counts = {
        row['sender_id']: row['count']
        for row in PrivateMessage.objects.filter(
            sender_id__in=friend_ids,
            recipient=request.user,
            is_read=False,
        ).values('sender_id').annotate(count=Count('id'))
    }
    conversation_items = [
        {
            'friend': friend_by_id[friend_id],
            'last_message': last_messages.get(friend_id),
            'unread_count': unread_counts.get(friend_id, 0),
        }
        for friend_id in friend_ids
    ]

    conversation_items.sort(
        key=lambda item: (
            item['last_message'].created_at.timestamp()
            if item['last_message']
            else 0
        ),
        reverse=True,
    )
    return render(request, 'conversations.html', {
        'conversation_items': conversation_items,
    })


@login_required
def conversation_view(request, user_id):
    friend = get_object_or_404(User.objects.select_related('profile'), id=user_id)
    if users_are_blocked_between(request.user, friend):
        messages.error(request, '你们之间已存在屏蔽关系，不能发送私信。')
        return redirect('friends')
    if not are_friends(request.user, friend):
        messages.error(request, '只有好友之间可以发送私信。')
        return redirect('friends')

    if request.method == 'POST':
        message_form = PrivateMessageForm(request.POST)
        retry_after = consume_rate_limit(
            request,
            'private-message',
            limit=60,
            window_seconds=3600,
            block_seconds=900,
        )
        if retry_after:
            message_form.add_error(
                None,
                f'私信发送过于频繁，请在 {retry_after} 秒后重试。',
            )
        elif message_form.is_valid():
            private_message = message_form.save(commit=False)
            private_message.sender = request.user
            private_message.recipient = friend
            private_message.save()
            create_notification(
                recipient=friend,
                actor=request.user,
                notification_type='private_message',
                message=f'{get_user_display_name(request.user)} 给你发来一条私信。',
                target_url=reverse('conversation', args=[request.user.id]),
                private_message=private_message,
            )
            return redirect('conversation', user_id=friend.id)
    else:
        message_form = PrivateMessageForm()

    conversation_messages = PrivateMessage.objects.filter(
        Q(sender=request.user, recipient=friend)
        | Q(sender=friend, recipient=request.user)
    ).select_related('sender__profile')

    return render(request, 'conversation.html', {
        'friend': friend,
        'conversation_messages': conversation_messages,
        'message_form': message_form,
        'mark_read_url': reverse('mark_conversation_read', args=[friend.id]),
    })


@login_required
@require_POST
def mark_conversation_read(request, user_id):
    friend = get_object_or_404(User, id=user_id)
    if not are_friends(request.user, friend):
        return JsonResponse({'error': '只有好友会话可以标记已读。'}, status=403)
    updated_count = PrivateMessage.objects.filter(
        sender=friend,
        recipient=request.user,
        is_read=False,
    ).update(is_read=True)
    return JsonResponse({'updated_count': updated_count})


@login_required
@require_GET
def favorite_posts(request):
    readable_post_ids = filter_readable_posts(
        Post.objects.all(),
        request.user,
    ).values('id')
    favorites = PostFavorite.objects.filter(
        user=request.user,
        post_id__in=readable_post_ids,
    ).select_related(
        'post',
        'post__author',
        'post__author__profile',
    ).order_by('-created_at')

    paginator = Paginator(favorites, 6)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'favorites.html', {
        'favorites': page_obj,
        'page_obj': page_obj,
        'pagination_page_range': build_elided_page_range(page_obj),
    })


@login_required
@require_POST
def toggle_favorite(request, post_id):
    post = get_readable_post_or_404(post_id, request.user)
    favorite = PostFavorite.objects.filter(
        user=request.user,
        post=post,
    ).first()

    if favorite:
        favorite.delete()
        is_favorited = False
        response_message = '已取消收藏。'
    else:
        PostFavorite.objects.create(user=request.user, post=post)
        is_favorited = True
        response_message = '文章已加入收藏。'

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'active': is_favorited,
            'label': '已收藏' if is_favorited else '收藏',
            'message': response_message,
        })

    if is_favorited:
        messages.success(request, response_message)
    else:
        messages.info(request, response_message)

    fallback_url = reverse('post_detail', args=[post.id])
    return redirect(get_safe_post_next_url(request, fallback_url))


@login_required
@require_POST
def toggle_post_like(request, post_id):
    post = get_readable_post_or_404(post_id, request.user)
    post_like = PostLike.objects.filter(
        user=request.user,
        post=post,
    ).first()

    if post_like:
        post_like.delete()
        is_liked = False
        response_message = '已取消点赞。'
    else:
        PostLike.objects.create(user=request.user, post=post)
        is_liked = True
        response_message = '已点赞。'

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'active': is_liked,
            'count': PostLike.objects.filter(post=post).count(),
            'label': '已赞' if is_liked else '点赞',
            'message': response_message,
        })

    if is_liked:
        messages.success(request, response_message)
    else:
        messages.info(request, response_message)

    fallback_url = reverse('post_detail', args=[post.id])
    return redirect(get_safe_post_next_url(request, fallback_url))


@login_required
@require_POST
def toggle_post_reaction(request, post_id):
    post = get_readable_post_or_404(post_id, request.user)
    reaction_type = (request.POST.get('reaction_type') or '').strip()
    allowed_reaction_types = {
        choice_value
        for choice_value, _ in PostReaction.REACTION_CHOICES
    }
    if reaction_type not in allowed_reaction_types:
        messages.error(request, '请选择有效的文章反应。')
        fallback_url = reverse('post_detail', args=[post.id])
        return redirect(get_safe_post_next_url(request, fallback_url))

    post_reaction = PostReaction.objects.filter(
        user=request.user,
        post=post,
    ).first()
    if post_reaction and post_reaction.reaction_type == reaction_type:
        post_reaction.delete()
        messages.info(request, '已取消这个反应。')
    elif post_reaction:
        post_reaction.reaction_type = reaction_type
        post_reaction.save(update_fields=['reaction_type', 'updated_at'])
        messages.success(request, '已更新文章反应。')
    else:
        PostReaction.objects.create(
            user=request.user,
            post=post,
            reaction_type=reaction_type,
        )
        messages.success(request, '已记录文章反应。')

    fallback_url = reverse('post_detail', args=[post.id])
    return redirect(get_safe_post_next_url(request, fallback_url))


@login_required
@require_GET
def notifications_view(request):
    selected_notification_status = request.GET.get('status', 'all').strip()
    selected_notification_type = request.GET.get('type', 'all').strip()
    if selected_notification_status not in {'all', 'unread', 'read'}:
        selected_notification_status = 'all'
    notification_type_labels = dict(Notification.TYPE_CHOICES)
    if selected_notification_type not in notification_type_labels:
        selected_notification_type = 'all'

    notifications = Notification.objects.filter(
        recipient=request.user,
    ).select_related(
        'actor',
        'actor__profile',
    )
    if selected_notification_status == 'unread':
        notifications = notifications.filter(is_read=False)
    elif selected_notification_status == 'read':
        notifications = notifications.filter(is_read=True)
    if selected_notification_type != 'all':
        notifications = notifications.filter(notification_type=selected_notification_type)
    notifications = notifications.order_by('-created_at')

    pagination_params = get_normalized_query_params(request)
    pagination_params.pop('page', None)
    pagination_query = pagination_params.urlencode()
    pagination_prefix = f'{pagination_query}&' if pagination_query else ''

    paginator = Paginator(notifications, 12)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'notifications.html', {
        'notifications': page_obj,
        'page_obj': page_obj,
        'pagination_page_range': build_elided_page_range(page_obj),
        'notification_status_options': [
            {'value': 'all', 'label': '全部状态'},
            {'value': 'unread', 'label': '未读'},
            {'value': 'read', 'label': '已读'},
        ],
        'notification_type_options': [
            {'value': 'all', 'label': '全部类型'},
            *[
                {'value': notification_type, 'label': notification_label}
                for notification_type, notification_label in Notification.TYPE_CHOICES
            ],
        ],
        'selected_notification_status': selected_notification_status,
        'selected_notification_type': selected_notification_type,
        'pagination_prefix': pagination_prefix,
    })


@login_required
@require_POST
def read_notification(request, notification_id):
    notification = get_object_or_404(
        Notification,
        id=notification_id,
        recipient=request.user,
    )
    notification.is_read = True
    notification.save(update_fields=['is_read'])

    fallback_url = reverse('notifications')
    target_url = notification.target_url or fallback_url
    if not url_has_allowed_host_and_scheme(
        target_url,
        allowed_hosts={request.get_host()},
        require_https=not settings.DEBUG,
    ):
        target_url = fallback_url
    return redirect(target_url)


@login_required
@require_POST
def mark_all_notifications_read(request):
    Notification.objects.filter(
        recipient=request.user,
        is_read=False,
    ).update(is_read=True)
    messages.success(request, '所有通知已标记为已读。')
    return redirect('notifications')


@login_required
def create_post(request):
    if request.method == 'POST':
        submission, submission_errors = parse_post_submission(request.POST)
        if submission_errors:
            for submission_error in submission_errors:
                messages.error(request, submission_error)
            return render(
                request,
                'create_post.html',
                build_post_form_context(
                    submission.title,
                    submission.category,
                    submission.tags,
                    submission.content,
                    submission.visibility,
                    submission.series_title,
                    submission.series_order,
                    submission.scheduled_publish_at,
                ),
            )

        title = submission.title
        category = submission.category
        tags = submission.tags
        series_title = submission.series_title
        series_order = submission.series_order
        content = submission.content
        cover = request.FILES.get('cover')
        cropped_cover_data = request.POST.get('cropped_cover')
        ai_cover_token = request.POST.get('ai_cover_token', '')
        status = submission.status
        scheduled_publish_at = submission.scheduled_publish_at
        visibility = submission.visibility

        if cropped_cover_data:
            try:
                cover = build_image_file_from_data_url(cropped_cover_data, 'cover')
            except ValueError as error:
                messages.error(request, str(error))
                return render(
                    request,
                    'create_post.html',
                    build_post_form_context(
                        title,
                        category,
                        tags,
                        content,
                        visibility,
                        series_title,
                        series_order,
                        scheduled_publish_at,
                    ),
                )
        elif cover:
            try:
                cover = validate_uploaded_image_file(cover)
            except ValueError as error:
                messages.error(request, str(error))
                return render(
                    request,
                    'create_post.html',
                    build_post_form_context(
                        title,
                        category,
                        tags,
                        content,
                        visibility,
                        series_title,
                        series_order,
                        scheduled_publish_at,
                    ),
                )

        ai_cover_data = None
        if cover is None:
            ai_cover_data = get_ai_cover_data(ai_cover_token)
            if ai_cover_data:
                try:
                    image_bytes = StartupPostCommand().download_pexels_image(
                        ai_cover_data['image_url']
                    )
                    image_extension = validate_image_bytes(image_bytes)
                    photo_id = ai_cover_data.get('photo_id', 'ai')
                    file_name = (
                        f"ai_{uuid.uuid4().hex[:8]}-{photo_id}.{image_extension}"
                    )
                    cover = ContentFile(image_bytes, name=file_name)
                    content = append_ai_cover_attribution(content, ai_cover_data)
                except (CommandError, ValueError):
                    messages.warning(request, '文章已保存，但 AI 封面下载失败。')

        post = Post(
            author=request.user,
            title=title,
            category=category,
            tags=tags,
            series_title=series_title,
            series_order=series_order,
            content=content,
            cover=cover,
            status=status,
            scheduled_publish_at=scheduled_publish_at,
            visibility=visibility
        )
        post.save()
        sync_post_body_images(post)
        
        if status == 'draft':
            return redirect('drafts')
        return redirect('index')

    return render(request, 'create_post.html', get_category_context())


@login_required
@require_POST
def generate_ai_post(request):
    topic = (request.POST.get('topic') or '').strip()
    requirements = (request.POST.get('requirements') or '').strip()
    article_length = (request.POST.get('article_length') or 'medium').strip()
    should_generate_cover = request.POST.get('generate_cover') == 'true'

    retry_after = consume_rate_limit(
        request,
        f'ai-generate-user-{request.user.id}',
        limit=AI_GENERATION_HOURLY_LIMIT,
        window_seconds=3600,
        block_seconds=3600,
    )
    if retry_after:
        response = JsonResponse({'error': 'AI 生成次数已达上限，请稍后重试。'}, status=429)
        response['Retry-After'] = str(retry_after)
        return response

    if not topic:
        return JsonResponse({'error': '请先填写文章主题。'}, status=400)
    if len(topic) > 200:
        return JsonResponse({'error': '文章主题不能超过 200 个字符。'}, status=400)
    if len(requirements) > 1000:
        return JsonResponse({'error': '补充要求不能超过 1000 个字符。'}, status=400)
    if article_length not in {'short', 'medium', 'long'}:
        return JsonResponse({'error': '文章长度选项无效。'}, status=400)

    current_timestamp = int(time.time())
    last_generation_timestamp = request.session.get('last_ai_generation_timestamp', 0)
    remaining_seconds = AI_GENERATION_COOLDOWN_SECONDS - (
        current_timestamp - last_generation_timestamp
    )
    if remaining_seconds > 0:
        return JsonResponse(
            {'error': f'请等待 {remaining_seconds} 秒后再生成。'},
            status=429,
        )

    request.session['last_ai_generation_timestamp'] = current_timestamp
    recent_titles = list(
        Post.objects.filter(author=request.user)
        .order_by('-created_at')
        .values_list('title', flat=True)[:20]
    )
    model = os.getenv('DEEPSEEK_MODEL', DEFAULT_DEEPSEEK_MODEL)
    generator = StartupPostCommand()

    try:
        generated_article = generator.generate_custom_article(
            model=model,
            topic=topic,
            requirements=requirements,
            article_length=article_length,
            recent_titles=recent_titles,
        )
    except CommandError:
        return JsonResponse(
            {'error': 'AI 生成失败，请稍后重试或联系管理员检查 DeepSeek 配置。'},
            status=502,
        )

    generated_tags = [
        tag.strip()
        for tag in generated_article['tags']
        if isinstance(tag, str) and tag.strip()
    ]
    response_data = {
        'title': generated_article['title'].strip()[:200],
        'category': generated_article['category'],
        'tags': ','.join(generated_tags)[:200],
        'content': generated_article['content'].strip(),
        'cover': None,
        'cover_warning': '',
    }

    if should_generate_cover:
        pexels_api_key = os.getenv('PEXELS_API_KEY')
        if not pexels_api_key:
            response_data['cover_warning'] = '服务器未配置 Pexels，文章已生成但没有自动封面。'
        else:
            try:
                pexels_photo = generator.search_pexels_photo(
                    pexels_api_key,
                    generated_article,
                    timezone.localdate(),
                )
                image_url = (
                    pexels_photo.get('src', {}).get('landscape')
                    or pexels_photo.get('src', {}).get('large')
                )
                if image_url:
                    cover_data = {
                        'image_url': image_url,
                        'photo_id': pexels_photo.get('id', 'ai'),
                        'photo_url': pexels_photo.get('url', ''),
                        'photographer': pexels_photo.get('photographer', ''),
                        'photographer_url': pexels_photo.get('photographer_url', ''),
                    }
                    response_data['cover'] = {
                        'preview_url': image_url,
                        'photographer': cover_data['photographer'],
                        'token': signing.dumps(cover_data, salt=AI_COVER_TOKEN_SALT),
                    }
                else:
                    response_data['cover_warning'] = '未找到可用的封面图片。'
            except CommandError:
                response_data['cover_warning'] = '封面匹配失败，文章正文仍可正常使用。'

    return JsonResponse(response_data)


@login_required
@require_GET
def drafts_list(request):
    posts = Post.objects.filter(author=request.user, status='draft').order_by('-updated_at')
    return render(request, 'drafts.html', {'posts': posts})

@login_required
def edit_post(request, post_id):
    post = get_object_or_404(Post, id=post_id, author=request.user)
    
    if request.method == 'POST':
        submission, submission_errors = parse_post_submission(request.POST)
        updated_post_values = submission.as_model_values()
        if submission_errors:
            for submission_error in submission_errors:
                messages.error(request, submission_error)
            return render(
                request,
                'create_post.html',
                build_edit_post_form_context(post, updated_post_values),
            )
        cover = request.FILES.get('cover')
        cropped_cover_data = request.POST.get('cropped_cover')
        should_update_cover = False
        updated_cover = None
        
        if cropped_cover_data:
            try:
                updated_cover = build_image_file_from_data_url(cropped_cover_data, 'cover')
                should_update_cover = True
            except ValueError as error:
                messages.error(request, str(error))
                return render(
                    request,
                    'create_post.html',
                    build_edit_post_form_context(post, updated_post_values),
                )
        elif cover:
            try:
                updated_cover = validate_uploaded_image_file(cover)
                should_update_cover = True
            except ValueError as error:
                messages.error(request, str(error))
                return render(
                    request,
                    'create_post.html',
                    build_edit_post_form_context(post, updated_post_values),
                )
        elif request.POST.get('clear_cover') == 'true':
            should_update_cover = True

        has_revision_changes = any(
            getattr(post, field_name) != field_value
            for field_name, field_value in updated_post_values.items()
        )
        if has_revision_changes:
            PostRevision.create_from_post(post, request.user)

        for field_name, field_value in updated_post_values.items():
            setattr(post, field_name, field_value)
        if should_update_cover:
            post.cover = updated_cover
        post.save()
        sync_post_body_images(post)
        
        if post.status == 'draft':
            return redirect('drafts')
        return redirect('post_detail', post_id=post.id)

    context = {'post': post}
    context.update(get_category_context(post))
    return render(request, 'create_post.html', context)

@login_required
@require_POST
def delete_draft(request, post_id):
    post = get_object_or_404(Post, id=post_id, author=request.user)
    if post.status == 'draft':
        post.delete()
    return redirect('drafts')

@require_GET
def post_detail(request, post_id):
    if request.user.is_authenticated:
        post = get_object_or_404(
            Post,
            Q(id=post_id),
            Q(get_currently_published_query(), visibility='public') | Q(author=request.user)
        )
    else:
        post = get_object_or_404(
            Post,
            Q(id=post_id),
            Q(get_currently_published_query(), visibility='public')
        )

    record_post_view(request, post)
    record_recently_read_post(request, post)

    comments_enabled = (
        post.status == 'published'
        and post.visibility == 'public'
        and (
            post.scheduled_publish_at is None
            or post.scheduled_publish_at <= timezone.now()
        )
    )
    can_moderate_comments = (
        request.user.is_authenticated
        and (
            request.user.is_superuser
            or post.author_id == request.user.id
        )
    )

    if comments_enabled:
        reply_queryset = Comment.objects.select_related(
            'author__profile'
        ).order_by('created_at')
        if not can_moderate_comments:
            reply_queryset = reply_queryset.filter(is_hidden=False)
        comments = post.comments.filter(
            parent__isnull=True
        ).select_related(
            'author__profile'
        ).prefetch_related(
            Prefetch('replies', queryset=reply_queryset)
        )
        if not can_moderate_comments:
            comments = comments.filter(is_hidden=False)
        comment_count = post.comments.filter(is_hidden=False).count()
        hidden_comment_count = post.comments.filter(is_hidden=True).count()
    else:
        comments = post.comments.none()
        comment_count = 0
        hidden_comment_count = 0

    if comments_enabled and request.user.is_authenticated:
        comment_form = CommentForm()
    else:
        comment_form = None

    can_view_revisions = request.user.is_authenticated and post.author == request.user
    post_revisions = (
        post.revisions.select_related('editor')[:5]
        if can_view_revisions
        else []
    )

    context = {
        'post': post,
        'post_cover_absolute_url': (
            request.build_absolute_uri(post.cover_access_url)
            if post.cover
            else ''
        ),
        'comments_enabled': comments_enabled,
        'comments': comments,
        'comment_count': comment_count,
        'hidden_comment_count': hidden_comment_count,
        'can_moderate_comments': can_moderate_comments,
        'comment_form': comment_form,
        'display_tags': get_display_tags(post),
        'related_posts': get_related_posts(post, request.user),
        'series_posts': get_series_posts(post, request.user),
        'post_revisions': post_revisions,
        'is_favorited': (
            request.user.is_authenticated
            and PostFavorite.objects.filter(user=request.user, post=post).exists()
        ),
    }
    context.update(get_post_interaction_context(post, request.user))
    context.update(get_category_context(post))
    return render(request, 'post_detail.html', context)

@login_required
@require_POST
def add_comment(request, post_id):
    retry_after = consume_rate_limit(
        request,
        'comment',
        limit=30,
        window_seconds=3600,
        block_seconds=900,
    )
    if retry_after:
        messages.error(
            request,
            f'评论发表过于频繁，请在 {retry_after} 秒后重试。',
        )
        return redirect('post_detail', post_id=post_id)

    post = get_object_or_404(
        Post.objects.filter(get_currently_published_query()),
        id=post_id,
        visibility='public',
    )
    if (
        post.author_id
        and request.user.id != post.author_id
        and users_are_blocked_between(request.user, post.author)
    ):
        messages.error(request, '你们之间已存在屏蔽关系，不能在这篇文章下评论。')
        return redirect('post_detail', post_id=post.id)

    comment_form = CommentForm(request.POST)
    parent_id = request.POST.get('parent_id')
    parent_comment = None

    if parent_id:
        parent_comment = get_object_or_404(
            Comment,
            id=parent_id,
            post=post,
            parent__isnull=True,
            is_hidden=False,
        )
        if users_are_blocked_between(request.user, parent_comment.author):
            messages.error(request, '你们之间已存在屏蔽关系，不能回复这条评论。')
            return redirect('post_detail', post_id=post.id)

    if comment_form.is_valid():
        comment = comment_form.save(commit=False)
        comment.post = post
        comment.author = request.user
        comment.parent = parent_comment
        comment.save()
        notification_target_url = reverse('post_detail', args=[post.id])
        if parent_comment:
            create_notification(
                recipient=parent_comment.author,
                actor=request.user,
                notification_type='reply_to_comment',
                message=f'{get_user_display_name(request.user)} 回复了你的评论。',
                target_url=notification_target_url,
                post=post,
                comment=comment,
            )
            if post.author_id != parent_comment.author_id:
                create_notification(
                    recipient=post.author,
                    actor=request.user,
                    notification_type='comment_on_post',
                    message=f'{get_user_display_name(request.user)} 回复了你文章下的评论。',
                    target_url=notification_target_url,
                    post=post,
                    comment=comment,
                )
            notify_mentioned_users(
                comment,
                post,
                request.user,
                excluded_user_ids={parent_comment.author_id, post.author_id},
            )
            messages.success(request, '回复发表成功。')
        else:
            create_notification(
                recipient=post.author,
                actor=request.user,
                notification_type='comment_on_post',
                message=f'{get_user_display_name(request.user)} 评论了你的文章《{post.title}》。',
                target_url=notification_target_url,
                post=post,
                comment=comment,
            )
            notify_mentioned_users(
                comment,
                post,
                request.user,
                excluded_user_ids={post.author_id},
            )
            messages.success(request, '评论发表成功。')
    else:
        messages.error(request, '评论发表失败，请检查评论内容。')

    return redirect('post_detail', post_id=post.id)


@login_required
@require_POST
def delete_comment(request, comment_id):
    comment = get_object_or_404(
        Comment.objects.select_related('post'),
        id=comment_id,
    )
    post_id = comment.post_id
    can_delete_comment = (
        comment.author_id == request.user.id
        or comment.post.author_id == request.user.id
    )

    if not can_delete_comment:
        messages.error(request, '你没有权限删除这条评论。')
        return redirect('post_detail', post_id=post_id)

    comment.delete()
    messages.success(request, '评论已删除。')
    return redirect('post_detail', post_id=post_id)


@login_required
@require_POST
def moderate_comment(request, comment_id):
    comment = get_object_or_404(
        Comment.objects.select_related('post'),
        id=comment_id,
    )
    post_id = comment.post_id
    if not can_moderate_comment(request.user, comment):
        messages.error(request, '你没有权限审核这条评论。')
        return redirect('post_detail', post_id=post_id)

    moderation_action = request.POST.get('action')
    if moderation_action == 'hide':
        comment.is_hidden = True
        comment.moderated_by = request.user
        comment.moderated_at = timezone.now()
        comment.save(update_fields=['is_hidden', 'moderated_by', 'moderated_at'])
        messages.success(request, '评论已隐藏。')
    elif moderation_action == 'restore':
        comment.is_hidden = False
        comment.moderated_by = request.user
        comment.moderated_at = timezone.now()
        comment.save(update_fields=['is_hidden', 'moderated_by', 'moderated_at'])
        messages.success(request, '评论已恢复显示。')
    else:
        messages.error(request, '未知的评论审核操作。')

    return redirect('post_detail', post_id=post_id)


@login_required
@require_POST
def delete_post(request, post_id):
    post = get_object_or_404(Post, id=post_id, author=request.user)
    if post.status == 'published':
        post.delete()
    return redirect('index')


@require_GET
def rss_feed(request):
    owner, profile = get_site_owner_profile()
    if not owner:
        return HttpResponse('RSS 未配置', status=404, content_type='text/plain; charset=utf-8')

    posts = Post.objects.filter(
        get_currently_published_query(),
        author=owner,
        visibility='public',
    ).order_by('-created_at')[:20]
    site_url = request.build_absolute_uri('/')
    feed_url = request.build_absolute_uri(request.path)
    title = f"{profile.display_name} 的文章订阅"

    output = StringIO()
    xml = SimplerXMLGenerator(output, 'utf-8')
    xml.startDocument()
    xml.startElement('rss', {'version': '2.0'})
    xml.startElement('channel', {})
    xml.addQuickElement('title', title)
    xml.addQuickElement('link', site_url)
    xml.addQuickElement('description', profile.bio or '白车轴草博客文章订阅')
    xml.addQuickElement('language', 'zh-cn')
    xml.addQuickElement('atom:link', None, {
        'href': feed_url,
        'rel': 'self',
        'type': 'application/rss+xml',
        'xmlns:atom': 'http://www.w3.org/2005/Atom',
    })

    for post in posts:
        post_url = request.build_absolute_uri(post.get_absolute_url()) if hasattr(post, 'get_absolute_url') else request.build_absolute_uri(f'/post/{post.id}/')
        xml.startElement('item', {})
        xml.addQuickElement('title', post.title)
        xml.addQuickElement('link', post_url)
        xml.addQuickElement('guid', post_url)
        xml.addQuickElement('category', post.category_label)
        xml.addQuickElement('description', strip_tags(post.content)[:200])
        xml.addQuickElement('pubDate', post.created_at.strftime('%a, %d %b %Y %H:%M:%S +0000'))
        xml.endElement('item')

    xml.endElement('channel')
    xml.endElement('rss')
    xml.endDocument()
    return HttpResponse(output.getvalue(), content_type='application/rss+xml; charset=utf-8')
