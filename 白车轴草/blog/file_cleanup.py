import logging

from django.db import transaction
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from blog.models import Post, PostImage, UserProfile


logger = logging.getLogger(__name__)


def schedule_file_delete(storage, file_name):
    if not file_name:
        return

    def delete_file_after_commit():
        try:
            storage.delete(file_name)
        except Exception:
            logger.exception('Stored file cleanup failed for %s.', file_name)

    transaction.on_commit(delete_file_after_commit)


def schedule_replaced_file_delete(instance, model, field_name):
    if not instance.pk:
        return
    previous_file_name = (
        model.objects.filter(pk=instance.pk)
        .values_list(field_name, flat=True)
        .first()
    )
    current_file = getattr(instance, field_name)
    current_file_name = current_file.name if current_file else ''
    if previous_file_name and previous_file_name != current_file_name:
        field = model._meta.get_field(field_name)
        schedule_file_delete(field.storage, previous_file_name)


@receiver(pre_save, sender=Post)
def delete_replaced_post_cover(sender, instance, **kwargs):
    schedule_replaced_file_delete(instance, sender, 'cover')


@receiver(post_delete, sender=Post)
def delete_removed_post_cover(sender, instance, **kwargs):
    if instance.cover:
        schedule_file_delete(instance.cover.storage, instance.cover.name)


@receiver(pre_save, sender=PostImage)
def delete_replaced_post_image(sender, instance, **kwargs):
    schedule_replaced_file_delete(instance, sender, 'image')


@receiver(post_delete, sender=PostImage)
def delete_removed_post_image(sender, instance, **kwargs):
    if instance.image:
        schedule_file_delete(instance.image.storage, instance.image.name)


@receiver(pre_save, sender=UserProfile)
def delete_replaced_avatar(sender, instance, **kwargs):
    schedule_replaced_file_delete(instance, sender, 'avatar')


@receiver(post_delete, sender=UserProfile)
def delete_removed_avatar(sender, instance, **kwargs):
    if instance.avatar:
        schedule_file_delete(instance.avatar.storage, instance.avatar.name)
