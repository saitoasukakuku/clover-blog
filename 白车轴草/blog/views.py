from django.shortcuts import get_object_or_404, render, redirect
from django.core.files.base import ContentFile
from django.core import signing
from django.core.paginator import Paginator
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import F, Prefetch, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.core.management.base import CommandError
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.html import strip_tags
from django.utils.xmlutils import SimplerXMLGenerator
from PIL import Image, UnidentifiedImageError
from blog.forms import (
    ChineseAuthenticationForm,
    ChineseUserCreationForm,
    CommentForm,
    PrivateMessageForm,
    RegistrationRequestForm,
    UserCenterForm,
)
from blog.management.commands.create_startup_post import (
    DEFAULT_DEEPSEEK_MODEL,
    Command as StartupPostCommand,
)
from blog.models import (
    Comment,
    FriendRequest,
    Friendship,
    Notification,
    Post,
    PostFavorite,
    PrivateMessage,
    RegistrationRequest,
    UserProfile,
)
from blog.site_owner import get_site_owner_profile
from collections import Counter
from io import BytesIO, StringIO
import base64
import binascii
import os
import time
import uuid
from urllib.parse import quote, urlparse


CUSTOM_CATEGORY_VALUE = '__custom__'
AI_GENERATION_COOLDOWN_SECONDS = 60
AI_COVER_TOKEN_SALT = 'blog.ai-cover'
AI_COVER_TOKEN_MAX_AGE_SECONDS = 7200
MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_EXTENSIONS = {
    'jpeg': 'jpg',
    'jpg': 'jpg',
    'png': 'png',
    'webp': 'webp',
}
HOMEPAGE_IMAGE_DIR_NAME = 'index_img'
HOMEPAGE_ALLOWED_IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.webp'}
HOMEPAGE_MAX_CAROUSEL_SLIDES = 12
HOMEPAGE_THEME_PRESETS = [
    {
        'accent': '#5f8fc8',
        'accent_strong': '#2c5f96',
        'accent_soft': 'rgba(95, 143, 200, .18)',
        'kicker': '晨湖 · 清透',
        'headline': '让每次进入网站，都像翻开一张新的明信片。',
        'lead': '首页背景从图片库轮播，文字、按钮和卡片颜色跟着图片气质变化。读者先感受到这一刻的氛围，再进入文章、归档或标签。',
        'card_title': '湖边醒来的阅读时间',
        'card_text': '蓝色和雪山适合清透的第一屏，按钮和标签会切换成偏冷的蓝色。',
        'moods': ['清晨', '远山', '慢生活'],
    },
    {
        'accent': '#c8893e',
        'accent_strong': '#8a5726',
        'accent_soft': 'rgba(200, 137, 62, .20)',
        'kicker': '秋屋 · 温暖',
        'headline': '先坐下来，再慢慢读几篇文章。',
        'lead': '当背景切到木屋和秋色，首页文案可以更像邀请。整体从清透变成温暖，适合生活记录和博主介绍。',
        'card_title': '木屋旁边的慢阅读',
        'card_text': '暖色背景适合把“认识博主”和“最近文章”做得更有亲近感。',
        'moods': ['秋天', '木屋', '随笔'],
    },
    {
        'accent': '#d06a54',
        'accent_strong': '#9a3e31',
        'accent_soft': 'rgba(208, 106, 84, .20)',
        'kicker': '夕照 · 强烈',
        'headline': '把一天的尾声，写进新的开场。',
        'lead': '夕阳和红叶更有戏剧感，适合让标题更短、更有画面。首页可以不是固定气质，而是随图库变化。',
        'card_title': '夕色里的首页入口',
        'card_text': '强色图片需要更厚的遮罩，文字保持清楚，按钮使用更深的主题色。',
        'moods': ['夕阳', '红叶', '故事'],
    },
    {
        'accent': '#8aa0b8',
        'accent_strong': '#4f657c',
        'accent_soft': 'rgba(138, 160, 184, .22)',
        'kicker': '雪林 · 安静',
        'headline': '安静一点，也能让网站更有记忆。',
        'lead': '雪景时减少装饰和饱和度，首页会变得更平静。适合展示归档、标签和长期阅读入口。',
        'card_title': '雪地里的安静入口',
        'card_text': '冷色低饱和背景适合突出文章和归档，不需要太多动效。',
        'moods': ['雪景', '安静', '归档'],
    },
]
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


