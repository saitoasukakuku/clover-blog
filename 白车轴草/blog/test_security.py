import os
import re
import tempfile
import uuid
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth.models import AnonymousUser, User
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.http import HttpResponse
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from PIL import Image

from blog.external_io import RestrictedRedirectHandler, read_limited_text_response
from blog.management.commands.create_startup_post import Command as StartupPostCommand
from blog.management.commands.process_site_tasks import Command as TaskWorkerCommand
from blog.media_security import (
    has_valid_audio_signature,
    validate_image_bytes,
    validate_uploaded_image,
)
from blog.models import (
    BackgroundTask,
    Notification,
    Post,
    PostImage,
    PostRevision,
    RateLimitState,
    UserBlock,
)
from blog.context_processors import read_mp3_id3_metadata
from blog.request_throttling import (
    consume_rate_limit,
    get_client_identity,
    get_client_ip,
)
from blog.security_middleware import TrustedProxyHeadersMiddleware
from blog.templatetags.blog_extras import post_content
from blog.views import move_music_assets


def build_image_bytes(image_format='JPEG', size=(32, 32)):
    image_buffer = BytesIO()
    Image.new('RGB', size, color=(44, 128, 92)).save(
        image_buffer,
        format=image_format,
    )
    return image_buffer.getvalue()


class SecurityHeaderTests(TestCase):
    def setUp(self):
        self.request_factory = RequestFactory()

    def test_html_response_has_matching_csp_nonce_and_permissions_policy(self):
        response = self.client.get(reverse('index'))
        response_html = response.content.decode('utf-8')
        nonce_match = re.search(r'data-csp-nonce="([^"]+)"', response_html)

        self.assertIsNotNone(nonce_match)
        self.assertIn(
            f"'nonce-{nonce_match.group(1)}'",
            response['Content-Security-Policy'],
        )
        self.assertIn("object-src 'none'", response['Content-Security-Policy'])
        self.assertIn('camera=()', response['Permissions-Policy'])

    def test_authenticated_pages_are_not_stored_in_shared_or_browser_caches(self):
        User.objects.create_user(username='cache-reader', password='StrongPass12345')
        self.client.login(username='cache-reader', password='StrongPass12345')

        response = self.client.get(reverse('index'))

        cache_control = response['Cache-Control']
        self.assertIn('private', cache_control)
        self.assertIn('no-store', cache_control)
        self.assertIn('max-age=0', cache_control)

    def test_canonical_url_excludes_search_and_pagination_parameters(self):
        response = self.client.get(reverse('index'), {'q': 'python', 'page': '2'})

        self.assertContains(
            response,
            f'<link rel="canonical" href="http://testserver{reverse("index")}">',
            html=True,
        )
        self.assertNotContains(response, 'rel="canonical" href="http://testserver/index/?')

    def test_homepage_copy_is_assigned_as_text_not_html(self):
        template_path = os.path.join(
            os.path.dirname(__file__),
            'templates',
            'home.html',
        )
        with open(template_path, 'r', encoding='utf-8') as template_file:
            template_source = template_file.read()

        self.assertIn('kickerText.textContent = slide.kicker', template_source)
        self.assertNotIn('kicker.innerHTML = slide.kicker', template_source)

    def test_markdown_preview_uses_same_https_image_rule_as_server(self):
        template_path = os.path.join(
            os.path.dirname(__file__),
            'templates',
            'includes',
            'markdown_editor_script.html',
        )
        with open(template_path, 'r', encoding='utf-8') as template_file:
            template_source = template_file.read()

        self.assertIn("if (/^https:/i.test(rawUrl))", template_source)
        self.assertNotIn("['http:', 'https:'].indexOf(parsedUrl.protocol)", template_source)

    @override_settings(TRUSTED_PROXY_IPS=frozenset({'127.0.0.1'}))
    def test_untrusted_peer_cannot_spoof_forwarded_https(self):
        captured_request_state = {}

        def capture_request(request):
            captured_request_state['is_secure'] = request.is_secure()
            captured_request_state['forwarded_host'] = request.META.get(
                'HTTP_X_FORWARDED_HOST'
            )
            return HttpResponse('ok')

        request = self.request_factory.get(
            '/',
            REMOTE_ADDR='203.0.113.30',
            HTTP_X_FORWARDED_PROTO='https',
            HTTP_X_FORWARDED_HOST='attacker.example',
        )

        TrustedProxyHeadersMiddleware(capture_request)(request)

        self.assertFalse(captured_request_state['is_secure'])
        self.assertIsNone(captured_request_state['forwarded_host'])

    @override_settings(TRUSTED_PROXY_IPS=frozenset({'127.0.0.1'}))
    def test_trusted_proxy_can_mark_request_as_https(self):
        captured_request_state = {}

        def capture_request(request):
            captured_request_state['is_secure'] = request.is_secure()
            return HttpResponse('ok')

        request = self.request_factory.get(
            '/',
            REMOTE_ADDR='127.0.0.1',
            HTTP_X_FORWARDED_PROTO='https',
        )

        TrustedProxyHeadersMiddleware(capture_request)(request)

        self.assertTrue(captured_request_state['is_secure'])


