from django.core.management.base import BaseCommand

from blog.models import BackgroundTask


class Command(BaseCommand):
    help = 'Enqueue a supported site background task unless one is already active.'

    def add_arguments(self, parser):
        parser.add_argument(
            'task_type',
            choices=[
                BackgroundTask.TYPE_PREPARE_MUSIC,
                BackgroundTask.TYPE_GENERATE_HOMEPAGE_COPY,
            ],
        )

    def handle(self, *args, **options):
        task_type = options['task_type']
        active_task = BackgroundTask.objects.filter(
            task_type=task_type,
            status__in={
                BackgroundTask.STATUS_PENDING,
                BackgroundTask.STATUS_RUNNING,
            },
        ).first()
        if active_task:
            self.stdout.write(f'Task already active: {active_task.public_id}')
            return
        task = BackgroundTask.objects.create(task_type=task_type)
        self.stdout.write(self.style.SUCCESS(f'Enqueued task: {task.public_id}'))