def get_category_context(post=None):
    category = getattr(post, 'category', '') if post else ''
    is_custom_category = bool(category) and category not in Post.CATEGORY_LABELS
    return {
        'categories': Post.CATEGORY_CHOICES,
        'custom_category_value': CUSTOM_CATEGORY_VALUE,
        'is_custom_category': is_custom_category,
        'custom_category': category if is_custom_category else '',
    }


def get_clear_query(request, parameter_name):
    query_params = request.GET.copy()
    query_params.pop(parameter_name, None)
    query_params.pop('page', None)
    return query_params.urlencode()


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


def resolve_category(request):
    category = (request.POST.get('category') or '').strip()
    if category == CUSTOM_CATEGORY_VALUE:
        return (request.POST.get('custom_category') or '').strip()[:50]
    return category


def normalize_image_extension(raw_extension):
    extension = raw_extension.lower().strip()
    normalized_extension = ALLOWED_IMAGE_EXTENSIONS.get(extension)
    if normalized_extension is None:
        raise ValueError(INVALID_IMAGE_FILE_MESSAGE)
    return normalized_extension


def validate_image_bytes(image_bytes):
    if len(image_bytes) > MAX_IMAGE_UPLOAD_BYTES:
        raise ValueError(OVERSIZED_IMAGE_MESSAGE)
    try:
        image = Image.open(BytesIO(image_bytes))
        image.verify()
    except (UnidentifiedImageError, OSError, ValueError) as error:
        raise ValueError(INVALID_IMAGE_FILE_MESSAGE) from error


