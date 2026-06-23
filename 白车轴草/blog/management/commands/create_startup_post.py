import os
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from blog.models import Post


CATEGORY_VALUES = [category_value for category_value, _ in Post.CATEGORY_CHOICES]
DEFAULT_DEEPSEEK_MODEL = 'deepseek-v4-flash'
DEEPSEEK_CHAT_COMPLETIONS_URL = 'https://api.deepseek.com/chat/completions'


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

    def handle(self, *args, **options):
        username = options['username']
        should_create_draft = options['draft']
        model = options['model']
        current_time = timezone.localtime()
        current_date = current_time.date()
        daily_tag = f'daily:{current_date.isoformat()}'

        author = User.objects.filter(username=username).first()
        if author is None:
            raise CommandError(f'User "{username}" does not exist.')

        existing_post = Post.objects.filter(author=author, tags__icontains=daily_tag).first()
        if existing_post is not None:
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
                        '请从做菜、生活技巧、学习笔记、技术小记、读书、骑行、摄影、项目记录中选择一个角度。\n'
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