class ProtectedPostMediaTests(TestCase):
    def setUp(self):
        self.protected_media_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.protected_media_directory.cleanup)
        self.protected_settings = override_settings(
            PROTECTED_MEDIA_ROOT=self.protected_media_directory.name,
        )
        self.protected_settings.enable()
        self.addCleanup(self.protected_settings.disable)
        self.owner = User.objects.create_user(
            username='media-owner',
            password='StrongPass12345',
        )
        self.other_user = User.objects.create_user(
            username='other-reader',
            password='StrongPass12345',
        )

    def create_post_image(self):
        post_image = PostImage(owner=self.owner, original_name='private.jpg')
        post_image.image.save(
            'private.jpg',
            ContentFile(build_image_bytes()),
            save=True,
        )
        return post_image

    def test_unassociated_post_image_is_only_readable_by_owner(self):
        post_image = self.create_post_image()
        image_url = reverse('post_image_file', args=[post_image.public_id])

        anonymous_response = self.client.get(image_url)
        self.client.login(username='other-reader', password='StrongPass12345')
        other_response = self.client.get(image_url)
        self.client.login(username='media-owner', password='StrongPass12345')
        owner_response = self.client.get(image_url)

        self.assertEqual(anonymous_response.status_code, 404)
        self.assertEqual(other_response.status_code, 404)
        self.assertEqual(owner_response.status_code, 200)
        self.assertIn('private', owner_response['Cache-Control'])
        self.assertIn('no-store', owner_response['Cache-Control'])
        owner_response.close()

    def test_image_becomes_public_only_after_public_post_association(self):
        post_image = self.create_post_image()
        private_post = Post.objects.create(
            author=self.owner,
            title='私密文章',
            category='life',
            content='私密正文',
            status='published',
            visibility='private',
        )
        private_post.body_images.add(post_image)
        image_url = reverse('post_image_file', args=[post_image.public_id])

        private_response = self.client.get(image_url)
        private_post.visibility = 'public'
        private_post.save(update_fields=['visibility'])
        public_response = self.client.get(image_url)

        self.assertEqual(private_response.status_code, 404)
        self.assertEqual(public_response.status_code, 200)
        self.assertIn('public', public_response['Cache-Control'])
        self.assertIn('max-age=3600', public_response['Cache-Control'])
        public_response.close()

    def test_private_cover_is_hidden_but_public_cover_is_cacheable(self):
        post = Post.objects.create(
            author=self.owner,
            title='带封面的文章',
            category='life',
            content='正文',
            status='published',
            visibility='private',
        )
        post.cover.save(
            'cover.jpg',
            ContentFile(build_image_bytes()),
            save=True,
        )
        cover_url = reverse('post_cover', args=[post.id])

        private_response = self.client.get(cover_url)
        self.client.login(username='media-owner', password='StrongPass12345')
        owner_response = self.client.get(cover_url)
        self.client.logout()
        post.visibility = 'public'
        post.save(update_fields=['visibility'])
        public_response = self.client.get(cover_url)

        self.assertEqual(private_response.status_code, 404)
        self.assertEqual(owner_response.status_code, 200)
        self.assertIn('no-store', owner_response['Cache-Control'])
        self.assertEqual(public_response.status_code, 200)
        self.assertIn('public', public_response['Cache-Control'])
        owner_response.close()
        public_response.close()

    def test_public_post_outputs_permission_checked_open_graph_cover(self):
        post = Post.objects.create(
            author=self.owner,
            title='分享封面',
            category='life',
            content='正文',
            status='published',
            visibility='public',
        )
        post.cover.save(
            'share.jpg',
            ContentFile(build_image_bytes()),
            save=True,
        )

        response = self.client.get(reverse('post_detail', args=[post.id]))

        expected_cover_url = reverse('post_cover', args=[post.id])
        self.assertContains(
            response,
            f'<meta property="og:image" content="http://testserver{expected_cover_url}">',
            html=True,
        )

    def test_replaced_cover_file_is_deleted_after_database_commit(self):
        post = Post.objects.create(
            author=self.owner,
            title='替换封面',
            category='life',
            content='正文',
            status='draft',
            visibility='private',
        )
        post.cover.save(
            'original.jpg',
            ContentFile(build_image_bytes()),
            save=True,
        )
        original_cover_path = post.cover.path

        with self.captureOnCommitCallbacks(execute=True):
            post.cover.save(
                'replacement.jpg',
                ContentFile(build_image_bytes()),
                save=True,
            )

        self.assertFalse(os.path.exists(original_cover_path))
        self.assertTrue(os.path.exists(post.cover.path))

    def test_markdown_renders_exact_protected_image_url(self):
        public_id = uuid.uuid4()
        rendered_content = str(
            post_content(f'![正文图片](/post-images/{public_id}/)')
        )
        unsafe_content = str(post_content('![危险](javascript:alert(1))'))

        self.assertIn(f'<img src="/post-images/{public_id}/"', rendered_content)
        self.assertNotIn('<img', unsafe_content)

    def test_legacy_public_media_routes_are_always_blocked(self):
        cover_response = self.client.get(
            reverse('legacy_post_cover', args=['legacy-cover.jpg'])
        )
        body_image_response = self.client.get(
            reverse('legacy_post_image', args=['legacy-body.jpg'])
        )

        self.assertEqual(cover_response.status_code, 404)
        self.assertEqual(body_image_response.status_code, 404)

    def test_markdown_remote_images_require_https(self):
        secure_content = str(post_content('![安全图](https://example.com/a.jpg)'))
        insecure_content = str(post_content('![不安全图](http://example.com/a.jpg)'))
        legacy_private_content = str(
            post_content('![旧私有图](/media/post_images/legacy.jpg)')
        )

        self.assertIn('<img src="https://example.com/a.jpg"', secure_content)
        self.assertNotIn('<img', insecure_content)
        self.assertNotIn('<img', legacy_private_content)

    def test_legacy_revision_only_image_is_migrated_idempotently(self):
        with tempfile.TemporaryDirectory() as public_media_root:
            legacy_image_directory = os.path.join(public_media_root, 'post_images')
            os.makedirs(legacy_image_directory)
            with open(
                os.path.join(legacy_image_directory, 'revision-only.jpg'),
                'wb',
            ) as legacy_image_file:
                legacy_image_file.write(build_image_bytes())
            post = Post.objects.create(
                author=self.owner,
                title='历史版本图片',
                category='life',
                content='当前正文没有图片',
                status='draft',
                visibility='private',
            )
            revision = PostRevision.create_from_post(post, self.owner)
            revision.content = '![旧图](/media/post_images/revision-only.jpg)'
            revision.save(update_fields=['content'])

            with override_settings(MEDIA_ROOT=public_media_root):
                call_command('migrate_private_media')
                call_command('migrate_private_media')

        revision.refresh_from_db()
        self.assertIn('/post-images/', revision.content)
        self.assertEqual(PostImage.objects.count(), 1)
        self.assertEqual(post.body_images.count(), 0)