def build_image_file_from_data_url(data_url, file_prefix):
    try:
        image_format, image_data = data_url.split(';base64,', 1)
    except ValueError as error:
        raise ValueError(INVALID_IMAGE_DATA_MESSAGE) from error

    if not image_format.startswith('data:image/'):
        raise ValueError(INVALID_IMAGE_DATA_MESSAGE)

    raw_extension = image_format.rsplit('/', 1)[-1]
    extension = normalize_image_extension(raw_extension)
    try:
        image_bytes = base64.b64decode(image_data, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ValueError(INVALID_IMAGE_DATA_MESSAGE) from error

    validate_image_bytes(image_bytes)
    file_name = f'{file_prefix}_{uuid.uuid4().hex[:8]}.{extension}'
    return ContentFile(image_bytes, name=file_name)


def validate_uploaded_image_file(uploaded_file):
    if uploaded_file.size > MAX_IMAGE_UPLOAD_BYTES:
        raise ValueError(OVERSIZED_IMAGE_MESSAGE)
    try:
        uploaded_file.seek(0)
        image = Image.open(uploaded_file)
        image.verify()
        uploaded_file.seek(0)
    except (UnidentifiedImageError, OSError, ValueError) as error:
        try:
            uploaded_file.seek(0)
        except OSError:
            pass
        raise ValueError(INVALID_IMAGE_FILE_MESSAGE) from error
    return uploaded_file


def build_post_form_context(title, category, tags, content, visibility):
    post = Post(
        title=title or '',
        category=category or '',
        tags=tags or '',
        content=content or '',
        visibility=visibility or 'private',
    )
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
    if request_user.is_authenticated:
        return posts.filter(
            Q(status='published', visibility='public')
            | Q(author=request_user, status='published')
        ).distinct()

    return posts.filter(
        status='published',
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


def get_category_counts(posts):
    counter = Counter(post.category for post in posts if post.category)
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
    tag_counter = Counter()

    for post in posts:
        unique_tags = set(post.tag_list)
        for tag in unique_tags:
            if tag.startswith('daily:'):
                continue
            tag_counter[tag] += 1

    return [
        {'name': tag_name, 'count': tag_count}
        for tag_name, tag_count in sorted(
            tag_counter.items(),
            key=lambda tag_item: (-tag_item[1], tag_item[0].lower()),
        )
    ]


def get_display_tags(post):
    display_tags = []
    for tag in post.tag_list:
        if tag.startswith('daily:'):
            continue
        if tag not in display_tags:
            display_tags.append(tag)
    return display_tags


def filter_posts_by_tag(posts, selected_tag):
    if not selected_tag:
        return posts
    return [
        post
        for post in posts
        if selected_tag in get_display_tags(post)
    ]


def get_related_posts(post, request_user, limit=3):
    source_tags = set(get_display_tags(post))
    if not source_tags:
        return []

    tag_filter = Q()
    for source_tag in source_tags:
        tag_filter |= Q(tags__icontains=source_tag)

    candidate_posts = get_readable_published_posts(request_user).exclude(
        id=post.id,
    ).filter(
        tag_filter,
    ).select_related(
        'author',
        'author__profile',
    )
    scored_posts = []
    for candidate_post in candidate_posts:
        candidate_tags = set(get_display_tags(candidate_post))
        shared_tag_count = len(source_tags & candidate_tags)
        if shared_tag_count:
            scored_posts.append((shared_tag_count, candidate_post.created_at, candidate_post))

    scored_posts.sort(
        key=lambda scored_post: (scored_post[0], scored_post[1]),
        reverse=True,
    )
    return [
        candidate_post
        for _, __, candidate_post in scored_posts[:limit]
    ]


def get_homepage_image_file_names():
    image_directory = os.path.join(settings.MEDIA_ROOT, HOMEPAGE_IMAGE_DIR_NAME)
    try:
        image_file_names = sorted(os.listdir(image_directory), key=str.lower)
    except OSError:
        return []

    allowed_file_names = []
    for image_file_name in image_file_names:
        image_file_path = os.path.join(image_directory, image_file_name)
        _, image_extension = os.path.splitext(image_file_name)
        if image_extension.lower() not in HOMEPAGE_ALLOWED_IMAGE_EXTENSIONS:
            continue
        if not os.path.isfile(image_file_path):
            continue
        allowed_file_names.append(image_file_name)
        if len(allowed_file_names) >= HOMEPAGE_MAX_CAROUSEL_SLIDES:
            break
    return allowed_file_names


def build_homepage_carousel_slides():
    carousel_slides = []
    image_file_names = get_homepage_image_file_names()
    media_url_prefix = f"{settings.MEDIA_URL.rstrip('/')}/{HOMEPAGE_IMAGE_DIR_NAME}"

    for image_index, image_file_name in enumerate(image_file_names):
        theme_preset = HOMEPAGE_THEME_PRESETS[image_index % len(HOMEPAGE_THEME_PRESETS)]
        carousel_slides.append({
            'image_url': f"{media_url_prefix}/{quote(image_file_name)}",
            'file_name': image_file_name,
            **theme_preset,
        })
    return carousel_slides


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

    selected_category = request.GET.get('category', '').strip()
    selected_author = request.GET.get('author', '').strip()
    selected_tag = request.GET.get('tag', '').strip()
    search_query = request.GET.get('q', '').strip()
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
        post.card_display_tags = get_display_tags(post)[:3]
    page_obj.object_list = page_posts
    return render(request, 'index.html', {
        'posts': page_obj,
        'page_obj': page_obj,
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
    })


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
        'posts': page_obj,
        'page_obj': page_obj,
        'published_count': readable_posts.count(),
        'total_views': readable_posts.aggregate(total=Sum('views_count'))['total'] or 0,
    })


