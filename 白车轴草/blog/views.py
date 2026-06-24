from django.shortcuts import get_object_or_404, render, redirect
from django.core.files.base import ContentFile
from django.core import signing
from django.core.paginator import Paginator
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.db.models import F, Q, Sum
from django.http import HttpResponse, JsonResponse
from django.views.decorators.http import require_POST
from django.core.management.base import CommandError
from django.utils.dateparse import parse_date
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.html import strip_tags
from django.utils.xmlutils import SimplerXMLGenerator
from blog.forms import ChineseAuthenticationForm, ChineseUserCreationForm, UserCenterForm
from blog.management.commands.create_startup_post import (
    DEFAULT_DEEPSEEK_MODEL,
    Command as StartupPostCommand,
)
from blog.models import Post, UserProfile
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

def index(request):
    owner, owner_profile = get_site_owner_profile()
    if request.user.is_authenticated:
        all_posts = Post.objects.filter(
            Q(status='published', visibility='public') |
            Q(author=request.user, status='published')
        ).distinct().order_by('-created_at')
        profile, _ = UserProfile.objects.get_or_create(user=request.user)
        about_posts = Post.objects.filter(
            author=request.user,
            status='published',
        )
    else:
        all_posts = Post.objects.filter(
            status='published',
            visibility='public'
        ).order_by('-created_at')
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

    context = {'post': post}
    context.update(get_category_context(post))
    return render(request, 'post_detail.html', context)

@login_required
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
