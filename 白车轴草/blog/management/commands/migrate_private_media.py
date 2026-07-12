import os

from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand
from django.db import transaction
from django.urls import reverse

from blog.models import Post, PostImage
from blog.atomic_files import atomic_copy_file
from blog.media_security import inspect_image
from blog.post_media import (
    LEGACY_POST_IMAGE_URL_PATTERN,
    decode_legacy_post_image_name,
    sync_post_body_images,
)


class Command(BaseCommand):
    help = 'Copy legacy covers and body images into protected media storage.'

    def handle(self, *args, **options):
        copied_cover_count = self.copy_legacy_covers()
        migrated_image_count = self.migrate_legacy_body_images()
        self.stdout.write(
            self.style.SUCCESS(
                f'Private media migration complete: {copied_cover_count} covers copied, '
                f'{migrated_image_count} body images migrated.'
            )
        )

    def copy_legacy_covers(self):
        copied_count = 0
        for post in Post.objects.exclude(cover='').exclude(cover__isnull=True).iterator():
            protected_path = post.cover.storage.path(post.cover.name)
            legacy_path = os.path.join(settings.MEDIA_ROOT, post.cover.name)
            if not os.path.isfile(legacy_path):
                continue
            if (
                os.path.isfile(protected_path)
                and os.path.getsize(protected_path) == os.path.getsize(legacy_path)
            ):
                continue
            try:
                inspect_image(legacy_path)
            except ValueError:
                self.stderr.write(f'Skipped invalid legacy cover: {post.cover.name}')
                continue
            atomic_copy_file(legacy_path, protected_path)
            copied_count += 1
        return copied_count

    def migrate_legacy_body_images(self):
        migrated_count = 0
        migrated_images = {}
        posts = Post.objects.exclude(content__isnull=True).select_related('author')
        for post in posts.iterator():
            legacy_matches = list(LEGACY_POST_IMAGE_URL_PATTERN.finditer(post.content or ''))
            if post.author_id is None:
                continue
            has_legacy_revision = post.revisions.filter(
                content__contains='/media/post_images/',
            ).exists()
            if not legacy_matches and not has_legacy_revision:
                continue

            updated_content = post.content
            post_images = []
            with transaction.atomic():
                for legacy_match in legacy_matches:
                    raw_url = legacy_match.group(0)
                    legacy_file_name = decode_legacy_post_image_name(legacy_match.group(1))
                    legacy_file_path = os.path.join(
                        settings.MEDIA_ROOT,
                        'post_images',
                        legacy_file_name,
                    )
                    if not os.path.isfile(legacy_file_path):
                        continue
                    post_image, was_created = self.get_or_create_post_image(
                        post,
                        legacy_file_name,
                        legacy_file_path,
                        migrated_images,
                    )
                    if post_image is None:
                        continue
                    migrated_count += int(was_created)

                    updated_content = updated_content.replace(
                        raw_url,
                        reverse('post_image_file', args=[post_image.public_id]),
                    )
                    post_images.append(post_image)

                if updated_content != post.content:
                    post.content = updated_content
                    post.save(update_fields=['content', 'updated_at'])
                if post_images:
                    post.body_images.add(*post_images)
                migrated_count += self.update_revisions(post, migrated_images)
                sync_post_body_images(post)
        return migrated_count

    def get_or_create_post_image(
        self,
        post,
        legacy_file_name,
        legacy_file_path,
        migrated_images,
    ):
        image_key = (post.author_id, legacy_file_name.casefold())
        post_image = migrated_images.get(image_key)
        if post_image is not None:
            return post_image, False
        try:
            inspect_image(legacy_file_path)
        except ValueError:
            self.stderr.write(f'Skipped invalid legacy post image: {legacy_file_name}')
            return None, False

        post_image = PostImage(
            owner=post.author,
            original_name=legacy_file_name[:255],
        )
        with open(legacy_file_path, 'rb') as legacy_file:
            post_image.image.save(
                legacy_file_name,
                File(legacy_file),
                save=True,
            )
        migrated_images[image_key] = post_image
        return post_image, True

    def update_revisions(self, post, migrated_images):
        migrated_count = 0
        for revision in post.revisions.all():
            updated_content = revision.content
            for legacy_match in LEGACY_POST_IMAGE_URL_PATTERN.finditer(revision.content or ''):
                legacy_file_name = decode_legacy_post_image_name(legacy_match.group(1))
                legacy_file_path = os.path.join(
                    settings.MEDIA_ROOT,
                    'post_images',
                    legacy_file_name,
                )
                if not os.path.isfile(legacy_file_path):
                    continue
                post_image, was_created = self.get_or_create_post_image(
                    post,
                    legacy_file_name,
                    legacy_file_path,
                    migrated_images,
                )
                if post_image:
                    migrated_count += int(was_created)
                    updated_content = updated_content.replace(
                        legacy_match.group(0),
                        reverse('post_image_file', args=[post_image.public_id]),
                    )
            if updated_content != revision.content:
                revision.content = updated_content
                revision.save(update_fields=['content'])
        return migrated_count
