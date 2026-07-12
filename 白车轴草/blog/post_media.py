import mimetypes
import os
import re
from urllib.parse import unquote

from django.conf import settings
from django.core.exceptions import SuspiciousFileOperation
from django.db.models import Q
from django.http import FileResponse, Http404
from django.utils import timezone
from django.utils.cache import patch_cache_control
from django.utils.http import content_disposition_header

from blog.models import PostImage


POST_IMAGE_URL_PATTERN = re.compile(r'/post-images/([0-9a-fA-F-]{36})/')
LEGACY_POST_IMAGE_URL_PATTERN = re.compile(r'/media/post_images/([^\s)]+)')


def sync_post_body_images(post):
    public_ids = set(POST_IMAGE_URL_PATTERN.findall(post.content or ''))
    post_images = PostImage.objects.filter(
        owner=post.author,
        public_id__in=public_ids,
    )
    post.body_images.set(post_images)


def find_protected_or_legacy_file(file_field):
    if not file_field or not file_field.name:
        raise Http404('文件不存在。')
    try:
        protected_file_path = file_field.storage.path(file_field.name)
    except (OSError, SuspiciousFileOperation, ValueError):
        raise Http404('文件不存在。')
    if os.path.isfile(protected_file_path):
        return protected_file_path
    legacy_media_root = os.path.realpath(settings.MEDIA_ROOT)
    legacy_file_path = os.path.realpath(
        os.path.join(legacy_media_root, file_field.name)
    )
    try:
        is_inside_legacy_root = (
            os.path.commonpath([legacy_media_root, legacy_file_path])
            == legacy_media_root
        )
    except ValueError:
        is_inside_legacy_root = False
    if not is_inside_legacy_root:
        raise Http404('文件不存在。')
    if os.path.isfile(legacy_file_path):
        return legacy_file_path
    raise Http404('文件不存在。')


def build_inline_file_response(file_path, *, public):
    content_type = mimetypes.guess_type(file_path)[0] or 'application/octet-stream'
    response = FileResponse(open(file_path, 'rb'), content_type=content_type)
    response['Content-Disposition'] = content_disposition_header(
        False,
        os.path.basename(file_path),
    )
    if public:
        patch_cache_control(response, public=True, max_age=3600)
    else:
        patch_cache_control(response, private=True, no_store=True, max_age=0)
    return response


def can_read_post_image(post_image, user):
    if user.is_authenticated and (user.is_superuser or post_image.owner_id == user.id):
        return True
    return post_image.posts.filter(
        status='published',
        visibility='public',
    ).filter(
        Q(scheduled_publish_at__isnull=True)
        | Q(scheduled_publish_at__lte=timezone.now())
    ).exists()


def decode_legacy_post_image_name(raw_file_name):
    return os.path.basename(unquote(raw_file_name))