class RequestThrottlingTests(TestCase):
    def setUp(self):
        self.request_factory = RequestFactory()

    @override_settings(TRUSTED_PROXY_IPS=frozenset({'127.0.0.1'}))
    def test_trusted_proxy_uses_rightmost_forwarded_client_address(self):
        request = self.request_factory.get(
            '/',
            REMOTE_ADDR='127.0.0.1',
            HTTP_X_FORWARDED_FOR='192.0.2.15, 198.51.100.21',
        )

        self.assertEqual(get_client_ip(request), '198.51.100.21')

    @override_settings(TRUSTED_PROXY_IPS=frozenset({'127.0.0.1'}))
    def test_untrusted_peer_cannot_spoof_forwarded_address(self):
        request = self.request_factory.get(
            '/',
            REMOTE_ADDR='203.0.113.9',
            HTTP_X_FORWARDED_FOR='192.0.2.99',
        )

        self.assertEqual(get_client_ip(request), '203.0.113.9')

    def test_authenticated_limit_identity_is_stable_per_user(self):
        user = User.objects.create_user(username='limited-user')
        request = self.request_factory.get('/', REMOTE_ADDR='203.0.113.10')
        request.user = user

        self.assertEqual(get_client_identity(request), f'user:{user.pk}')

    def test_limit_blocks_after_threshold_without_storing_raw_ip(self):
        request = self.request_factory.get('/', REMOTE_ADDR='203.0.113.11')
        request.user = AnonymousUser()

        first_retry_after = consume_rate_limit(
            request,
            'security-test',
            limit=1,
            window_seconds=60,
            block_seconds=120,
        )
        second_retry_after = consume_rate_limit(
            request,
            'security-test',
            limit=1,
            window_seconds=60,
            block_seconds=120,
        )

        rate_state = RateLimitState.objects.get(action='security-test')
        self.assertEqual(first_retry_after, 0)
        self.assertEqual(second_retry_after, 120)
        self.assertEqual(rate_state.request_count, 2)
        self.assertNotIn('203.0.113.11', rate_state.key_hash)

    def test_admin_login_view_returns_retry_after_when_limited(self):
        with patch(
            'blog.admin_views.consume_rate_limit',
            return_value=120,
        ):
            response = self.client.post('/admin/login/', {
                'username': 'admin',
                'password': 'wrong-password',
            })

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response['Retry-After'], '120')

    def test_admin_login_csrf_failure_does_not_consume_rate_limit(self):
        csrf_client = Client(enforce_csrf_checks=True)
        with patch('blog.admin_views.consume_rate_limit') as rate_limit_mock:
            response = csrf_client.post('/admin/login/', {
                'username': 'admin',
                'password': 'wrong-password',
            })

        self.assertEqual(response.status_code, 403)
        rate_limit_mock.assert_not_called()


