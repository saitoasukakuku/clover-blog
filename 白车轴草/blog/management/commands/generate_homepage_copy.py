import colorsys
import json
import os

from django.core.management.base import BaseCommand, CommandError
from PIL import Image, ImageOps

from blog.management.commands.create_startup_post import (
    DEFAULT_DEEPSEEK_MODEL,
    Command as StartupPostCommand,
)
from blog.views import (
    HOMEPAGE_IMAGE_COPY_FILE_NAME,
    get_homepage_ai_copy_by_file_name,
    get_homepage_image_copy_file_path,
    get_homepage_image_file_names,
    get_homepage_image_file_path,
    normalize_homepage_slide_copy,
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

    def handle(self, *args, **options):
        api_key = os.getenv('DEEPSEEK_API_KEY')
        if not api_key:
            raise CommandError('DEEPSEEK_API_KEY is not configured.')

        image_file_names = get_homepage_image_file_names()
        if not image_file_names:
            self.stdout.write(self.style.WARNING('No homepage images were found.'))
            return

        model = options['model']
        should_force = options['force']
        batch_size = max(1, options['batch_size'])
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

            pending_image_descriptions.append({
                'file_name': image_file_name,
                'description': self.describe_homepage_image(image_file_name),
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
            'messages': [
                {
                    'role': 'system',
                    'content': (
                        '你是一个中文个人博客首页文案助手。'
                        '根据图片描述生成短小、有画面感、适合首页轮播的中文文案。'
                        '不要输出导航说明，不要提“文章/归档/标签”，不要编造实时事实。'
                        '只输出 JSON 对象，不要输出 Markdown。'
                    ),
                },
                {
                    'role': 'user',
                    'content': (
                        f'图片描述数组：{image_descriptions_json}\n'
                        '请为数组里的每个 file_name 生成一组首页轮播文案。'
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
            'max_tokens': 700,
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
        copy_directory = os.path.dirname(copy_file_path)
        if copy_directory:
            os.makedirs(copy_directory, exist_ok=True)
        with open(copy_file_path, 'w', encoding='utf-8') as copy_file:
            json.dump(copy_by_file_name, copy_file, ensure_ascii=False, indent=2)
            copy_file.write('\n')
