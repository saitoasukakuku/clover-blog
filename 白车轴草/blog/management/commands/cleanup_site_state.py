from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from blog.models import BackgroundTask, RateLimitState


class Command(BaseCommand):
    help = 'Delete expired request-limit state and old completed background tasks.'

    def add_arguments(self, parser):
        parser.add_argument('--rate-limit-days', type=int, default=7)
        parser.add_argument('--task-days', type=int, default=30)

    def handle(self, *args, **options):
        current_time = timezone.now()
        rate_limit_cutoff = current_time - timedelta(
            days=max(1, options['rate_limit_days'])
        )
        task_cutoff = current_time - timedelta(days=max(1, options['task_days']))

        deleted_rate_limit_count, _ = RateLimitState.objects.filter(
            updated_at__lt=rate_limit_cutoff,
        ).delete()
        deleted_task_count, _ = BackgroundTask.objects.filter(
            status__in=(
                BackgroundTask.STATUS_SUCCEEDED,
                BackgroundTask.STATUS_FAILED,
            ),
            finished_at__lt=task_cutoff,
        ).delete()

        self.stdout.write(
            self.style.SUCCESS(
                'Cleanup complete: '
                f'{deleted_rate_limit_count} rate-limit rows and '
                f'{deleted_task_count} background tasks deleted.'
            )
        )
