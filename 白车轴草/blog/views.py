from django.shortcuts import get_object_or_404, render, redirect
from django.core.files.base import ContentFile
from django.core import signing
from django.core.paginator import Paginator
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import F, Prefetch, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.core.management.base import CommandError
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.html import strip_tags
from django.utils.xmlutils import SimplerXMLGenerator
from blog.forms import (
    ChineseAuthenticationForm,
    ChineseUserCreationForm,
    CommentForm,
    PrivateMessageForm,
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
    Post,
    PrivateMessage,
    UserProfile,
)
from blog.site_owner import get_site_owner_profile
from collections import Counter
from io import StringIO
import base64
import os
import time
import uuid
from urllib.parse import urlparse


CUSTOM_CATEGORY_VALUE = '__custom__'
AI_GENERATION_COOLDOWN_SECONDS = 60
AI_COVER_TOKEN_SALT = 'blog.ai-cover'
AI_COVER_TOKEN_MAX_AGE_SECONDS = 7200


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


def resolve_category(request):
    category = (request.POST.get('category') or '').strip()
    if category == CUSTOM_CATEGORY_VALUE:
        return (request.POST.get('custom_category') or '').strip()[:50]
    return category


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
    if request_user.is_authenticated:
        return Post.objects.filter(
            Q(status='published', visibility='public')
            | Q(author=request_user, status='published')
        ).distinct().order_by('-created_at')

    return Post.objects.filter(
        status='published',
        visibility='public',
    ).order_by('-created_at')


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
    search_query = request.GET.get('q', '').strip()
    date_query = request.GET.get('date', '').strip()
    selected_date = parse_date(date_query) if date_query else None
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

    if selected_date:
        posts = posts.filter(created_at__date=selected_date)

    pagination_params = request.GET.copy()
    pagination_params.pop('page', None)
    pagination_query = pagination_params.urlencode()
    pagination_prefix = f'{pagination_query}&' if pagination_query else ''

    clear_category_params = request.GET.copy()
    clear_category_params.pop('category', None)
    clear_category_params.pop('page', None)
    clear_category_query = clear_category_params.urlencode()

    clear_search_params = request.GET.copy()
    clear_search_params.pop('q', None)
    clear_search_params.pop('page', None)
    clear_search_query = clear_search_params.urlencode()

    clear_date_params = request.GET.copy()
    clear_date_params.pop('date', None)
    clear_date_params.pop('page', None)
    clear_date_query = clear_date_params.urlencode()

    clear_author_params = request.GET.copy()
    clear_author_params.pop('author', None)
    clear_author_params.pop('page', None)
    clear_author_query = clear_author_params.urlencode()

    paginator = Paginator(posts, 6)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)
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
        'selected_date': date_query if selected_date else '',
        'pagination_prefix': pagination_prefix,
        'clear_category_query': clear_category_query,
        'clear_search_query': clear_search_query,
        'clear_date_query': clear_date_query,
        'clear_author_query': clear_author_query,
        'category_counts': category_counts,
        'top_categories': category_counts[:10],
        'profile': profile,
        'published_count': about_posts.count(),
        'total_views': about_posts.aggregate(total=Sum('views_count'))['total'] or 0,
        'recent_posts': author_posts[:5],
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
    tag_counts = build_tag_counts(posts)
    return render(request, 'tags.html', {
        'tag_counts': tag_counts,
    })


def register(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        form = ChineseUserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            login(request, user)
            messages.success(request, '注册成功，欢迎来到白车轴草。')
            return redirect('index')
    else:
        form = ChineseUserCreationForm()

    return render(request, 'auth_form.html', {
        'form': form,
        'page_title': '注册账号',
        'submit_text': '注册',
        'switch_text': '已经有账号？',
        'switch_url_name': 'login',
        'switch_link_text': '去登录',
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
                format, imgstr = cropped_avatar_data.split(';base64,')
                ext = format.split('/')[-1]
                file_name = f"avatar_{uuid.uuid4().hex[:8]}.{ext}"
                profile.avatar = ContentFile(base64.b64decode(imgstr), name=file_name)
            elif request.POST.get('clear_avatar') == 'true':
                profile.avatar = None
            request.user.email = form.cleaned_data.get('email', '')
            request.user.save(update_fields=['email'])
            profile.save()
            messages.success(request, '用户资料已保存。')
            return redirect('user_center')
    else:
        form = UserCenterForm(instance=profile, user=request.user)

    stats = {
        'published_count': Post.objects.filter(author=request.user, status='published').count(),
        'draft_count': Post.objects.filter(author=request.user, status='draft').count(),
        'total_count': Post.objects.filter(author=request.user).count(),
    }
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
def create_post(request):
    if request.method == 'POST':
        title = request.POST.get('title')
        category = resolve_category(request)
        tags = (request.POST.get('tags') or '').strip()[:200]
        content = request.POST.get('content')
        cover = request.FILES.get('cover')
        cropped_cover_data = request.POST.get('cropped_cover')
        ai_cover_token = request.POST.get('ai_cover_token', '')
        action = request.POST.get('action') # 'draft' or 'publish'

        status = 'published' if action == 'publish' else 'draft'
        visibility = request.POST.get('visibility', 'private')

        if cropped_cover_data:
            image_format, image_data = cropped_cover_data.split(';base64,')
            extension = image_format.split('/')[-1]
            file_name = f"cover_{uuid.uuid4().hex[:8]}.{extension}"
            cover = ContentFile(base64.b64decode(image_data), name=file_name)

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
            image_format, image_data = cropped_cover_data.split(';base64,')
            extension = image_format.split('/')[-1]
            file_name = f"cover_{uuid.uuid4().hex[:8]}.{extension}"
            post.cover = ContentFile(base64.b64decode(image_data), name=file_name)
        elif cover:
            post.cover = cover
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
        if parent_comment:
            messages.success(request, '回复发表成功。')
        else:
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