class UploadedMediaValidationTests(TestCase):
    def test_oversized_id3_metadata_declaration_is_not_loaded(self):
        oversized_tag_size = 17 * 1024 * 1024
        id3_header = (
            b'ID3\x04\x00\x00'
            + bytes([
                (oversized_tag_size >> 21) & 0x7F,
                (oversized_tag_size >> 14) & 0x7F,
                (oversized_tag_size >> 7) & 0x7F,
                oversized_tag_size & 0x7F,
            ])
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            audio_file_path = os.path.join(temporary_directory, 'oversized.mp3')
            with open(audio_file_path, 'wb') as audio_file:
                audio_file.write(id3_header)

            metadata = read_mp3_id3_metadata(audio_file_path)

        self.assertEqual(metadata['title'], '')
        self.assertIsNone(metadata['embedded_cover'])

    def test_music_asset_rename_rolls_back_completed_moves_on_failure(self):
        with tempfile.TemporaryDirectory() as music_directory:
            first_source_path = os.path.join(music_directory, 'old.mp3')
            second_source_path = os.path.join(music_directory, 'old.lrc')
            with open(first_source_path, 'wb') as first_source_file:
                first_source_file.write(b'audio')
            with open(second_source_path, 'wb') as second_source_file:
                second_source_file.write(b'lyrics')

            real_replace = os.replace

            def replace_with_second_move_failure(source_path, target_path):
                if source_path.endswith('old.lrc'):
                    raise OSError('simulated move failure')
                return real_replace(source_path, target_path)

            with patch(
                'blog.views.os.replace',
                side_effect=replace_with_second_move_failure,
            ):
                with self.assertRaises(OSError):
                    move_music_assets(
                        music_directory,
                        [
                            ('old.mp3', 'new.mp3'),
                            ('old.lrc', 'new.lrc'),
                        ],
                    )

            self.assertTrue(os.path.isfile(first_source_path))
            self.assertTrue(os.path.isfile(second_source_path))
            self.assertFalse(os.path.exists(os.path.join(music_directory, 'new.mp3')))
            self.assertFalse(os.path.exists(os.path.join(music_directory, 'new.lrc')))

    def test_validated_image_uses_detected_format_in_file_name(self):
        uploaded_image = SimpleUploadedFile(
            'misnamed.jpg',
            build_image_bytes('PNG'),
            content_type='image/jpeg',
        )

        validated_image = validate_uploaded_image(uploaded_image)

        self.assertEqual(validated_image.name, 'misnamed.png')

    def test_unsupported_gif_is_rejected_even_with_jpeg_name(self):
        uploaded_image = SimpleUploadedFile(
            'animated.jpg',
            build_image_bytes('GIF'),
            content_type='image/jpeg',
        )

        with self.assertRaises(ValueError):
            validate_uploaded_image(uploaded_image)

    def test_extreme_image_dimension_is_rejected(self):
        oversized_dimension_image = build_image_bytes('PNG', size=(12001, 1))

        with self.assertRaises(ValueError):
            validate_image_bytes(oversized_dimension_image)

    def test_audio_extension_spoof_is_rejected(self):
        spoofed_audio = BytesIO(b'plain text pretending to be mp3')

        self.assertFalse(has_valid_audio_signature(spoofed_audio, '.mp3'))

    def test_invalid_manually_added_homepage_image_returns_not_found(self):
        with tempfile.TemporaryDirectory() as temporary_media_root:
            image_directory = os.path.join(temporary_media_root, 'index_img')
            os.makedirs(image_directory)
            with open(os.path.join(image_directory, 'invalid.jpg'), 'wb') as image_file:
                image_file.write(build_image_bytes('GIF'))

            with override_settings(MEDIA_ROOT=temporary_media_root):
                response = self.client.get(
                    reverse('homepage_carousel_image', args=['invalid.jpg'])
                )

        self.assertEqual(response.status_code, 404)


class MediaManagerSecurityTests(TestCase):
    def test_music_lyrics_are_loaded_only_for_superuser_on_demand(self):
        superuser = User.objects.create_superuser(
            username='media-details-admin',
            password='StrongPass12345',
        )
        regular_user = User.objects.create_user(
            username='media-details-user',
            password='StrongPass12345',
        )
        with tempfile.TemporaryDirectory() as temporary_media_root:
            music_directory = os.path.join(temporary_media_root, 'music')
            os.makedirs(music_directory)
            with open(os.path.join(music_directory, 'private-song.mp3'), 'wb') as audio_file:
                audio_file.write(b'ID3')
            lyrics_text = '[00:01.00]只在详情请求加载'
            with open(
                os.path.join(music_directory, 'private-song.lrc'),
                'w',
                encoding='utf-8',
            ) as lyrics_file:
                lyrics_file.write(lyrics_text)

            with override_settings(MEDIA_ROOT=temporary_media_root):
                self.client.force_login(superuser)
                manager_response = self.client.get(reverse('media_manager'))
                details_response = self.client.get(
                    reverse('media_manager_music_details'),
                    {'file_name': 'private-song.mp3'},
                )
                self.client.force_login(regular_user)
                forbidden_response = self.client.get(
                    reverse('media_manager_music_details'),
                    {'file_name': 'private-song.mp3'},
                )

        self.assertNotContains(manager_response, lyrics_text)
        self.assertEqual(details_response.status_code, 200)
        self.assertEqual(details_response.json()['lyrics_text'], lyrics_text)
        self.assertEqual(forbidden_response.status_code, 403)


class ExternalRequestSecurityTests(TestCase):
    def test_text_response_reader_rejects_oversized_body(self):
        class OversizedResponse:
            headers = {}

            def read(self, size=-1):
                return b'x' * size

        with self.assertRaises(ValueError):
            read_limited_text_response(OversizedResponse(), maximum_bytes=16)

    def test_pexels_download_rejects_non_pexels_target_before_network(self):
        with patch('blog.external_io.build_opener') as build_opener_mock:
            with self.assertRaises(CommandError):
                StartupPostCommand().download_pexels_image(
                    'http://127.0.0.1/internal-metadata',
                )

        build_opener_mock.assert_not_called()

    def test_pexels_redirect_cannot_leave_allowed_https_host(self):
        redirect_handler = RestrictedRedirectHandler({'images.pexels.com'})
        request = type(
            'RedirectRequest',
            (),
            {'full_url': 'https://images.pexels.com/photos/1/image.jpg'},
        )()

        with self.assertRaises(ValueError):
            redirect_handler.redirect_request(
                request,
                None,
                302,
                'Found',
                {},
                'http://127.0.0.1/internal-metadata',
            )


class BackgroundTaskRecoveryTests(TestCase):
    def test_cleanup_command_removes_only_expired_runtime_state(self):
        current_time = timezone.now()
        expired_rate_state = RateLimitState.objects.create(
            action='expired',
            key_hash='a' * 64,
        )
        current_rate_state = RateLimitState.objects.create(
            action='current',
            key_hash='b' * 64,
        )
        RateLimitState.objects.filter(id=expired_rate_state.id).update(
            updated_at=current_time - timedelta(days=8),
        )
        expired_task = BackgroundTask.objects.create(
            task_type=BackgroundTask.TYPE_PREPARE_MUSIC,
            status=BackgroundTask.STATUS_SUCCEEDED,
            finished_at=current_time - timedelta(days=31),
        )
        current_task = BackgroundTask.objects.create(
            task_type=BackgroundTask.TYPE_PREPARE_MUSIC,
            status=BackgroundTask.STATUS_SUCCEEDED,
            finished_at=current_time,
        )

        call_command('cleanup_site_state')

        self.assertFalse(
            RateLimitState.objects.filter(id=expired_rate_state.id).exists()
        )
        self.assertTrue(
            RateLimitState.objects.filter(id=current_rate_state.id).exists()
        )
        self.assertFalse(
            BackgroundTask.objects.filter(id=expired_task.id).exists()
        )
        self.assertTrue(
            BackgroundTask.objects.filter(id=current_task.id).exists()
        )

    def test_worker_fails_only_tasks_older_than_stale_threshold(self):
        stale_task = BackgroundTask.objects.create(
            task_type=BackgroundTask.TYPE_PREPARE_MUSIC,
            status=BackgroundTask.STATUS_RUNNING,
            started_at=timezone.now() - timedelta(hours=7),
        )
        current_task = BackgroundTask.objects.create(
            task_type=BackgroundTask.TYPE_GENERATE_HOMEPAGE_COPY,
            status=BackgroundTask.STATUS_RUNNING,
            started_at=timezone.now(),
        )

        recovered_count = TaskWorkerCommand().fail_stale_tasks(21600)
        stale_task.refresh_from_db()
        current_task.refresh_from_db()

        self.assertEqual(recovered_count, 1)
        self.assertEqual(stale_task.status, BackgroundTask.STATUS_FAILED)
        self.assertIsNotNone(stale_task.finished_at)
        self.assertEqual(current_task.status, BackgroundTask.STATUS_RUNNING)

    def test_worker_records_successful_command_completion(self):
        task = BackgroundTask.objects.create(
            task_type=BackgroundTask.TYPE_PREPARE_MUSIC,
            status=BackgroundTask.STATUS_RUNNING,
            started_at=timezone.now(),
        )

        with patch(
            'blog.management.commands.process_site_tasks.call_command'
        ) as call_command_mock:
            TaskWorkerCommand().run_task(task)

        task.refresh_from_db()
        self.assertEqual(task.status, BackgroundTask.STATUS_SUCCEEDED)
        self.assertIsNotNone(task.finished_at)
        call_command_mock.assert_called_once()


class ContentIntegrityTests(TestCase):
    def test_public_search_query_is_bounded_before_database_filtering(self):
        response = self.client.get(reverse('index'), {'q': 'x' * 500})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['search_query'], 'x' * 100)

    @override_settings(DEBUG=False)
    def test_login_next_url_cannot_downgrade_to_http_in_production(self):
        User.objects.create_user(
            username='secure-login',
            password='StrongPass12345',
        )

        response = self.client.post(
            f'{reverse("login")}?next=http://testserver/favorites/',
            {
                'username': 'secure-login',
                'password': 'StrongPass12345',
            },
        )

        self.assertRedirects(
            response,
            reverse('index'),
            fetch_redirect_response=False,
        )

    def test_post_submission_rejects_invalid_visibility(self):
        User.objects.create_user(username='post-writer', password='StrongPass12345')
        self.client.login(username='post-writer', password='StrongPass12345')

        response = self.client.post(reverse('create_post'), {
            'title': '非法可见范围',
            'category': 'life',
            'content': '正文',
            'visibility': 'everyone-and-search-engines',
            'action': 'publish',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Post.objects.exists())
        self.assertContains(response, '请选择有效的可见范围。')

    def test_tag_merge_is_case_insensitive_and_deduplicates_target(self):
        admin = User.objects.create_superuser(
            username='tag-admin',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=admin,
            title='标签文章',
            category='tech',
            tags='编程,Python',
            content='正文',
            status='published',
            visibility='public',
        )
        self.client.login(username='tag-admin', password='StrongPass12345')

        response = self.client.post(reverse('tag_manager'), {
            'source_tag': 'PYTHON',
            'target_tag': '编程',
        })

        self.assertRedirects(response, reverse('tag_manager'))
        post.refresh_from_db()
        self.assertEqual(post.tags, '编程')

    def test_post_view_is_counted_once_during_session_cooldown(self):
        author = User.objects.create_user(username='view-author')
        post = Post.objects.create(
            author=author,
            title='只计一次',
            category='life',
            content='正文',
            status='published',
            visibility='public',
        )

        self.client.get(reverse('post_detail', args=[post.id]))
        self.client.get(reverse('post_detail', args=[post.id]))
        post.refresh_from_db()

        self.assertEqual(post.views_count, 1)

    def test_post_detail_rejects_post_without_counting_a_view(self):
        author = User.objects.create_user(username='method-author')
        post = Post.objects.create(
            author=author,
            title='只允许读取',
            category='life',
            content='正文',
            status='published',
            visibility='public',
        )

        response = self.client.post(reverse('post_detail', args=[post.id]))
        post.refresh_from_db()

        self.assertEqual(response.status_code, 405)
        self.assertEqual(post.views_count, 0)

    def test_scheduled_post_cannot_be_commented_or_leak_into_rss(self):
        owner = User.objects.create_superuser(
            username='root',
            password='StrongPass12345',
        )
        commenter = User.objects.create_user(
            username='scheduled-commenter',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=owner,
            title='尚未到时间的文章',
            category='life',
            content='正文',
            status='published',
            visibility='public',
            scheduled_publish_at=timezone.now() + timedelta(days=1),
        )
        self.client.login(
            username='scheduled-commenter',
            password='StrongPass12345',
        )

        comment_response = self.client.post(
            reverse('add_comment', args=[post.id]),
            {'content': '提前评论'},
        )
        self.client.logout()
        rss_response = self.client.get(reverse('rss_feed'))

        self.assertEqual(comment_response.status_code, 404)
        self.assertFalse(post.comments.exists())
        self.assertNotContains(rss_response, post.title)

    def test_hidden_parent_comment_cannot_receive_new_replies(self):
        author = User.objects.create_user(username='hidden-author')
        commenter = User.objects.create_user(
            username='hidden-commenter',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=author,
            title='隐藏评论文章',
            category='life',
            content='正文',
            status='published',
            visibility='public',
        )
        hidden_comment = post.comments.create(
            author=author,
            content='已隐藏',
            is_hidden=True,
        )
        self.client.login(
            username='hidden-commenter',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('add_comment', args=[post.id]),
            {
                'content': '尝试回复',
                'parent_id': hidden_comment.id,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(hidden_comment.replies.exists())

    def test_blocked_user_cannot_reply_or_mention_through_third_party_post(self):
        post_author = User.objects.create_user(username='third-party-author')
        blocked_user = User.objects.create_user(username='blocked-target')
        actor = User.objects.create_user(
            username='blocked-actor',
            password='StrongPass12345',
        )
        UserBlock.objects.create(blocker=blocked_user, blocked=actor)
        post = Post.objects.create(
            author=post_author,
            title='第三方文章',
            category='life',
            content='正文',
            status='published',
            visibility='public',
        )
        parent_comment = post.comments.create(
            author=blocked_user,
            content='原评论',
        )
        self.client.login(
            username='blocked-actor',
            password='StrongPass12345',
        )

        reply_response = self.client.post(
            reverse('add_comment', args=[post.id]),
            {
                'content': '@blocked-target 尝试骚扰',
                'parent_id': parent_comment.id,
            },
        )

        self.assertRedirects(
            reply_response,
            reverse('post_detail', args=[post.id]),
        )
        self.assertFalse(parent_comment.replies.exists())
        self.assertFalse(
            Notification.objects.filter(recipient=blocked_user).exists()
        )