def archive_view(request):
    posts = get_readable_published_posts(request.user).select_related(
        'author',
        'author__profile',
    )
    archive_groups = build_archive_groups(posts)
    return render(request, 'archive.html', {
        'archive_groups': archive_groups,
    })


def tags_view(request):
    posts = get_readable_published_posts(request.user)
    tag_search_query = request.GET.get('q', '').strip()
    tag_sort = request.GET.get('sort', 'count').strip()
    selected_tag = request.GET.get('selected', '').strip()
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


def register(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        form = RegistrationRequestForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            registration_request = RegistrationRequest.objects.filter(email=email).first()
            if registration_request:
                if registration_request.status == RegistrationRequest.STATUS_PENDING:
                    messages.info(request, '这个邮箱的注册申请正在等待审核。')
                    return redirect('register')
                if (
                    registration_request.status == RegistrationRequest.STATUS_APPROVED
                    and not registration_request.is_code_expired
                ):
                    messages.info(request, '这个邮箱已经通过审核，请查看邮件里的注册码。')
                    return redirect('complete_registration')

                registration_request.reopen()
                registration_request.save()
                messages.success(request, '注册申请已重新提交，请等待审核。')
                return redirect('register')

            RegistrationRequest.objects.create(email=email)
            messages.success(request, '注册申请已提交，请等待审核。')
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
        'switch_link_text': '去完成注册',
    })

def login_view(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        form = ChineseAuthenticationForm(request, data=request.POST)
        if form.is_valid():
            login(request, form.get_user())
            messages.success(request, '登录成功，欢迎回来。')
            next_url = request.GET.get('next')
            if not url_has_allowed_host_and_scheme(next_url, allowed_hosts={request.get_host()}):
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
def logout_view(request):
    logout(request)
    messages.success(request, '已退出登录。')
    return redirect('index')

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
def friends_view(request):
    search_query = (request.GET.get('q') or '').strip()
    friends = get_friends_for_user(request.user)
    friend_ids = {friend.id for friend in friends}
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
        incoming_sender_ids = {
            friend_request.sender_id
            for friend_request in incoming_requests
        }
        outgoing_receiver_ids = {
            friend_request.receiver_id
            for friend_request in outgoing_requests
        }
        for matched_user in matched_users:
            if matched_user.id in friend_ids:
                matched_user.relationship_status = 'friend'
            elif matched_user.id in incoming_sender_ids:
                matched_user.relationship_status = 'incoming'
            elif matched_user.id in outgoing_receiver_ids:
                matched_user.relationship_status = 'outgoing'
            else:
                matched_user.relationship_status = 'none'
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
    target_user = get_object_or_404(User, id=user_id)
    if target_user == request.user:
        messages.error(request, '不能添加自己为好友。')
        return redirect('friends')
    if are_friends(request.user, target_user):
        messages.info(request, '你们已经是好友。')
        return redirect('friends')
    if FriendRequest.objects.filter(
        sender=target_user,
        receiver=request.user,
        status='pending',
    ).exists():
        messages.info(request, '对方已向你发送好友申请，请在待处理申请中操作。')
        return redirect('friends')

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
    return redirect('friends')


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
    user_low_id, user_high_id = sorted((request.user.id, target_user.id))
    deleted_count, _ = Friendship.objects.filter(
        user_low_id=user_low_id,
        user_high_id=user_high_id,
    ).delete()
    if deleted_count:
        messages.success(request, '好友已删除。')
    else:
        messages.error(request, '当前用户不是你的好友。')
    return redirect('friends')


@login_required
def conversations_view(request):
    conversation_items = []
    for friend in get_friends_for_user(request.user):
        conversation_messages = PrivateMessage.objects.filter(
            Q(sender=request.user, recipient=friend)
            | Q(sender=friend, recipient=request.user)
        )
        conversation_items.append({
            'friend': friend,
            'last_message': conversation_messages.order_by('-created_at').first(),
            'unread_count': conversation_messages.filter(
                sender=friend,
                recipient=request.user,
                is_read=False,
            ).count(),
        })

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
    if not are_friends(request.user, friend):
        messages.error(request, '只有好友之间可以发送私信。')
        return redirect('friends')

    if request.method == 'POST':
        message_form = PrivateMessageForm(request.POST)
        if message_form.is_valid():
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

    PrivateMessage.objects.filter(
        sender=friend,
        recipient=request.user,
        is_read=False,
    ).update(is_read=True)
    conversation_messages = PrivateMessage.objects.filter(
        Q(sender=request.user, recipient=friend)
        | Q(sender=friend, recipient=request.user)
    ).select_related('sender__profile')

    return render(request, 'conversation.html', {
        'friend': friend,
        'conversation_messages': conversation_messages,
        'message_form': message_form,
    })


@login_required
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
    })


