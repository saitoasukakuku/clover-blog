import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from blog.models import Post


ARTICLE_TEMPLATES = [
    {
        'title': '厨房记录：一份简单的番茄鸡蛋面',
        'category': 'food',
        'tags': '自动草稿,做菜',
        'content': (
            '今天可以记录一道很适合忙碌日子的家常面。\n\n'
            '先把番茄切成小块，鸡蛋打散。锅里放少量油，先炒鸡蛋，成型后盛出。'
            '再把番茄炒出汁，加一点盐和少量清水，放入面条煮熟，最后把鸡蛋倒回锅里。\n\n'
            '这道菜的关键不是复杂调料，而是把番茄炒软，让汤底有自然的酸甜味。'
        ),
    },
    {
        'title': '生活技巧：给明天留一个更轻松的开始',
        'category': 'life',
        'tags': '自动草稿,生活技巧',
        'content': (
            '一个很实用的小技巧：睡前只整理三样东西。\n\n'
            '第一，桌面只留下明天一定会用的物品。第二，把第二天要穿的衣服提前放好。'
            '第三，把最重要的一件事写在纸上，放在醒来第一眼能看到的位置。\n\n'
            '这样做不会让生活立刻变完美，但可以减少早晨的混乱感。'
        ),
    },
    {
        'title': '学习笔记：为什么要给代码写清楚的名字',
        'category': 'study',
        'tags': '自动草稿,学习笔记',
        'content': (
            '写代码时，变量名其实是在给未来的自己留说明书。\n\n'
            '比如 raw_text 表示原始文本，cleaned_text 表示清理后的文本，final_text 表示最终要使用的文本。'
            '这三个名字比 text1、text2、text3 更容易理解，因为它们说明了数据当前处在什么阶段。\n\n'
            '清楚的命名会让排错更快，也会让代码更像一篇能读懂的文章。'
        ),
    },
    {
        'title': '技术小记：为什么网站通常需要 Nginx 和 Gunicorn',
        'category': 'tech',
        'tags': '自动草稿,技术',
        'content': (
            '一个 Django 网站上线时，经常会看到 Nginx 和 Gunicorn 一起出现。\n\n'
            'Nginx 负责站在最外面接收浏览器请求，也负责处理静态文件。Gunicorn 负责运行 Django 程序，'
            '把动态页面交给 Django 生成。\n\n'
            '简单说，Nginx 像门口接待，Gunicorn 像后面的业务窗口，Django 才是真正处理网站逻辑的人。'
        ),
    },
]


class Command(BaseCommand):
    help = 'Create one draft article for the current server boot.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            default=os.getenv('STARTUP_POST_USERNAME', '白车轴草'),
            help='Username that owns the generated article.',
        )
        parser.add_argument(
            '--publish',
            action='store_true',
            help='Create the generated article as a published post instead of a draft.',
        )

    def handle(self, *args, **options):
        username = options['username']
        should_publish = options['publish']
        boot_id = self.get_boot_id()
        boot_tag = f'boot:{boot_id}'

        author = User.objects.filter(username=username).first()
        if author is None:
            raise CommandError(f'User "{username}" does not exist.')

        existing_post = Post.objects.filter(author=author, tags__icontains=boot_tag).first()
        if existing_post is not None:
            self.stdout.write(self.style.WARNING(f'Generated article already exists: {existing_post.title}'))
            return

        current_time = timezone.localtime()
        formatted_time = current_time.strftime('%Y-%m-%d %H:%M:%S')
        article_template = self.get_article_template(boot_id)
        title = f"{article_template['title']} - {formatted_time}"
        content = f"{article_template['content']}\n\n生成时间：{formatted_time}"
        status = 'published' if should_publish else 'draft'

        post = Post.objects.create(
            author=author,
            title=title,
            category=article_template['category'],
            tags=f"{article_template['tags']},{boot_tag}",
            content=content,
            status=status,
        )

        self.stdout.write(self.style.SUCCESS(f'Created generated article: {post.title}'))

    def get_article_template(self, boot_id):
        boot_id_score = sum(ord(character) for character in boot_id)
        article_template_index = boot_id_score % len(ARTICLE_TEMPLATES)
        return ARTICLE_TEMPLATES[article_template_index]

    def get_boot_id(self):
        boot_id_path = '/proc/sys/kernel/random/boot_id'
        if os.path.exists(boot_id_path):
            with open(boot_id_path, encoding='utf-8') as boot_id_file:
                return boot_id_file.read().strip()
        return timezone.now().isoformat()
