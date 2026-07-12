import base64
import colorsys
import io
import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageOps

from blog.atomic_files import atomic_write_json
from blog.external_io import read_limited_error_response, read_limited_text_response
from blog.management.commands.create_startup_post import (
    DEFAULT_DEEPSEEK_MODEL,
    Command as StartupPostCommand,
)
from blog.homepage_media import (
    HOMEPAGE_IMAGE_COPY_FILE_NAME,
    get_homepage_ai_copy_by_file_name,
    get_homepage_image_copy_file_path,
    get_homepage_image_file_names,
    get_homepage_image_file_path,
    normalize_homepage_slide_copy,
)


DEFAULT_HOMEPAGE_VISION_MODEL = 'gpt-5.5'
OPENAI_RESPONSES_URL = 'https://api.openai.com/v1/responses'
HOMEPAGE_VISION_IMAGE_MAX_SIZE = (1280, 1280)
HOMEPAGE_VISION_IMAGE_QUALITY = 82
HOMEPAGE_VISION_FIELDS = (
    'subjects',
    'scene',
    'style',
    'character_candidates',
    'concise_description',
)


class Command(BaseCommand):
    help = 'Generate cached DeepSeek homepage copy for images in media/index_img.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--model',
            default=(
                os.getenv('DEEPSEEK_HOMEPAGE_COPY_MODEL')
                or os.getenv('DEEPSEEK_MODEL', DEFAULT_DEEPSEEK_MODEL)
            ),
            help='DeepSeek model used to generate homepage image copy.',
        )
        parser.add_argument(
            '--force',
            action='store_true',
            help='Regenerate copy even when a cached entry already exists.',
        )
        parser.add_argument(
            '--batch-size',
            type=int,
            default=12,
            help='Number of image descriptions sent to DeepSeek in one request.',
        )
        parser.add_argument(
            '--vision-model',
            default=os.getenv('HOMEPAGE_VISION_MODEL', DEFAULT_HOMEPAGE_VISION_MODEL),
            help='Vision model used to analyze homepage images before DeepSeek copy generation.',
        )
        parser.add_argument(
            '--skip-vision',
            action='store_true',
            help='Skip vision analysis even when HOMEPAGE_VISION_API_KEY is configured.',
        )

    def handle(self, *args, **options):
        api_key = os.getenv('DEEPSEEK_API_KEY')
        if not api_key:
            raise CommandError('DEEPSEEK_API_KEY is not configured.')

        image_file_names = get_homepage_image_file_names()
        if not image_file_names:
            self.stdout.write(self.style.WARNING('No homepage images were found.'))
            return

        model = options['model']
        vision_model = options['vision_model']
        should_force = options['force']
        should_skip_vision = options['skip_vision']
        batch_size = max(1, options['batch_size'])
        vision_api_key = os.getenv('HOMEPAGE_VISION_API_KEY')
        should_use_vision = bool(vision_api_key) and not should_skip_vision
        cached_copy_by_file_name = get_homepage_ai_copy_by_file_name()
        generated_copy_by_file_name = dict(cached_copy_by_file_name)
        deepseek_client = StartupPostCommand()
        generated_count = 0
        skipped_count = 0
        pending_image_descriptions = []

        for image_file_name in image_file_names:
            if not should_force and image_file_name in generated_copy_by_file_name:
                skipped_count += 1
                continue

            vision_analysis = {}
            if should_use_vision:
                try:
                    vision_analysis = self.describe_homepage_image_with_vision(
                        image_file_name,
                        vision_api_key,
                        vision_model,
                    )
                except CommandError as error:
                    self.stderr.write(
                        self.style.WARNING(
                            f'Vision analysis skipped for {image_file_name}: {error}'
                        )
                    )

            pending_image_descriptions.append({
                'file_name': image_file_name,
                'metadata_description': self.describe_homepage_image(image_file_name),
                'vision_analysis': vision_analysis,
            })

        for batch_start in range(0, len(pending_image_descriptions), batch_size):
            image_description_batch = pending_image_descriptions[batch_start:batch_start + batch_size]
            slide_copy_batch = self.generate_slide_copy_batch(
                deepseek_client,
                api_key,
                model,
                image_description_batch,
            )
            for image_description in image_description_batch:
                image_file_name = image_description['file_name']
                generated_copy_by_file_name[image_file_name] = slide_copy_batch[image_file_name]
                self.stdout.write(f'Generated homepage copy for {image_file_name}')
            generated_count += 1

        self.write_copy_file(generated_copy_by_file_name)
        self.stdout.write(
            self.style.SUCCESS(
                f'Wrote {HOMEPAGE_IMAGE_COPY_FILE_NAME}: '
                f'{len(pending_image_descriptions)} generated, {skipped_count} skipped, '
                f'{generated_count} DeepSeek request(s).'
            )
        )

    def describe_homepage_image_with_vision(self, image_file_name, api_key, model):
        image_data_url = self.build_homepage_image_data_url(image_file_name)
        request_body = {
            'model': model,
            'input': [
                {
                    'role': 'user',
                    'content': [
                        {
                            'type': 'input_text',
                            'text': (
                                '请识别这张个人博客首页背景图的真实画面内容。'
                                '如果是动漫、游戏或影视图片，请尽量识别作品、角色或场景；'
                                '只有比较确定时才写具体角色名，不确定时用“疑似”或留空。'
                                '输出必须是 JSON 对象，不要输出 Markdown。'
                                '字段必须是 subjects、scene、style、character_candidates、concise_description。'
                                'subjects 是画面主体短词数组。'
                                'character_candidates 是可能的角色名数组，可以为空。'
                                'concise_description 用一句中文概括画面，不超过 60 个中文字符。'
                            ),
                        },
                        {
                            'type': 'input_image',
                            'image_url': image_data_url,
                            'detail': 'high',
                        },
                    ],
                },
            ],
            'max_output_tokens': 700,
        }
        response_body = self.send_openai_response_request(api_key, request_body)
        output_text = self.extract_openai_response_text(response_body)
        return self.normalize_visual_analysis(output_text)

    def build_homepage_image_data_url(self, image_file_name):
        image_file_path = get_homepage_image_file_path(image_file_name)
        try:
            with Image.open(image_file_path) as source_image:
                image = ImageOps.exif_transpose(source_image).convert('RGB')
                image.thumbnail(HOMEPAGE_VISION_IMAGE_MAX_SIZE)
                image_bytes = io.BytesIO()
                image.save(
                    image_bytes,
                    format='JPEG',
                    quality=HOMEPAGE_VISION_IMAGE_QUALITY,
                    optimize=True,
                )
        except OSError as error:
            raise CommandError(f'Homepage image could not be opened: {image_file_name}') from error

        encoded_image = base64.b64encode(image_bytes.getvalue()).decode('ascii')
        return f'data:image/jpeg;base64,{encoded_image}'

    def send_openai_response_request(self, api_key, request_body):
        request_data = json.dumps(request_body).encode('utf-8')
        request = Request(
            OPENAI_RESPONSES_URL,
            data=request_data,
            headers={
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json',
            },
            method='POST',
        )

        try:
            with urlopen(request, timeout=90) as response:
                response_text = read_limited_text_response(response)
        except HTTPError as error:
            error_text = read_limited_error_response(error)
            raise CommandError(f'OpenAI vision API HTTP error {error.code}: {error_text}') from error
        except URLError as error:
            raise CommandError(f'OpenAI vision API network error: {error.reason}') from error
        except ValueError as error:
            raise CommandError(f'OpenAI vision API response was rejected: {error}') from error

        try:
            return json.loads(response_text)
        except json.JSONDecodeError as error:
            raise CommandError(f'OpenAI vision API returned invalid JSON: {error}') from error

    def extract_openai_response_text(self, response_body):
        output_text = response_body.get('output_text')
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        for output_item in response_body.get('output', []):
            if not isinstance(output_item, dict):
                continue
            for content_item in output_item.get('content', []):
                if not isinstance(content_item, dict):
                    continue
                content_text = content_item.get('text')
                if isinstance(content_text, str) and content_text.strip():
                    return content_text

        raise CommandError('OpenAI vision API response did not include output text.')

    def normalize_visual_analysis(self, output_text):
        try:
            raw_analysis = json.loads(output_text)
        except json.JSONDecodeError:
            cleaned_text = output_text.strip()
            if not cleaned_text:
                return {}
            return {
                'subjects': [],
                'scene': '',
                'style': '',
                'character_candidates': [],
                'concise_description': cleaned_text[:120],
            }

        if not isinstance(raw_analysis, dict):
            return {}

        normalized_analysis = {}
        for field_name in HOMEPAGE_VISION_FIELDS:
            field_value = raw_analysis.get(field_name)
            if field_name in {'subjects', 'character_candidates'}:
                normalized_analysis[field_name] = self.normalize_visual_list(field_value)
            elif isinstance(field_value, str):
                normalized_analysis[field_name] = field_value.strip()[:160]
            else:
                normalized_analysis[field_name] = ''
        return normalized_analysis

    def normalize_visual_list(self, raw_items):
        if not isinstance(raw_items, list):
            return []

        normalized_items = []
        for raw_item in raw_items:
            if not isinstance(raw_item, str):
                continue
            normalized_item = raw_item.strip()
            if normalized_item and normalized_item not in normalized_items:
                normalized_items.append(normalized_item[:40])
            if len(normalized_items) == 6:
                break
        return normalized_items

    def describe_homepage_image(self, image_file_name):
        image_file_path = get_homepage_image_file_path(image_file_name)
        try:
            with Image.open(image_file_path) as source_image:
                image = ImageOps.exif_transpose(source_image).convert('RGB')
                width, height = image.size
                thumbnail = image.resize((1, 1))
                red, green, blue = thumbnail.getpixel((0, 0))
        except OSError as error:
            raise CommandError(f'Homepage image could not be opened: {image_file_name}') from error

        orientation = '横向图片' if width >= height else '竖向图片'
        brightness = self.describe_brightness(red, green, blue)
        color_tone = self.describe_color_tone(red, green, blue)
        file_stem = os.path.splitext(image_file_name)[0].replace('-', ' ').replace('_', ' ')
        return (
            f'文件名：{file_stem}。'
            f'尺寸：{width}x{height}，{orientation}。'
            f'整体亮度：{brightness}。'
            f'平均主色：{color_tone}。'
            '用途：个人博客首页首屏轮播背景。'
        )

    def describe_brightness(self, red, green, blue):
        brightness = (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255
        if brightness >= 0.72:
            return '明亮'
        if brightness >= 0.42:
            return '柔和'
        return '偏暗'

    def describe_color_tone(self, red, green, blue):
        hue, saturation, _ = colorsys.rgb_to_hsv(red / 255, green / 255, blue / 255)
        if saturation < 0.16:
            return '低饱和的灰白色调'

        hue_degrees = hue * 360
        if hue_degrees < 35 or hue_degrees >= 335:
            return '暖红色调'
        if hue_degrees < 70:
            return '金黄暖色调'
        if hue_degrees < 165:
            return '绿色自然色调'
        if hue_degrees < 250:
            return '蓝绿色清透色调'
        if hue_degrees < 300:
            return '蓝紫冷色调'
        return '粉紫柔和色调'

    def generate_slide_copy_batch(self, deepseek_client, api_key, model, image_description_batch):
        image_descriptions_json = json.dumps(image_description_batch, ensure_ascii=False)
        request_body = {
            'model': model,
            'thinking': {
                'type': 'disabled',
            },
            'messages': [
                {
                    'role': 'system',
                    'content': (
                        '你是一个中文个人博客首页文案助手。'
                        '根据图片元数据和视觉识别结果生成短小、有画面感、适合首页轮播的中文文案。'
                        '如果 vision_analysis 不为空，优先使用其中的画面主体、场景、风格和角色候选。'
                        '角色名只有在视觉识别结果明确给出时才可以使用，不能凭空编造。'
                        '不要输出导航说明，不要提“文章/归档/标签”，不要编造实时事实。'
                        '只输出 JSON 对象，不要输出 Markdown。'
                    ),
                },
                {
                    'role': 'user',
                    'content': (
                        f'图片描述数组：{image_descriptions_json}\n'
                        '请为数组里的每个 file_name 生成一组首页轮播文案。'
                        'metadata_description 是基础图片信息。'
                        'vision_analysis 是视觉模型看到的画面内容；如果包含 character_candidates，可以简洁使用较确定的角色名。'
                        '输出必须是 JSON 对象，顶层 key 必须使用原始 file_name。'
                        '每个 value 的字段必须是 kicker、headline、lead、card_title、card_text、moods。'
                        'kicker 不超过 10 个中文字符，可以包含一个居中点。'
                        'headline 不超过 26 个中文字符。'
                        'lead 不超过 42 个中文字符。'
                        'card_title 不超过 16 个中文字符。'
                        'card_text 不超过 42 个中文字符。'
                        'moods 是 3 个中文短词数组，每个不超过 6 个中文字符。'
                    ),
                },
            ],
            'response_format': {
                'type': 'json_object',
            },
            'max_tokens': max(1200, len(image_description_batch) * 420),
        }
        response_body = deepseek_client.send_deepseek_request(api_key, request_body)
        output_text = deepseek_client.extract_message_content(response_body)
        try:
            raw_copy_batch = json.loads(output_text)
        except json.JSONDecodeError as error:
            raise CommandError(f'DeepSeek returned invalid homepage copy JSON: {error}') from error

        if len(image_description_batch) == 1 and normalize_homepage_slide_copy(raw_copy_batch):
            raw_copy_batch = {
                image_description_batch[0]['file_name']: raw_copy_batch,
            }
        if not isinstance(raw_copy_batch, dict):
            raise CommandError('DeepSeek returned homepage copy in an invalid shape.')

        normalized_copy_batch = {}
        for image_description in image_description_batch:
            image_file_name = image_description['file_name']
            normalized_copy = normalize_homepage_slide_copy(raw_copy_batch.get(image_file_name))
            if not normalized_copy:
                raise CommandError(f'DeepSeek returned incomplete homepage copy for {image_file_name}.')
            normalized_copy_batch[image_file_name] = normalized_copy
        return normalized_copy_batch

    def write_copy_file(self, copy_by_file_name):
        copy_file_path = get_homepage_image_copy_file_path()
        atomic_write_json(copy_file_path, copy_by_file_name)