@login_required
@require_POST
def toggle_favorite(request, post_id):
    readable_posts = filter_readable_posts(
        Post.objects.filter(id=post_id),
        request.user,
    )
    post = get_object_or_404(readable_posts)
    favorite = PostFavorite.objects.filter(
        user=request.user,
        post=post,
    ).first()

    if favorite:
        favorite.delete()
        messages.info(request, '已取消收藏。')
    else:
        PostFavorite.objects.create(user=request.user, post=post)
        messages.success(request, '文章已加入收藏。')

    fallback_url = reverse('post_detail', args=[post.id])
    next_url = request.POST.get('next') or fallback_url
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
    ):
        next_url = fallback_url
    return redirect(next_url)


@login_required
def notifications_view(request):
    notifications = Notification.objects.filter(
        recipient=request.user,
    ).select_related(
        'actor',
        'actor__profile',
    ).order_by('-created_at')
    paginator = Paginator(notifications, 12)
    page_obj = paginator.get_page(request.GET.get('page'))
    return render(request, 'notifications.html', {
        'notifications': page_obj,
        'page_obj': page_obj,
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
        title = request.POST.get('title')
        category = resolve_category(request)
        tags = (request.POST.get('tags') or '').strip()[:200]
        content = request.POST.get('content') or ''
        cover = request.FILES.get('cover')
        cropped_cover_data = request.POST.get('cropped_cover')
        ai_cover_token = request.POST.get('ai_cover_token', '')
        action = request.POST.get('action') # 'draft' or 'publish'

        status = 'published' if action == 'publish' else 'draft'
        visibility = request.POST.get('visibility', 'private')

        if cropped_cover_data:
            try:
                cover = build_image_file_from_data_url(cropped_cover_data, 'cover')
            except ValueError as error:
                messages.error(request, str(error))
                return render(
                    request,
                    'create_post.html',
                    build_post_form_context(title, category, tags, content, visibility),
                )
        elif cover:
            try:
                cover = validate_uploaded_image_file(cover)
            except ValueError as error:
                messages.error(request, str(error))
                return render(
                    request,
                    'create_post.html',
                    build_post_form_context(title, category, tags, content, visibility),
                )

        ai_cover_data = None
        if cover is None:
            ai_cover_data = get_ai_cover_data(ai_cover_token)
            if ai_cover_data:
                try:
                    image_bytes = StartupPostCommand().download_pexels_image(
                        ai_cover_data['image_url']
                    )
                    photo_id = ai_cover_data.get('photo_id', 'ai')
                    file_name = f"ai_{uuid.uuid4().hex[:8]}-{photo_id}.jpg"
                    cover = ContentFile(image_bytes, name=file_name)
                    content = append_ai_cover_attribution(content, ai_cover_data)
                except CommandError:
                    messages.warning(request, '文章已保存，但 AI 封面下载失败。')

        post = Post(
            author=request.user,
            title=title,
            category=category,
            tags=tags,
            content=content,
            cover=cover,
            status=status,
            visibility=visibility
        )
        post.save()
        
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
def drafts_list(request):
    posts = Post.objects.filter(author=request.user, status='draft').order_by('-updated_at')
    return render(request, 'drafts.html', {'posts': posts})

@login_required
def edit_post(request, post_id):
    post = get_object_or_404(Post, id=post_id, author=request.user)
    
    if request.method == 'POST':
        post.title = request.POST.get('title')
        post.category = resolve_category(request)
        post.tags = (request.POST.get('tags') or '').strip()[:200]
        post.content = request.POST.get('content')
        
        cover = request.FILES.get('cover')
        cropped_cover_data = request.POST.get('cropped_cover')
        
        if cropped_cover_data:
            try:
                post.cover = build_image_file_from_data_url(cropped_cover_data, 'cover')
            except ValueError as error:
                messages.error(request, str(error))
                context = {'post': post}
                context.update(get_category_context(post))
                return render(request, 'create_post.html', context)
        elif cover:
            try:
                post.cover = validate_uploaded_image_file(cover)
            except ValueError as error:
                messages.error(request, str(error))
                context = {'post': post}
                context.update(get_category_context(post))
                return render(request, 'create_post.html', context)
        elif request.POST.get('clear_cover') == 'true':
            post.cover = None

        action = request.POST.get('action')
        post.status = 'published' if action == 'publish' else 'draft'
        post.visibility = request.POST.get('visibility', 'private')
        post.save()
        
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

def post_detail(request, post_id):
    if request.user.is_authenticated:
        post = get_object_or_404(
            Post,
            Q(id=post_id),
            Q(status='published', visibility='public') | Q(author=request.user)
        )
    else:
        post = get_object_or_404(
            Post,
            id=post_id,
            status='published',
            visibility='public'
        )

    Post.objects.filter(id=post.id).update(views_count=F('views_count') + 1)
    post.refresh_from_db(fields=['views_count'])

    comments_enabled = (
        post.status == 'published'
        and post.visibility == 'public'
    )

    if comments_enabled:
        reply_queryset = Comment.objects.select_related(
            'author__profile'
        ).order_by('created_at')
        comments = post.comments.filter(
            parent__isnull=True
        ).select_related(
            'author__profile'
        ).prefetch_related(
            Prefetch('replies', queryset=reply_queryset)
        )
        comment_count = post.comments.count()
    else:
        comments = post.comments.none()
        comment_count = 0

    if comments_enabled and request.user.is_authenticated:
        comment_form = CommentForm()
    else:
        comment_form = None

    context = {
        'post': post,
        'comments_enabled': comments_enabled,
        'comments': comments,
        'comment_count': comment_count,
        'comment_form': comment_form,
        'display_tags': get_display_tags(post),
        'related_posts': get_related_posts(post, request.user),
        'is_favorited': (
            request.user.is_authenticated
            and PostFavorite.objects.filter(user=request.user, post=post).exists()
        ),
    }
    context.update(get_category_context(post))
    return render(request, 'post_detail.html', context)

@login_required
@require_POST
def add_comment(request, post_id):
    post = get_object_or_404(
        Post,
        id=post_id,
        status='published',
        visibility='public',
    )

    comment_form = CommentForm(request.POST)
    parent_id = request.POST.get('parent_id')
    parent_comment = None

    if parent_id:
        parent_comment = get_object_or_404(
            Comment,
            id=parent_id,
            post=post,
            parent__isnull=True,
        )

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
def delete_post(request, post_id):
    post = get_object_or_404(Post, id=post_id, author=request.user)
    if post.status == 'published':
        post.delete()
    return redirect('index')


def rss_feed(request):
    owner, profile = get_site_owner_profile()
    if not owner:
        return HttpResponse('RSS 未配置', status=404, content_type='text/plain; charset=utf-8')

    posts = Post.objects.filter(author=owner, status='published', visibility='public').order_by('-created_at')[:20]
    site_url = request.build_absolute_uri('/')
    feed_url = request.build_absolute_uri()
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
