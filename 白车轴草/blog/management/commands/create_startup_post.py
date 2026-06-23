import os

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from blog.models import Post


class Command(BaseCommand):
    help = 'Create one published post for the current server boot.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--username',
            default=os.getenv('STARTUP_POST_USERNAME', '白车轴草'),
            help='Username that owns the startup post.',
        )

    def handle(self, *args, **options):
        username = options['username']
        boot_id = self.get_boot_id()
        boot_tag = f'boot:{boot_id}'

        author = User.objects.filter(username=username).first()
        if author is None:
            raise CommandError(f'User "{username}" does not exist.')

        existing_post = Post.objects.filter(author=author, tags__icontains=boot_tag).first()
        if existing_post is not None:
            self.stdout.write(self.style.WARNING(f'Startup post already exists: {existing_post.title}'))
            return

        current_time = timezone.localtime()
        formatted_time = current_time.strftime('%Y-%m-%d %H:%M:%S')
        title = f'服务器启动记录 - {formatted_time}'
        content = (
            f'服务器在 {formatted_time} 启动。\n\n'
            '这篇文章由系统开机任务自动创建，用来确认网站服务已经随服务器启动。'
        )

        post = Post.objects.create(
            author=author,
            title=title,
            category='project',
            tags=f'server-startup,{boot_tag}',
            content=content,
            status='published',
        )

        self.stdout.write(self.style.SUCCESS(f'Created startup post: {post.title}'))

    def get_boot_id(self):
        boot_id_path = '/proc/sys/kernel/random/boot_id'
        if os.path.exists(boot_id_path):
            with open(boot_id_path, encoding='utf-8') as boot_id_file:
                return boot_id_file.read().strip()
        return timezone.now().isoformat()
