import time
from datetime import timedelta
from io import StringIO

from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from blog.models import BackgroundTask


class Command(BaseCommand):
    help = 'Process queued site background tasks.'

    def add_arguments(self, parser):
        parser.add_argument('--watch', action='store_true', help='Keep polling for tasks.')
        parser.add_argument('--poll-interval', type=float, default=2.0)
        parser.add_argument('--max-tasks', type=int, default=1)
        parser.add_argument('--stale-after', type=int, default=21600)

    def handle(self, *args, **options):
        processed_count = 0
        while True:
            self.fail_stale_tasks(options['stale_after'])
            task = self.claim_next_task()
            if task is None:
                if options['watch']:
                    time.sleep(max(0.2, options['poll_interval']))
                    continue
                break

            self.run_task(task)
            processed_count += 1
            if not options['watch'] and processed_count >= max(1, options['max_tasks']):
                break

    def fail_stale_tasks(self, stale_after_seconds):
        stale_before = timezone.now() - timedelta(
            seconds=max(300, stale_after_seconds),
        )
        finished_at = timezone.now()
        return BackgroundTask.objects.filter(
            status=BackgroundTask.STATUS_RUNNING,
            started_at__lt=stale_before,
        ).update(
            status=BackgroundTask.STATUS_FAILED,
            error_message='后台 worker 中断，任务已超时；请重新提交。',
            finished_at=finished_at,
        )

    def claim_next_task(self):
        with transaction.atomic():
            task = (
                BackgroundTask.objects.select_for_update(skip_locked=True)
                .filter(status=BackgroundTask.STATUS_PENDING)
                .order_by('created_at')
                .first()
            )
            if task is None:
                return None
            task.status = BackgroundTask.STATUS_RUNNING
            task.started_at = timezone.now()
            task.save(update_fields=['status', 'started_at'])
            return task

    def run_task(self, task):
        command_output = StringIO()
        try:
            if task.task_type == BackgroundTask.TYPE_PREPARE_MUSIC:
                call_command(
                    'prepare_music_playback',
                    '--continue-on-error',
                    stdout=command_output,
                    stderr=command_output,
                )
            elif task.task_type == BackgroundTask.TYPE_GENERATE_HOMEPAGE_COPY:
                call_command(
                    'generate_homepage_copy',
                    '--batch-size',
                    '8',
                    stdout=command_output,
                    stderr=command_output,
                )
            else:
                raise ValueError(f'Unsupported task type: {task.task_type}')
        except Exception as error:
            task.status = BackgroundTask.STATUS_FAILED
            task.error_message = str(error)[:4000]
            task.output = command_output.getvalue()[-20000:]
            task.finished_at = timezone.now()
            task.save(
                update_fields=['status', 'error_message', 'output', 'finished_at']
            )
            self.stderr.write(f'Task {task.public_id} failed: {error}')
            return

        task.status = BackgroundTask.STATUS_SUCCEEDED
        task.output = command_output.getvalue()[-20000:]
        task.error_message = ''
        task.finished_at = timezone.now()
        task.save(
            update_fields=['status', 'output', 'error_message', 'finished_at']
        )
        self.stdout.write(f'Task {task.public_id} completed.')
