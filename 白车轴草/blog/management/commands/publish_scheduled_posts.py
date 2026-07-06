from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from blog.models import Post


class Command(BaseCommand):
    help = 'Publish draft posts whose scheduled publish time has arrived.'

    def handle(self, *args, **options):
        current_time = timezone.now()
        with transaction.atomic():
            due_posts = Post.objects.select_for_update().filter(
                status='draft',
                scheduled_publish_at__isnull=False,
                scheduled_publish_at__lte=current_time,
            )
            due_post_count = due_posts.update(
                status='published',
                scheduled_publish_at=None,
                updated_at=current_time,
            )
        self.stdout.write(f'Published scheduled posts: {due_post_count}')
