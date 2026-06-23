import os
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.contrib.auth.models import User
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from blog.models import Post


CATEGORY_VALUES = [category_value for category_value, _ in Post.CATEGORY_CHOICES]
DEFAULT_DEEPSEEK_MODEL = 'deepseek-v4-flash'
DEEPSEEK_CHAT_COMPLETIONS_URL = 'https://api.deepseek.com/chat/completions'
PEXELS_SEARCH_URL = 'https://api.pexels.com/v1/search'
PEXELS_CATEGORY_QUERIES = {
    'tech': 'workspace laptop coding',
    'life': 'cozy morning home',
    'reading': 'books reading desk',
    'cycling': 'bicycle road',
    'photography': 'camera photography',
    'travel': 'travel landscape',
    'movie': 'cinema film',
    'music': 'music headphones',
    'food': 'home cooking food',
    'study': 'study notebook desk',
    'project': 'creative project workspace',
    'mood': 'quiet nature mood',
}


class Command(BaseCommand):
    help = 'Create one DeepSeek-generated published article for the current day.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            default=os.getenv('STARTUP_POST_USERNAME', '白车轴草'),
            help='Username that owns the generated article.',
        )
        parser.add_argument(
            '--draft',
            action='store_true',
            help='Create the generated article as a draft instead of publishing it.',
        )
        parser.add_argument(
            '--model',
            default=os.getenv('DEEPSEEK_MODEL', DEFAULT_DEEPSEEK_MODEL),
            help='DeepSeek model used to generate the article.',
        )
        parser.add_argument(
            '--skip-cover',
            action='store_true',
            help='Publish the article without searching Pexels for a cover image.',
        )
        parser.add_argument(
            '--cover-existing',
            action='store_true',
            help='Attach a Pexels cover to today existing daily article without creating a new article.',
        )

    def handle(self, *args, **options):
        username = options['username']
        should_create_draft = options['draft']
        should_skip_cover = options['skip_cover']
        should_cover_existing = options['cover_existing']
        model = options['model']
        current_time = timezone.localtime()
        current_date = current_time.date()
        daily_tag = f'daily:{current_date.isoformat()}'

        author = User.objects.filter(username=username).first()
        if author is None:
            raise CommandError(f'User "{username}" does not exist.')

        existing_post = Post.objects.filter(author=author, tags__icontains=daily_tag).first()
        if existing_post is not None:
            if should_cover_existing:
                self.attach_cover_to_existing_post(existing_post, current_date)
            self.stdout.write(self.style.WARNING(f'Daily article already exists: {existing_post.title}'))
            return

        formatted_date = current_date.strftime('%Y-%m-%d')
        recent_titles = list(
            Post.objects.filter(author=author)
            .order_by('-created_at')
            .values_list('title', flat=True)[:20]
        )
        generated_article = self.generate_article(model, formatted_date, recent_titles)
        title = f"{formatted_date}｜{generated_article['title']}"
        content = generated_article['content']
        status = 'draft' if should_create_draft else 'published'

        if Post.objects.filter(author=author, title=title).exists():
            raise CommandError(f'Generated duplicate title: {title}')
        if Post.objects.filter(author=author, content=content).exists():
            raise CommandError('Generated duplicate content.')

        post = Post.objects.create(
            author=author,
            title=title,
            category=generated_article['category'],
            tags=self.build_tags(generated_article['tags'], daily_tag),
            content=content,
            status=status,
        )

        if should_skip_cover:
            self.stdout.write(self.style.WARNING('Skipped Pexels cover image.'))
        else:
            try:
                self.attach_cover(post, generated_article, current_date)
            except CommandError as error:
                self.stderr.write(self.style.WARNING(f'Cover image was not attached: {error}'))

        self.stdout.write(self.style.SUCCESS(f'Created daily article: {post.title}'))

    def generate_article(self, model, formatted_date, recent_titles):
        api_key = os.getenv('DEEPSEEK_API_KEY')
        if not api_key:
            raise CommandError('DEEPSEEK_API_KEY is not configured.')

        request_body = {
            'model': model,
            'messages': [
                {
                    'role': 'system',
                    'content': (
                        '你是一个中文个人博客作者。请生成一篇原创文章。'
                        '文章要适合个人博客，不要声称自己亲历了不存在的事情。'
                        '不要写实时新闻、价格、医疗建议、法律建议或无法验证的事实。'
                        '文章应自然、有用、具体，避免空泛鸡汤。'
                        '只输出 JSON 对象，不要输出 Markdown。'
                    ),
                },
                {
                    'role': 'user',
                    'content': (
                        f'今天日期是 {formatted_date}。\n'
                        '请从做菜、生活技巧、学习笔记、技术小记、读书、骑行、摄影、项目记录等选择一个角度，你也可以自己随便想一个。\n'
                        '最近已经写过的标题如下，请避免重复主题和重复标题：\n'
                        f'{json.dumps(recent_titles, ensure_ascii=False)}\n'
                        'JSON 字段必须是 title、category、tags、content。'
                        f'category 必须从这些值中选择：{json.dumps(CATEGORY_VALUES, ensure_ascii=False)}。'
                        'tags 必须是 2 到 4 个中文短标签组成的数组。'
                        'content 写 500 到 1800 个中文字符。'
                    ),
                },
            ],
            'response_format': {
                'type': 'json_object',
            },
            'max_tokens': 1800,
        }
        response_body = self.send_deepseek_request(api_key, request_body)
        output_text = self.extract_message_content(response_body)

        try:
            generated_article = json.loads(output_text)
        except json.JSONDecodeError as error:
            raise CommandError(f'DeepSeek returned invalid JSON: {error}') from error

        self.validate_article(generated_article)
        return generated_article

    def send_deepseek_request(self, api_key, request_body):
        request_data = json.dumps(request_body).encode('utf-8')
        request = Request(
            DEEPSEEK_CHAT_COMPLETIONS_URL,
            data=request_data,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )

        try:
            with urlopen(request, timeout=90) as response:
                response_text = response.read().decode('utf-8')
        except HTTPError as error:
            error_text = error.read().decode('utf-8', errors='replace')
            raise CommandError(f'DeepSeek API HTTP error {error.code}: {error_text}') from error
        except URLError as error:
            raise CommandError(f'DeepSeek API network error: {error.reason}') from error

        try:
            return json.loads(response_text)
        except json.JSONDecodeError as error:
            raise CommandError(f'DeepSeek API returned invalid JSON: {error}') from error

    def extract_message_content(self, response_body):
        choices = response_body.get('choices', [])
        if not choices:
            raise CommandError('DeepSeek API response did not include choices.')

        message = choices[0].get('message', {})
        content = message.get('content', '')
        if content:
            return content

        raise CommandError('DeepSeek API response did not include message content.')

    def validate_article(self, generated_article):
        title = generated_article.get('title')
        category = generated_article.get('category')
        tags = generated_article.get('tags')
        content = generated_article.get('content')

        if not isinstance(title, str) or not title.strip():
            raise CommandError('Generated article title is empty.')
        if category not in CATEGORY_VALUES:
            raise CommandError(f'Generated article category is invalid: {category}')
        if not isinstance(tags, list) or not tags:
            raise CommandError('Generated article tags are invalid.')
        if not isinstance(content, str) or not content.strip():
            raise CommandError('Generated article content is empty.')

    def build_tags(self, generated_tags, daily_tag):
        cleaned_tags = []
        for raw_tag in generated_tags:
            if not isinstance(raw_tag, str):
                continue
            cleaned_tag = raw_tag.strip()
            if cleaned_tag and cleaned_tag not in cleaned_tags:
                cleaned_tags.append(cleaned_tag)

        final_tags = ['自动发布', *cleaned_tags, daily_tag]
        return ','.join(final_tags)[:200]

    def attach_cover(self, post, generated_article, current_date):
        api_key = os.getenv('PEXELS_API_KEY')
        if not api_key:
            self.stderr.write(self.style.WARNING('PEXELS_API_KEY is not configured; article published without cover.'))
            return

        pexels_photo = self.search_pexels_photo(api_key, generated_article, current_date)
        image_url = pexels_photo.get('src', {}).get('landscape') or pexels_photo.get('src', {}).get('large')
        if not image_url:
            self.stderr.write(self.style.WARNING('Pexels photo did not include a usable image URL.'))
            return

        image_bytes = self.download_pexels_image(image_url)
        photo_id = pexels_photo.get('id', 'unknown')
        cover_filename = f'auto_covers/{current_date.isoformat()}-{photo_id}.jpg'
        post.cover.save(cover_filename, ContentFile(image_bytes), save=True)
        self.append_pexels_attribution(post, pexels_photo)
        self.stdout.write(self.style.SUCCESS(f'Attached Pexels cover: {cover_filename}'))

    def search_pexels_photo(self, api_key, generated_article, current_date):
        search_query = self.build_pexels_query(generated_article)
        query_parameters = urlencode({
            'query': search_query,
            'orientation': 'landscape',
            'per_page': 10,
            'locale': 'zh-CN',
        })
        request = Request(
            f'{PEXELS_SEARCH_URL}?{query_parameters}',
            headers={
                'Authorization': api_key,
                'User-Agent': 'clover-blog/1.0',
            },
            method='GET',
        )

        try:
            with urlopen(request, timeout=30) as response:
                response_text = response.read().decode('utf-8')
        except HTTPError as error:
            error_text = error.read().decode('utf-8', errors='replace')
            raise CommandError(f'Pexels API HTTP error {error.code}: {error_text}') from error
        except URLError as error:
            raise CommandError(f'Pexels API network error: {error.reason}') from error

        try:
            response_body = json.loads(response_text)
        except json.JSONDecodeError as error:
            raise CommandError(f'Pexels API returned invalid JSON: {error}') from error

        photos = response_body.get('photos', [])
        if not photos:
            raise CommandError(f'Pexels did not find a photo for query: {search_query}')

        photo_index = current_date.toordinal() % len(photos)
        return photos[photo_index]

    def build_pexels_query(self, generated_article):
        category = generated_article['category']
        title = generated_article['title']
        tags = generated_article['tags']
        category_query = PEXELS_CATEGORY_QUERIES.get(category, 'personal blog lifestyle')
        first_tag = tags[0] if tags else ''
        return f'{category_query} {first_tag} {title}'[:120]

    def download_pexels_image(self, image_url):
        request = Request(image_url, headers={'User-Agent': 'clover-blog/1.0'}, method='GET')
        try:
            with urlopen(request, timeout=60) as response:
                return response.read()
        except HTTPError as error:
            error_text = error.read().decode('utf-8', errors='replace')
            raise CommandError(f'Pexels image HTTP error {error.code}: {error_text}') from error
        except URLError as error:
            raise CommandError(f'Pexels image network error: {error.reason}') from error

    def append_pexels_attribution(self, post, pexels_photo):
        photographer = pexels_photo.get('photographer')
        photo_url = pexels_photo.get('url')
        photographer_url = pexels_photo.get('photographer_url')
        if not photographer or not photo_url:
            return

        attribution = f'封面图：Photo by {photographer} on Pexels。'
        if photographer_url:
            attribution += f'\n摄影师主页：{photographer_url}'
        attribution += f'\n图片来源：{photo_url}'

        post.content = f'{post.content}\n\n{attribution}'
        post.save(update_fields=['content', 'updated_at'])

    def attach_cover_to_existing_post(self, post, current_date):
        if post.cover:
            self.stdout.write(self.style.WARNING(f'Existing post already has cover: {post.cover.name}'))
            return

        generated_article = {
            'title': self.remove_date_prefix(post.title),
            'category': post.category,
            'tags': post.tag_list,
            'content': post.content,
        }
        self.attach_cover(post, generated_article, current_date)

    def remove_date_prefix(self, title):
        if '｜' in title:
            return title.split('｜', 1)[1]
        return title
