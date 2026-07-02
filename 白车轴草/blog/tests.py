import json
import os
import tempfile
from datetime import datetime, timedelta
from html.parser import HTMLParser
from io import StringIO
from unittest.mock import patch

from django import forms
from django.contrib.auth.models import User
from django.apps import apps
from django.core import mail, signing
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from django.db.models.fields.files import FieldFile
from PIL import Image
from blog.management.commands.create_startup_post import Command
from blog.forms import CompleteRegistrationForm
from blog.models import (
    Comment,
    FriendRequest,
    Friendship,
    Post,
    PrivateMessage,
    RegistrationRequest,
    UserProfile,
)
from blog.views import AI_COVER_TOKEN_SALT


class DeletionFormParser(HTMLParser):
    def __init__(self, deletion_url):
        super().__init__()
        self.deletion_url = deletion_url
        self.is_inside_deletion_form = False
        self.deletion_form_found = False
        self.csrf_token_found = False
        self.submit_button_found = False
        self.delete_link_found = False

    def handle_starttag(self, tag, attributes):
        attribute_values = dict(attributes)

        if tag == 'a' and attribute_values.get('href') == self.deletion_url:
            self.delete_link_found = True

        if tag == 'form':
            form_method = attribute_values.get('method', '').lower()
            form_action = attribute_values.get('action')
            if form_method == 'post' and form_action == self.deletion_url:
                self.is_inside_deletion_form = True
                self.deletion_form_found = True
            return

        if not self.is_inside_deletion_form:
            return

        if tag == 'input':
            input_type = attribute_values.get('type', '').lower()
            input_name = attribute_values.get('name')
            input_value = attribute_values.get('value', '')
            if (
                input_type == 'hidden'
                and input_name == 'csrfmiddlewaretoken'
                and input_value
            ):
                self.csrf_token_found = True

        if tag == 'button':
            button_type = attribute_values.get('type', '').lower()
            if button_type == 'submit':
                self.submit_button_found = True

    def handle_endtag(self, tag):
        if tag == 'form' and self.is_inside_deletion_form:
            self.is_inside_deletion_form = False


class RegistrationRequestModelTests(TestCase):
    def create_request_with_invite_code(
        self,
        *,
        status,
        raw_invite_code='ABC123CODE456',
        code_expires_at=None,
        used_at=None,
    ):
        registration_request = RegistrationRequest(
            email='reader@example.com',
            status=status,
            code_expires_at=code_expires_at,
            used_at=used_at,
        )
        registration_request.set_invite_code(raw_invite_code)
        registration_request.save()
        return registration_request

    def test_invite_code_is_hashed_and_checkable(self):
        registration_request = RegistrationRequest(email='Reader@Example.COM')
        raw_invite_code = 'ABC123CODE456'

        registration_request.set_invite_code(raw_invite_code)
        registration_request.save()
        registration_request.refresh_from_db()

        self.assertEqual(registration_request.email, 'reader@example.com')
        self.assertNotEqual(registration_request.invite_code_hash, raw_invite_code)
        self.assertTrue(registration_request.check_invite_code(raw_invite_code))
        self.assertFalse(registration_request.check_invite_code('WRONGCODE789'))

    def test_reopen_clears_review_and_code_fields(self):
        reviewer = User.objects.create_superuser(
            username='reviewer',
            email='reviewer@example.com',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest(
            email='reader@example.com',
            status=RegistrationRequest.STATUS_APPROVED,
            approved_by=reviewer,
            reviewed_at=timezone.now(),
            code_expires_at=timezone.now() - timedelta(days=1),
        )
        registration_request.set_invite_code('ABC123CODE456')
        registration_request.save()

        registration_request.reopen()
        registration_request.save()
        registration_request.refresh_from_db()

        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_PENDING)
        self.assertEqual(registration_request.invite_code_hash, '')
        self.assertIsNone(registration_request.code_expires_at)
        self.assertIsNone(registration_request.approved_by)
        self.assertIsNone(registration_request.reviewed_at)

    def test_approved_unexpired_unused_correct_code_can_be_used(self):
        raw_invite_code = 'ABC123CODE456'
        registration_request = self.create_request_with_invite_code(
            status=RegistrationRequest.STATUS_APPROVED,
            raw_invite_code=raw_invite_code,
            code_expires_at=timezone.now() + timedelta(days=1),
        )

        self.assertTrue(registration_request.can_use_invite_code(raw_invite_code))

    def test_pending_request_with_matching_code_cannot_be_used(self):
        raw_invite_code = 'ABC123CODE456'
        registration_request = self.create_request_with_invite_code(
            status=RegistrationRequest.STATUS_PENDING,
            raw_invite_code=raw_invite_code,
            code_expires_at=timezone.now() + timedelta(days=1),
        )

        self.assertFalse(registration_request.can_use_invite_code(raw_invite_code))

    def test_approved_expired_request_with_matching_code_cannot_be_used(self):
        raw_invite_code = 'ABC123CODE456'
        registration_request = self.create_request_with_invite_code(
            status=RegistrationRequest.STATUS_APPROVED,
            raw_invite_code=raw_invite_code,
            code_expires_at=timezone.now() - timedelta(days=1),
        )

        self.assertFalse(registration_request.can_use_invite_code(raw_invite_code))

    def test_approved_used_request_with_matching_code_cannot_be_used(self):
        raw_invite_code = 'ABC123CODE456'
        registration_request = self.create_request_with_invite_code(
            status=RegistrationRequest.STATUS_APPROVED,
            raw_invite_code=raw_invite_code,
            code_expires_at=timezone.now() + timedelta(days=1),
            used_at=timezone.now(),
        )

        self.assertFalse(registration_request.can_use_invite_code(raw_invite_code))

    def test_approved_unexpired_unused_wrong_code_cannot_be_used(self):
        registration_request = self.create_request_with_invite_code(
            status=RegistrationRequest.STATUS_APPROVED,
            raw_invite_code='ABC123CODE456',
            code_expires_at=timezone.now() + timedelta(days=1),
        )

        self.assertFalse(registration_request.can_use_invite_code('WRONGCODE789'))


class RegistrationApprovalEmailTests(TestCase):
    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='default@example.com',
    )
    def test_approval_email_uses_superuser_named_site_as_sender(self):
        User.objects.create_superuser(
            username='白车轴草',
            email='owner@example.com',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
        )
        raw_invite_code = 'ABC123CODE456'
        completion_url = 'http://testserver/register/complete/'
        from blog.registration_approval import send_registration_code_email

        send_registration_code_email(
            registration_request,
            raw_invite_code,
            completion_url,
        )

        self.assertEqual(len(mail.outbox), 1)
        approval_email = mail.outbox[0]
        self.assertEqual(approval_email.from_email, 'owner@example.com')
        self.assertEqual(approval_email.to, ['reader@example.com'])
        self.assertIn(raw_invite_code, approval_email.body)
        self.assertIn(completion_url, approval_email.body)

    def test_generated_registration_code_has_expected_shape(self):
        from blog.registration_approval import generate_registration_code

        raw_invite_code = generate_registration_code()

        self.assertEqual(len(raw_invite_code), 12)
        self.assertTrue(raw_invite_code.isalnum())
        self.assertEqual(raw_invite_code, raw_invite_code.upper())

    def test_approval_email_failure_rolls_back_registration_request(self):
        reviewer = User.objects.create_superuser(
            username='reviewer',
            email='reviewer@example.com',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
        )
        completion_url = 'http://testserver/register/complete/'
        from blog.registration_approval import approve_registration_request

        with patch(
            'blog.registration_approval.send_mail',
            side_effect=RuntimeError('smtp failed'),
        ):
            with self.assertRaises(RuntimeError):
                approve_registration_request(
                    registration_request,
                    reviewer,
                    completion_url,
                )

        registration_request.refresh_from_db()
        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_PENDING)
        self.assertEqual(registration_request.invite_code_hash, '')
        self.assertIsNone(registration_request.code_expires_at)


class AuthViewsTests(TestCase):
    def make_approved_registration_request(
        self,
        email='reader@example.com',
        raw_invite_code='ABC123CODE456',
    ):
        reviewer = User.objects.create_superuser(
            username='reviewer',
            email='reviewer@example.com',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
            email=email,
            status=RegistrationRequest.STATUS_APPROVED,
            approved_by=reviewer,
            code_expires_at=timezone.now() + timedelta(days=7),
        )
        registration_request.set_invite_code(raw_invite_code)
        registration_request.save()
        return registration_request

    def test_register_requires_email(self):
        response = self.client.post(reverse('register'), {
            'email': '',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(RegistrationRequest.objects.exists())
        self.assertContains(response, '请输入邮箱。')

    def test_register_creates_request_without_creating_user(self):
        response = self.client.post(reverse('register'), {
            'email': 'NewReader@Example.COM',
        }, follow=True)

        self.assertRedirects(response, reverse('register'))
        registration_request = RegistrationRequest.objects.get()
        self.assertEqual(registration_request.email, 'newreader@example.com')
        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_PENDING)
        self.assertFalse(User.objects.filter(email__iexact='newreader@example.com').exists())
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertContains(response, '注册申请已提交，请等待审核。')

    def test_register_rejects_duplicate_registered_email(self):
        User.objects.create_user(
            username='existing',
            email='used@example.com',
            password='StrongPass12345',
        )

        response = self.client.post(reverse('register'), {
            'email': 'used@example.com',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(RegistrationRequest.objects.exists())
        self.assertContains(response, '这个邮箱已经被注册。')

    def test_register_does_not_duplicate_pending_request(self):
        RegistrationRequest.objects.create(email='reader@example.com')

        response = self.client.post(reverse('register'), {
            'email': 'reader@example.com',
        }, follow=True)

        self.assertRedirects(response, reverse('register'))
        self.assertEqual(RegistrationRequest.objects.count(), 1)
        self.assertContains(response, '这个邮箱的注册申请正在等待审核。')

    def test_register_reopens_expired_approved_request(self):
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
            status=RegistrationRequest.STATUS_APPROVED,
            code_expires_at=timezone.now() - timedelta(days=1),
        )
        registration_request.set_invite_code('ABC123XYZ789')
        registration_request.save()

        response = self.client.post(reverse('register'), {
            'email': 'reader@example.com',
        }, follow=True)

        self.assertRedirects(response, reverse('register'))
        registration_request.refresh_from_db()
        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_PENDING)
        self.assertEqual(registration_request.invite_code_hash, '')
        self.assertIsNone(registration_request.code_expires_at)
        self.assertContains(response, '注册申请已重新提交，请等待审核。')

    def test_register_redirects_unexpired_approved_request_to_complete_registration(self):
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
            status=RegistrationRequest.STATUS_APPROVED,
            code_expires_at=timezone.now() + timedelta(days=7),
        )
        registration_request.set_invite_code('ABC123CODE456')
        registration_request.save()

        response = self.client.post(reverse('register'), {
            'email': 'reader@example.com',
        }, follow=True)

        self.assertRedirects(response, reverse('complete_registration'))
        self.assertEqual(RegistrationRequest.objects.count(), 1)
        registration_request.refresh_from_db()
        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_APPROVED)
        self.assertNotEqual(registration_request.invite_code_hash, '')
        self.assertContains(response, '这个邮箱已经通过审核，请查看邮件里的注册码。')

    def test_register_renders_complete_registration_link(self):
        response = self.client.get(reverse('register'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, reverse('complete_registration'))

    def test_complete_registration_with_valid_code_creates_and_logs_in_user(self):
        registration_request = self.make_approved_registration_request()

        response = self.client.post(reverse('complete_registration'), {
            'email': 'reader@example.com',
            'invite_code': 'ABC123CODE456',
            'username': 'newreader',
            'nickname': '小草',
            'password1': 'StrongPass12345',
            'password2': 'StrongPass12345',
        })

        self.assertRedirects(response, reverse('index'))
        created_user = User.objects.get(username='newreader')
        self.assertEqual(created_user.email, 'reader@example.com')
        user_profile = UserProfile.objects.get(user=created_user)
        self.assertEqual(user_profile.nickname, '小草')
        self.assertEqual(str(self.client.session['_auth_user_id']), str(created_user.id))
        registration_request.refresh_from_db()
        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_USED)
        self.assertIsNotNone(registration_request.used_at)

    def test_complete_registration_rejects_wrong_code(self):
        self.make_approved_registration_request()

        response = self.client.post(reverse('complete_registration'), {
            'email': 'reader@example.com',
            'invite_code': 'WRONGCODE789',
            'username': 'newreader',
            'nickname': '小草',
            'password1': 'StrongPass12345',
            'password2': 'StrongPass12345',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newreader').exists())
        self.assertContains(response, '注册码不正确。')

    def test_complete_registration_rejects_expired_code(self):
        registration_request = self.make_approved_registration_request()
        registration_request.code_expires_at = timezone.now() - timedelta(days=1)
        registration_request.save(update_fields=['code_expires_at', 'updated_at'])

        response = self.client.post(reverse('complete_registration'), {
            'email': 'reader@example.com',
            'invite_code': 'ABC123CODE456',
            'username': 'newreader',
            'nickname': '小草',
            'password1': 'StrongPass12345',
            'password2': 'StrongPass12345',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newreader').exists())
        self.assertContains(response, '注册码已经过期。')

    def test_complete_registration_rejects_used_request(self):
        registration_request = self.make_approved_registration_request()
        registration_request.status = RegistrationRequest.STATUS_USED
        registration_request.used_at = timezone.now()
        registration_request.save(update_fields=['status', 'used_at', 'updated_at'])

        response = self.client.post(reverse('complete_registration'), {
            'email': 'reader@example.com',
            'invite_code': 'ABC123CODE456',
            'username': 'newreader',
            'nickname': '小草',
            'password1': 'StrongPass12345',
            'password2': 'StrongPass12345',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newreader').exists())
        self.assertContains(response, '这个注册码不能使用。')

    def test_complete_registration_rejects_rejected_request(self):
        registration_request = self.make_approved_registration_request()
        registration_request.status = RegistrationRequest.STATUS_REJECTED
        registration_request.save(update_fields=['status', 'updated_at'])

        response = self.client.post(reverse('complete_registration'), {
            'email': 'reader@example.com',
            'invite_code': 'ABC123CODE456',
            'username': 'newreader',
            'nickname': '小草',
            'password1': 'StrongPass12345',
            'password2': 'StrongPass12345',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newreader').exists())
        self.assertContains(response, '这个注册码不能使用。')

    def test_complete_registration_rejects_duplicate_username(self):
        self.make_approved_registration_request()
        User.objects.create_user(
            username='newreader',
            email='existing@example.com',
            password='StrongPass12345',
        )

        response = self.client.post(reverse('complete_registration'), {
            'email': 'reader@example.com',
            'invite_code': 'ABC123CODE456',
            'username': 'newreader',
            'nickname': '小草',
            'password1': 'StrongPass12345',
            'password2': 'StrongPass12345',
        })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.filter(username='newreader').count(), 1)
        self.assertContains(response, '这个用户名已经被注册。')

    def test_complete_registration_save_rechecks_stale_used_request_before_creating_user(self):
        registration_request = self.make_approved_registration_request()
        form = CompleteRegistrationForm(data={
            'email': 'reader@example.com',
            'invite_code': 'ABC123CODE456',
            'username': 'newreader',
            'nickname': '小草',
            'password1': 'StrongPass12345',
            'password2': 'StrongPass12345',
        })
        self.assertTrue(form.is_valid(), form.errors)
        RegistrationRequest.objects.filter(pk=registration_request.pk).update(
            status=RegistrationRequest.STATUS_USED,
            used_at=timezone.now(),
        )

        with self.assertRaisesMessage(forms.ValidationError, '这个注册码不能使用。'):
            form.save()

        self.assertFalse(User.objects.filter(username='newreader').exists())

    def test_registration_requests_requires_superuser(self):
        User.objects.create_user(
            username='reader',
            password='StrongPass12345',
        )
        self.client.login(username='reader', password='StrongPass12345')

        response = self.client.get(reverse('registration_requests'))

        self.assertEqual(response.status_code, 403)

    def test_superuser_can_view_registration_requests(self):
        User.objects.create_superuser(
            username='reviewer',
            email='reviewer@example.com',
            password='StrongPass12345',
        )
        RegistrationRequest.objects.create(email='reader@example.com')
        self.client.login(username='reviewer', password='StrongPass12345')

        response = self.client.get(reverse('registration_requests'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'reader@example.com')
        self.assertContains(response, '注册审核')

    def test_approve_registration_request_rejects_get(self):
        User.objects.create_superuser(
            username='reviewer',
            email='reviewer@example.com',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
        )
        self.client.login(username='reviewer', password='StrongPass12345')

        response = self.client.get(reverse(
            'approve_registration_request',
            args=[registration_request.id],
        ))

        self.assertEqual(response.status_code, 405)

    @override_settings(
        EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
        DEFAULT_FROM_EMAIL='default@example.com',
    )
    def test_superuser_can_approve_registration_request_and_send_email(self):
        reviewer = User.objects.create_superuser(
            username='白车轴草',
            email='owner@example.com',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
        )
        self.client.login(username='白车轴草', password='StrongPass12345')

        response = self.client.post(reverse(
            'approve_registration_request',
            args=[registration_request.id],
        ), follow=True)

        self.assertRedirects(response, reverse('registration_requests'))
        registration_request.refresh_from_db()
        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_APPROVED)
        self.assertEqual(registration_request.approved_by, reviewer)
        self.assertTrue(registration_request.invite_code_hash)
        self.assertEqual(len(mail.outbox), 1)
        approval_email = mail.outbox[0]
        self.assertEqual(approval_email.to, ['reader@example.com'])
        self.assertNotIn(registration_request.invite_code_hash, approval_email.body)
        self.assertContains(response, '已通过并发送注册码。')

    def test_email_failure_keeps_registration_request_pending(self):
        User.objects.create_superuser(
            username='reviewer',
            email='reviewer@example.com',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
        )
        self.client.login(username='reviewer', password='StrongPass12345')

        with patch(
            'blog.views.approve_registration_request_service',
            side_effect=RuntimeError('smtp failed'),
        ):
            response = self.client.post(reverse(
                'approve_registration_request',
                args=[registration_request.id],
            ), follow=True)

        registration_request.refresh_from_db()
        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_PENDING)
        self.assertEqual(registration_request.invite_code_hash, '')
        self.assertContains(response, '邮件发送失败，申请仍保持待审核。')

    def test_already_reviewed_approval_shows_pending_only_message(self):
        User.objects.create_superuser(
            username='reviewer',
            email='reviewer@example.com',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
        )
        self.client.login(username='reviewer', password='StrongPass12345')
        from blog.registration_approval import RegistrationRequestAlreadyReviewed

        with patch(
            'blog.views.approve_registration_request_service',
            side_effect=RegistrationRequestAlreadyReviewed,
        ):
            response = self.client.post(reverse(
                'approve_registration_request',
                args=[registration_request.id],
            ), follow=True)

        self.assertContains(response, '只有待审核申请可以通过。')

    def test_reject_registration_request_rejects_get(self):
        User.objects.create_superuser(
            username='reviewer',
            email='reviewer@example.com',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
        )
        self.client.login(username='reviewer', password='StrongPass12345')

        response = self.client.get(reverse(
            'reject_registration_request',
            args=[registration_request.id],
        ))

        self.assertEqual(response.status_code, 405)

    def test_superuser_can_reject_registration_request(self):
        reviewer = User.objects.create_superuser(
            username='reviewer',
            email='reviewer@example.com',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
        )
        self.client.login(username='reviewer', password='StrongPass12345')

        response = self.client.post(reverse(
            'reject_registration_request',
            args=[registration_request.id],
        ), follow=True)

        self.assertRedirects(response, reverse('registration_requests'))
        registration_request.refresh_from_db()
        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_REJECTED)
        self.assertEqual(registration_request.approved_by, reviewer)
        self.assertContains(response, '已拒绝这个注册申请。')

    def test_already_approved_reject_keeps_registration_request_approved(self):
        reviewer = User.objects.create_superuser(
            username='reviewer',
            email='reviewer@example.com',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
            status=RegistrationRequest.STATUS_APPROVED,
            code_expires_at=timezone.now() + timedelta(days=7),
        )
        registration_request.set_invite_code('ABC123CODE456')
        registration_request.save()
        original_invite_code_hash = registration_request.invite_code_hash
        self.client.login(username='reviewer', password='StrongPass12345')

        response = self.client.post(reverse(
            'reject_registration_request',
            args=[registration_request.id],
        ), follow=True)

        self.assertContains(response, '只有待审核申请可以拒绝。')
        registration_request.refresh_from_db()
        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_APPROVED)
        self.assertEqual(registration_request.invite_code_hash, original_invite_code_hash)
        self.assertTrue(registration_request.invite_code_hash)

    def test_login_accepts_existing_user(self):
        User.objects.create_user(username='writer', password='StrongPass12345')

        response = self.client.post(reverse('login'), {
            'username': 'writer',
            'password': 'StrongPass12345',
        })

        self.assertRedirects(response, reverse('index'))
        self.assertEqual(self.client.session['_auth_user_id'], str(User.objects.get(username='writer').id))

    def test_protected_create_post_redirects_to_login(self):
        response = self.client.get(reverse('create_post'))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('create_post')}")

    def test_create_post_belongs_to_current_user(self):
        user = User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')

        response = self.client.post(reverse('create_post'), {
            'title': '我的文章',
            'category': 'life',
            'tags': '生活,记录',
            'content': '只属于当前用户',
            'action': 'publish',
        })

        self.assertRedirects(response, reverse('index'))
        post = Post.objects.get(title='我的文章')
        self.assertEqual(post.author, user)
        self.assertEqual(post.tags, '生活,记录')

    def test_generate_ai_post_requires_login(self):
        response = self.client.post(reverse('generate_ai_post'), {
            'topic': '学习 Django',
            'article_length': 'medium',
        })

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('generate_ai_post')}",
        )

    def test_generate_ai_post_returns_editable_draft_without_saving_post(self):
        user = User.objects.create_user(username='writer', password='StrongPass12345')
        Post.objects.create(
            author=user,
            title='以前的文章',
            category='life',
            content='以前的正文',
            status='published',
        )
        self.client.login(username='writer', password='StrongPass12345')
        generated_article = {
            'title': 'Django 学习记录',
            'category': 'study',
            'tags': ['Django', '学习笔记'],
            'content': '这是 AI 生成后供用户继续修改的正文。',
        }

        with patch.dict(os.environ, {'DEEPSEEK_MODEL': 'test-model'}):
            with patch(
                'blog.views.StartupPostCommand.generate_custom_article',
                return_value=generated_article,
            ) as generate_custom_article:
                response = self.client.post(reverse('generate_ai_post'), {
                    'topic': '学习 Django',
                    'requirements': '语气自然',
                    'article_length': 'medium',
                })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {
            'title': 'Django 学习记录',
            'category': 'study',
            'tags': 'Django,学习笔记',
            'content': '这是 AI 生成后供用户继续修改的正文。',
            'cover': None,
            'cover_warning': '',
        })
        self.assertEqual(Post.objects.filter(author=user).count(), 1)
        generate_custom_article.assert_called_once_with(
            model='test-model',
            topic='学习 Django',
            requirements='语气自然',
            article_length='medium',
            recent_titles=['以前的文章'],
        )

    def test_generate_ai_post_limits_repeated_requests(self):
        user = User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')
        generated_article = {
            'title': '测试标题',
            'category': 'life',
            'tags': ['测试', '生活'],
            'content': '测试正文',
        }

        with patch(
            'blog.views.StartupPostCommand.generate_custom_article',
            return_value=generated_article,
        ):
            first_response = self.client.post(reverse('generate_ai_post'), {
                'topic': '第一次生成',
                'article_length': 'short',
            })
            second_response = self.client.post(reverse('generate_ai_post'), {
                'topic': '立即再次生成',
                'article_length': 'short',
            })

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(second_response.status_code, 429)
        self.assertIn('请等待', second_response.json()['error'])

    def test_generate_ai_post_can_return_signed_pexels_cover(self):
        user = User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')
        generated_article = {
            'title': '雨天阅读',
            'category': 'reading',
            'tags': ['阅读', '雨天'],
            'content': '适合雨天阅读的一篇文章。',
        }
        pexels_photo = {
            'id': 12345,
            'url': 'https://www.pexels.com/photo/books-12345/',
            'photographer': 'Test Photographer',
            'photographer_url': 'https://www.pexels.com/@test',
            'src': {
                'landscape': 'https://images.pexels.com/photos/12345/books.jpg',
            },
        }

        with patch.dict(os.environ, {'PEXELS_API_KEY': 'test-key'}):
            with patch(
                'blog.views.StartupPostCommand.generate_custom_article',
                return_value=generated_article,
            ):
                with patch(
                    'blog.views.StartupPostCommand.search_pexels_photo',
                    return_value=pexels_photo,
                ):
                    response = self.client.post(reverse('generate_ai_post'), {
                        'topic': '雨天阅读',
                        'article_length': 'short',
                        'generate_cover': 'true',
                    })

        self.assertEqual(response.status_code, 200)
        response_cover = response.json()['cover']
        self.assertEqual(
            response_cover['preview_url'],
            'https://images.pexels.com/photos/12345/books.jpg',
        )
        signed_cover_data = signing.loads(
            response_cover['token'],
            salt=AI_COVER_TOKEN_SALT,
        )
        self.assertEqual(signed_cover_data['photo_id'], 12345)
        self.assertEqual(signed_cover_data['photographer'], 'Test Photographer')

    def test_create_post_downloads_signed_ai_cover(self):
        user = User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')
        cover_data = {
            'image_url': 'https://images.pexels.com/photos/12345/books.jpg',
            'photo_id': 12345,
            'photo_url': 'https://www.pexels.com/photo/books-12345/',
            'photographer': 'Test Photographer',
            'photographer_url': 'https://www.pexels.com/@test',
        }
        ai_cover_token = signing.dumps(cover_data, salt=AI_COVER_TOKEN_SALT)

        with tempfile.TemporaryDirectory() as temporary_media_root:
            with self.settings(MEDIA_ROOT=temporary_media_root):
                with patch(
                    'blog.views.StartupPostCommand.download_pexels_image',
                    return_value=b'test-image-bytes',
                ) as download_pexels_image:
                    response = self.client.post(reverse('create_post'), {
                        'title': '带 AI 封面的文章',
                        'category': 'reading',
                        'tags': '阅读,雨天',
                        'content': '文章正文',
                        'visibility': 'private',
                        'action': 'draft',
                        'ai_cover_token': ai_cover_token,
                    })

        self.assertRedirects(response, reverse('drafts'))
        post = Post.objects.get(title='带 AI 封面的文章')
        self.assertTrue(post.cover.name.startswith('covers/ai_'))
        self.assertIn('Photo by Test Photographer on Pexels', post.content)
        download_pexels_image.assert_called_once_with(cover_data['image_url'])

    def test_index_only_shows_current_users_posts(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        other = User.objects.create_user(username='other', password='StrongPass12345')
        Post.objects.create(author=owner, title='自己的文章', category='life', content='可见', status='published')
        Post.objects.create(author=other, title='别人的文章', category='life', content='不可见', status='published')
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('index'))

        self.assertContains(response, '自己的文章')
        self.assertNotContains(response, '别人的文章')

    def test_index_my_posts_filter_only_shows_current_users_published_posts(self):
        current_user = User.objects.create_user(
            username='current',
            password='StrongPass12345',
        )
        other_user = User.objects.create_user(
            username='other',
            password='StrongPass12345',
        )
        Post.objects.create(
            author=current_user,
            title='我的公开文章',
            category='life',
            content='公开正文',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=current_user,
            title='我的私密文章',
            category='study',
            content='私密正文',
            status='published',
            visibility='private',
        )
        Post.objects.create(
            author=other_user,
            title='其他用户公开文章',
            category='tech',
            content='其他正文',
            status='published',
            visibility='public',
        )
        self.client.login(username='current', password='StrongPass12345')

        response = self.client.get(reverse('index'), {'author': 'current'})

        result_titles = [
            post.title
            for post in response.context['posts'].object_list
        ]
        self.assertCountEqual(result_titles, ['我的公开文章', '我的私密文章'])
        self.assertTrue(response.context['is_my_posts_filter'])
        self.assertEqual(response.context['selected_author_label'], 'current')
        self.assertContains(response, '正在看我的文章')
        self.assertNotContains(response, '其他用户公开文章')

    def test_index_author_filter_only_shows_selected_authors_visible_posts(self):
        current_user = User.objects.create_user(
            username='current',
            password='StrongPass12345',
        )
        other_user = User.objects.create_user(
            username='other',
            password='StrongPass12345',
        )
        UserProfile.objects.create(user=other_user, nickname='其他作者')
        Post.objects.create(
            author=current_user,
            title='当前用户文章',
            category='life',
            content='当前正文',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=other_user,
            title='其他作者公开文章',
            category='tech',
            content='公开正文',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=other_user,
            title='其他作者私密文章',
            category='study',
            content='私密正文',
            status='published',
            visibility='private',
        )
        self.client.login(username='current', password='StrongPass12345')

        response = self.client.get(reverse('index'), {'author': 'other'})

        result_titles = [
            post.title
            for post in response.context['posts'].object_list
        ]
        self.assertEqual(result_titles, ['其他作者公开文章'])
        self.assertFalse(response.context['is_my_posts_filter'])
        self.assertEqual(response.context['selected_author_label'], '其他作者')
        self.assertContains(response, '正在筛选作者')
        self.assertNotContains(response, '其他作者私密文章')

    def test_index_card_metadata_keeps_date_as_plain_text(self):
        author = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=author,
            title='可以筛选的文章',
            category='mood',
            content='正文',
            status='published',
            visibility='public',
        )
        Post.objects.filter(pk=post.pk).update(
            created_at=timezone.make_aware(datetime(2026, 6, 24, 12, 0)),
        )

        response = self.client.get(reverse('index'))

        self.assertContains(response, 'author=writer')
        self.assertContains(response, '2026-06-24')
        self.assertNotContains(response, 'date=2026-06-24')
        self.assertContains(response, 'category=mood')
        self.assertContains(response, 'post-meta-link')
        self.assertContains(response, 'post-category')

    def test_index_cards_show_limited_readable_tag_links(self):
        author = User.objects.create_user(
            username='card-tag-author',
            password='StrongPass12345',
        )
        Post.objects.create(
            author=author,
            title='首页标签文章',
            category='life',
            tags='Django, 生活技巧, 整理, 第四个标签, daily:2026-06-27',
            content='首页标签正文',
            status='published',
            visibility='public',
        )

        response = self.client.get(reverse('index'))

        post = response.context['posts'].object_list[0]
        self.assertEqual(post.card_display_tags, ['Django', '生活技巧', '整理'])
        self.assertContains(response, '# Django')
        self.assertContains(response, '# 生活技巧')
        self.assertContains(response, '# 整理')
        self.assertContains(response, 'href="/index/?tag=Django"')
        self.assertNotContains(response, '第四个标签')
        self.assertNotContains(response, 'daily:2026-06-27')

    def test_index_tag_filter_matches_exact_tags(self):
        author = User.objects.create_user(
            username='exact-tag-author',
            password='StrongPass12345',
        )
        Post.objects.create(
            author=author,
            title='精确标签文章',
            category='life',
            tags='生活,Django',
            content='真正带有生活标签',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=author,
            title='正文提到生活但无标签',
            category='life',
            tags='随笔',
            content='正文里面提到了生活两个字',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=author,
            title='相似标签文章',
            category='life',
            tags='生活方式',
            content='标签相似但不是同一个标签',
            status='published',
            visibility='public',
        )

        response = self.client.get(reverse('index'), {'tag': '生活'})

        result_titles = [
            post.title
            for post in response.context['posts'].object_list
        ]
        self.assertEqual(result_titles, ['精确标签文章'])
        self.assertEqual(response.context['selected_tag'], '生活')
        self.assertContains(response, '正在筛选标签')
        self.assertContains(response, '清除标签')

    def test_index_exposes_unified_active_filter_chips(self):
        author = User.objects.create_user(
            username='chip-author',
            password='StrongPass12345',
        )
        UserProfile.objects.create(user=author, nickname='筛选作者')
        Post.objects.create(
            author=author,
            title='Django 标签文章',
            category='tech',
            tags='Django,筛选',
            content='筛选正文',
            status='published',
            visibility='public',
        )

        response = self.client.get(reverse('index'), {
            'q': 'Django',
            'category': 'tech',
            'tag': '筛选',
            'author': 'chip-author',
        })

        active_filter_chips = response.context['active_filter_chips']
        self.assertEqual(
            [active_filter_chip['label'] for active_filter_chip in active_filter_chips],
            ['搜索', '分类', '标签', '作者'],
        )
        self.assertEqual(
            [active_filter_chip['value'] for active_filter_chip in active_filter_chips],
            ['Django', '技术', '筛选', '筛选作者'],
        )
        self.assertContains(response, 'active-filter-chip')
        self.assertContains(response, '清除筛选')

    def test_archive_page_groups_readable_posts_by_month(self):
        author = User.objects.create_user(
            username='archive-author',
            password='StrongPass12345',
        )
        june_post = Post.objects.create(
            author=author,
            title='六月公开文章',
            category='life',
            content='六月正文',
            status='published',
            visibility='public',
        )
        may_post = Post.objects.create(
            author=author,
            title='五月公开文章',
            category='study',
            content='五月正文',
            status='published',
            visibility='public',
        )
        draft_post = Post.objects.create(
            author=author,
            title='草稿不进归档',
            category='tech',
            content='草稿正文',
            status='draft',
            visibility='private',
        )
        Post.objects.filter(pk=june_post.pk).update(
            created_at=timezone.make_aware(datetime(2026, 6, 27, 9, 0)),
        )
        Post.objects.filter(pk=may_post.pk).update(
            created_at=timezone.make_aware(datetime(2026, 5, 20, 9, 0)),
        )
        Post.objects.filter(pk=draft_post.pk).update(
            created_at=timezone.make_aware(datetime(2026, 6, 28, 9, 0)),
        )

        response = self.client.get(reverse('archive'))

        self.assertEqual(response.status_code, 200)
        archive_groups = response.context['archive_groups']
        self.assertEqual(len(archive_groups), 2)
        self.assertEqual(archive_groups[0]['year'], 2026)
        self.assertEqual(archive_groups[0]['month'], 6)
        self.assertEqual(archive_groups[0]['posts'][0].title, '六月公开文章')
        self.assertEqual(archive_groups[1]['month'], 5)
        self.assertContains(response, '六月公开文章')
        self.assertContains(response, '五月公开文章')
        self.assertNotContains(response, '草稿不进归档')

    def test_archive_page_includes_current_users_private_published_posts(self):
        current_user = User.objects.create_user(
            username='archive-current',
            password='StrongPass12345',
        )
        other_user = User.objects.create_user(
            username='archive-other',
            password='StrongPass12345',
        )
        own_private_post = Post.objects.create(
            author=current_user,
            title='自己的私密已发布文章',
            category='life',
            content='自己可见',
            status='published',
            visibility='private',
        )
        other_private_post = Post.objects.create(
            author=other_user,
            title='别人的私密已发布文章',
            category='life',
            content='别人私密',
            status='published',
            visibility='private',
        )
        Post.objects.filter(pk=own_private_post.pk).update(
            created_at=timezone.make_aware(datetime(2026, 6, 25, 9, 0)),
        )
        Post.objects.filter(pk=other_private_post.pk).update(
            created_at=timezone.make_aware(datetime(2026, 6, 24, 9, 0)),
        )
        self.client.login(username='archive-current', password='StrongPass12345')

        response = self.client.get(reverse('archive'))

        archive_titles = [
            post.title
            for archive_group in response.context['archive_groups']
            for post in archive_group['posts']
        ]
        self.assertIn('自己的私密已发布文章', archive_titles)
        self.assertNotIn('别人的私密已发布文章', archive_titles)
        self.assertContains(response, '自己的私密已发布文章')
        self.assertNotContains(response, '别人的私密已发布文章')

    def test_tags_page_counts_visible_tags_once_per_post(self):
        author = User.objects.create_user(
            username='tag-author',
            password='StrongPass12345',
        )
        Post.objects.create(
            author=author,
            title='标签文章一',
            category='life',
            tags='生活, Django, 生活,, daily:2026-06-27',
            content='标签正文一',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=author,
            title='标签文章二',
            category='study',
            tags='Django, 学习',
            content='标签正文二',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=author,
            title='草稿标签不统计',
            category='tech',
            tags='隐藏',
            content='草稿正文',
            status='draft',
            visibility='private',
        )

        response = self.client.get(reverse('tags'))

        self.assertEqual(response.status_code, 200)
        tag_counts = response.context['tag_counts']
        self.assertEqual(tag_counts[0], {'name': 'Django', 'count': 2})
        self.assertIn({'name': '生活', 'count': 1}, tag_counts)
        self.assertIn({'name': '学习', 'count': 1}, tag_counts)
        self.assertNotIn({'name': '隐藏', 'count': 1}, tag_counts)
        self.assertNotIn({'name': 'daily:2026-06-27', 'count': 1}, tag_counts)
        self.assertContains(response, 'href="/index/?tag=Django"')
        self.assertContains(response, '2 篇')
        self.assertNotContains(response, 'daily:2026-06-27')

    def test_tags_page_can_search_sort_and_highlight_tags(self):
        author = User.objects.create_user(
            username='tag-search-author',
            password='StrongPass12345',
        )
        Post.objects.create(
            author=author,
            title='Django 标签文章',
            category='tech',
            tags='Django,Python',
            content='标签正文一',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=author,
            title='Python 标签文章',
            category='study',
            tags='Python',
            content='标签正文二',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=author,
            title='生活标签文章',
            category='life',
            tags='生活',
            content='标签正文三',
            status='published',
            visibility='public',
        )

        response = self.client.get(reverse('tags'), {
            'q': 'py',
            'sort': 'name',
            'selected': 'Python',
        })

        self.assertEqual(response.context['tag_search_query'], 'py')
        self.assertEqual(response.context['tag_sort'], 'name')
        self.assertEqual(response.context['selected_tag'], 'Python')
        self.assertEqual(response.context['tag_counts'], [{'name': 'Python', 'count': 2}])
        self.assertContains(response, 'value="py"')
        self.assertContains(response, 'tag-pill active')
        self.assertContains(response, 'href="/index/?tag=Python"')
        self.assertNotContains(response, '# 生活')

    def test_base_navigation_links_to_archive_and_tags_pages(self):
        response = self.client.get(reverse('index'))

        self.assertContains(response, f'href="{reverse("archive")}"')
        self.assertContains(response, f'href="{reverse("tags")}"')
        self.assertContains(response, '归档')
        self.assertContains(response, '标签')

    def test_index_about_card_uses_current_user_profile_and_post_stats(self):
        owner = User.objects.create_superuser(username='root', password='StrongPass12345')
        current_user = User.objects.create_user(username='current', password='StrongPass12345')
        UserProfile.objects.create(user=owner, nickname='站点博主', bio='记录公开文章。')
        current_profile = UserProfile.objects.create(
            user=current_user,
            nickname='当前用户',
            bio='这是当前登录用户。',
        )
        Post.objects.create(
            author=owner,
            title='站点博主文章',
            category='life',
            content='公开可见',
            status='published',
            visibility='public',
            views_count=20,
        )
        Post.objects.create(
            author=current_user,
            title='当前用户文章',
            category='tech',
            content='自己可见',
            status='published',
            views_count=7,
        )
        self.client.login(username='current', password='StrongPass12345')

        response = self.client.get(reverse('index'))

        self.assertEqual(response.context['profile'], current_profile)
        self.assertContains(response, '当前用户')
        self.assertContains(response, '这是当前登录用户。')
        self.assertEqual(response.context['published_count'], 1)
        self.assertEqual(response.context['total_views'], 7)

    def test_index_search_filters_current_users_published_posts(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        other = User.objects.create_user(username='other', password='StrongPass12345')
        Post.objects.create(author=owner, title='雨后散步', category='life', content='今天适合散步', status='published')
        Post.objects.create(author=owner, title='Django 记录', category='tech', content='视图和模板', status='published')
        Post.objects.create(author=owner, title='雨声草稿', category='life', content='不可见', status='draft')
        Post.objects.create(author=other, title='雨后别人的文章', category='life', content='不可见', status='published')
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('index'), {'q': '雨后'})

        result_titles = [post.title for post in response.context['posts'].object_list]
        self.assertEqual(result_titles, ['雨后散步'])
        self.assertEqual(response.context['search_query'], '雨后')

    def test_index_search_matches_content_and_category_label(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        Post.objects.create(author=owner, title='普通标题', category='tech', content='包含松饼这个关键词', status='published')
        Post.objects.create(author=owner, title='分类命中文章', category='life', content='没有直接关键词', status='published')
        Post.objects.create(author=owner, title='不相关文章', category='reading', content='别的内容', status='published')
        self.client.login(username='owner', password='StrongPass12345')

        content_response = self.client.get(reverse('index'), {'q': '松饼'})
        category_response = self.client.get(reverse('index'), {'q': '生活'})

        content_titles = [post.title for post in content_response.context['posts'].object_list]
        category_titles = [post.title for post in category_response.context['posts'].object_list]
        self.assertEqual(content_titles, ['普通标题'])
        self.assertEqual(category_titles, ['分类命中文章'])

    def test_index_search_can_combine_with_category_and_pagination(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        for index in range(7):
            Post.objects.create(
                author=owner,
                title=f'Django 生活 {index}',
                category='life',
                content='搜索分页',
                status='published',
            )
        Post.objects.create(author=owner, title='Django 技术', category='tech', content='搜索分页', status='published')
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('index'), {'q': 'Django', 'category': 'life'})

        result_titles = [post.title for post in response.context['posts'].object_list]
        self.assertEqual(len(result_titles), 6)
        self.assertTrue(all(title.startswith('Django 生活') for title in result_titles))
        self.assertContains(response, '?q=Django&amp;category=life&amp;page=2')
        self.assertContains(response, 'value="Django"')

    def test_index_shows_filtered_result_count(self):
        owner = User.objects.create_user(username='result-count-owner', password='StrongPass12345')
        Post.objects.create(author=owner, title='Django 入门', category='tech', content='筛选', status='published', visibility='public')
        Post.objects.create(author=owner, title='Django 模板', category='tech', content='筛选', status='published', visibility='public')
        Post.objects.create(author=owner, title='生活记录', category='life', content='筛选', status='published', visibility='public')

        response = self.client.get(reverse('index'), {'q': 'Django'})

        self.assertEqual(response.context['result_count'], 2)
        self.assertContains(response, '共找到 2 篇文章')

    def test_index_ignores_date_query_parameter(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        target_post = Post.objects.create(author=owner, title='六月文章', category='life', content='夏天', status='published')
        other_post = Post.objects.create(author=owner, title='五月文章', category='life', content='春天', status='published')
        Post.objects.filter(pk=target_post.pk).update(created_at=timezone.make_aware(datetime(2026, 6, 13, 12, 0)))
        Post.objects.filter(pk=other_post.pk).update(created_at=timezone.make_aware(datetime(2026, 5, 20, 12, 0)))
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('index'), {'date': '2026-06-13'})

        result_titles = [post.title for post in response.context['posts'].object_list]
        self.assertEqual(result_titles, ['六月文章', '五月文章'])
        self.assertNotIn('selected_date', response.context)
        self.assertNotContains(response, 'type="date"')
        self.assertNotContains(response, '正在筛选日期')

    def test_index_search_ignores_date_when_combined_with_keyword_and_category(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        matched = Post.objects.create(author=owner, title='Django 日期', category='tech', content='筛选', status='published')
        wrong_date = Post.objects.create(author=owner, title='Django 旧文', category='tech', content='筛选', status='published')
        wrong_category = Post.objects.create(author=owner, title='Django 生活', category='life', content='筛选', status='published')
        Post.objects.filter(pk=matched.pk).update(created_at=timezone.make_aware(datetime(2026, 6, 13, 12, 0)))
        Post.objects.filter(pk=wrong_date.pk).update(created_at=timezone.make_aware(datetime(2026, 6, 12, 12, 0)))
        Post.objects.filter(pk=wrong_category.pk).update(created_at=timezone.make_aware(datetime(2026, 6, 13, 13, 0)))
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('index'), {'q': 'Django', 'date': '2026-06-13', 'category': 'tech'})

        result_titles = [post.title for post in response.context['posts'].object_list]
        self.assertEqual(result_titles, ['Django 日期', 'Django 旧文'])
        self.assertEqual(response.context['pagination_prefix'], 'q=Django&category=tech&')
        self.assertNotContains(response, 'value="2026-06-13"')

    def test_detail_requires_post_owner(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        other = User.objects.create_user(username='other', password='StrongPass12345')
        post = Post.objects.create(author=other, title='别人的文章', category='life', content='不可见', status='published')
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('post_detail', args=[post.id]))

        self.assertEqual(response.status_code, 404)

    def test_post_detail_displays_readable_tags_with_search_links(self):
        author = User.objects.create_user(
            username='tag-detail-author',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=author,
            title='带标签的文章',
            category='life',
            tags='Django, 生活技巧, daily:2026-06-27',
            content='文章正文',
            status='published',
            visibility='public',
        )

        response = self.client.get(reverse('post_detail', args=[post.id]))

        self.assertEqual(response.context['display_tags'], ['Django', '生活技巧'])
        self.assertContains(response, '# Django')
        self.assertContains(response, '# 生活技巧')
        self.assertContains(response, 'href="/index/?tag=Django"')
        self.assertNotContains(response, 'daily:2026-06-27')

    def test_post_detail_formats_simple_markdown_bold_without_allowing_html(self):
        author = User.objects.create_user(
            username='format-author',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=author,
            title='正文格式文章',
            category='life',
            content='**重点段落**\n<script>alert("bad")</script>',
            status='published',
            visibility='public',
        )

        response = self.client.get(reverse('post_detail', args=[post.id]))

        self.assertContains(response, '<strong>重点段落</strong>', html=True)
        self.assertNotContains(response, '**重点段落**')
        self.assertContains(response, '&lt;script&gt;alert(&quot;bad&quot;)&lt;/script&gt;')

    def test_post_detail_shows_related_posts_with_shared_exact_tags(self):
        author = User.objects.create_user(
            username='related-author',
            password='StrongPass12345',
        )
        current_post = Post.objects.create(
            author=author,
            title='当前文章',
            category='life',
            tags='生活,Django',
            content='当前正文',
            status='published',
            visibility='public',
        )
        related_post = Post.objects.create(
            author=author,
            title='相关精确标签文章',
            category='tech',
            tags='Django,学习',
            content='相关正文',
            status='published',
            visibility='public',
        )
        similar_tag_post = Post.objects.create(
            author=author,
            title='相似标签文章',
            category='life',
            tags='Django学习',
            content='相似正文',
            status='published',
            visibility='public',
        )
        private_post = Post.objects.create(
            author=author,
            title='不可见相关文章',
            category='life',
            tags='Django',
            content='私密正文',
            status='published',
            visibility='private',
        )

        response = self.client.get(reverse('post_detail', args=[current_post.id]))

        related_titles = [post.title for post in response.context['related_posts']]
        self.assertEqual(related_titles, [related_post.title])
        self.assertContains(response, '相关文章')
        self.assertContains(response, related_post.title)
        self.assertNotContains(response, similar_tag_post.title)
        self.assertNotContains(response, private_post.title)

    def test_logged_in_user_can_comment_on_public_post(self):
        user = User.objects.create_user(
            username='commenter',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=user,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        self.client.login(
            username='commenter',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('add_comment', args=[post.id]),
            {'content': '这是一条测试评论。'},
        )

        self.assertRedirects(
            response,
            reverse('post_detail', args=[post.id]),
        )
        comment = Comment.objects.get(post=post)
        self.assertEqual(comment.author, user)
        self.assertEqual(comment.content, '这是一条测试评论。')


    def test_anonymous_user_cannot_comment(self):
        author = User.objects.create_user(
            username='author',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=author,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )

        response = self.client.post(
            reverse('add_comment', args=[post.id]),
            {'content': '游客评论。'},
        )

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('add_comment', args=[post.id])}",
        )
        self.assertFalse(Comment.objects.filter(post=post).exists())


    def test_private_post_cannot_be_commented(self):
        user = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=user,
            title='私密文章',
            category='life',
            content='私密正文',
            status='published',
            visibility='private',
        )
        self.client.login(
            username='writer',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('add_comment', args=[post.id]),
            {'content': '不应该保存的评论。'},
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Comment.objects.filter(post=post).exists())

    def test_logged_in_user_can_reply_to_comment(self):
        post_author = User.objects.create_user(
            username='post-author',
            password='StrongPass12345',
        )
        reply_author = User.objects.create_user(
            username='reply-author',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=post_author,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        parent_comment = Comment.objects.create(
            post=post,
            author=post_author,
            content='主评论。',
        )
        self.client.login(
            username='reply-author',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('add_comment', args=[post.id]),
            {
                'content': '这是一条回复。',
                'parent_id': parent_comment.id,
            },
        )

        self.assertRedirects(
            response,
            reverse('post_detail', args=[post.id]),
        )
        reply = Comment.objects.get(parent=parent_comment)
        self.assertEqual(reply.post, post)
        self.assertEqual(reply.author, reply_author)
        self.assertEqual(reply.content, '这是一条回复。')

    def test_reply_parent_must_belong_to_same_post(self):
        user = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
        )
        first_post = Post.objects.create(
            author=user,
            title='第一篇文章',
            category='life',
            content='第一篇正文',
            status='published',
            visibility='public',
        )
        second_post = Post.objects.create(
            author=user,
            title='第二篇文章',
            category='life',
            content='第二篇正文',
            status='published',
            visibility='public',
        )
        other_post_comment = Comment.objects.create(
            post=second_post,
            author=user,
            content='另一篇文章的评论。',
        )
        self.client.login(
            username='writer',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('add_comment', args=[first_post.id]),
            {
                'content': '伪造的跨文章回复。',
                'parent_id': other_post_comment.id,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Comment.objects.filter(
                post=first_post,
                content='伪造的跨文章回复。',
            ).exists()
        )

    def test_reply_cannot_target_another_reply(self):
        user = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=user,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        parent_comment = Comment.objects.create(
            post=post,
            author=user,
            content='主评论。',
        )
        first_reply = Comment.objects.create(
            post=post,
            author=user,
            parent=parent_comment,
            content='第一层回复。',
        )
        self.client.login(
            username='writer',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('add_comment', args=[post.id]),
            {
                'content': '不允许的第二层回复。',
                'parent_id': first_reply.id,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            Comment.objects.filter(content='不允许的第二层回复。').exists()
        )

    def test_deleting_parent_comment_also_deletes_replies(self):
        user = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=user,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        parent_comment = Comment.objects.create(
            post=post,
            author=user,
            content='主评论。',
        )
        reply = Comment.objects.create(
            post=post,
            author=user,
            parent=parent_comment,
            content='回复。',
        )
        self.client.login(
            username='writer',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('delete_comment', args=[parent_comment.id]),
        )

        self.assertRedirects(
            response,
            reverse('post_detail', args=[post.id]),
        )
        self.assertFalse(Comment.objects.filter(id=parent_comment.id).exists())
        self.assertFalse(Comment.objects.filter(id=reply.id).exists())

    def test_comment_author_can_delete_own_comment(self):
        post_author = User.objects.create_user(
            username='post-author',
            password='StrongPass12345',
        )
        comment_author = User.objects.create_user(
            username='comment-author',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=post_author,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        comment = Comment.objects.create(
            post=post,
            author=comment_author,
            content='由评论者删除。',
        )
        self.client.login(
            username='comment-author',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('delete_comment', args=[comment.id]),
        )

        self.assertRedirects(
            response,
            reverse('post_detail', args=[post.id]),
        )
        self.assertFalse(Comment.objects.filter(id=comment.id).exists())

    def test_post_author_can_delete_comment_on_own_post(self):
        post_author = User.objects.create_user(
            username='post-author',
            password='StrongPass12345',
        )
        comment_author = User.objects.create_user(
            username='comment-author',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=post_author,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        comment = Comment.objects.create(
            post=post,
            author=comment_author,
            content='由文章作者管理。',
        )
        self.client.login(
            username='post-author',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('delete_comment', args=[comment.id]),
        )

        self.assertRedirects(
            response,
            reverse('post_detail', args=[post.id]),
        )
        self.assertFalse(Comment.objects.filter(id=comment.id).exists())

    def test_unrelated_user_cannot_delete_comment(self):
        post_author = User.objects.create_user(
            username='post-author',
            password='StrongPass12345',
        )
        comment_author = User.objects.create_user(
            username='comment-author',
            password='StrongPass12345',
        )
        unrelated_user = User.objects.create_user(
            username='unrelated',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=post_author,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        comment = Comment.objects.create(
            post=post,
            author=comment_author,
            content='不能被无关用户删除。',
        )
        self.client.login(
            username='unrelated',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('delete_comment', args=[comment.id]),
        )

        self.assertRedirects(
            response,
            reverse('post_detail', args=[post.id]),
        )
        self.assertTrue(Comment.objects.filter(id=comment.id).exists())

    def test_delete_comment_rejects_get_request(self):
        user = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
        )
        post = Post.objects.create(
            author=user,
            title='公开文章',
            category='life',
            content='文章正文',
            status='published',
            visibility='public',
        )
        comment = Comment.objects.create(
            post=post,
            author=user,
            content='不能通过 GET 删除。',
        )
        self.client.login(
            username='writer',
            password='StrongPass12345',
        )

        response = self.client.get(
            reverse('delete_comment', args=[comment.id]),
        )

        self.assertEqual(response.status_code, 405)
        self.assertTrue(Comment.objects.filter(id=comment.id).exists())

    def test_user_can_send_friend_request(self):
        sender = User.objects.create_user(
            username='sender',
            password='StrongPass12345',
        )
        receiver = User.objects.create_user(
            username='receiver',
            password='StrongPass12345',
        )
        self.client.login(username='sender', password='StrongPass12345')

        response = self.client.post(
            reverse('send_friend_request', args=[receiver.id]),
        )

        self.assertRedirects(response, reverse('friends'))
        friend_request = FriendRequest.objects.get(
            sender=sender,
            receiver=receiver,
        )
        self.assertEqual(friend_request.status, 'pending')

    def test_user_cannot_send_friend_request_to_self(self):
        user = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
        )
        self.client.login(username='writer', password='StrongPass12345')

        response = self.client.post(
            reverse('send_friend_request', args=[user.id]),
        )

        self.assertRedirects(response, reverse('friends'))
        self.assertFalse(FriendRequest.objects.exists())

    def test_receiver_can_accept_friend_request(self):
        sender = User.objects.create_user(
            username='sender',
            password='StrongPass12345',
        )
        receiver = User.objects.create_user(
            username='receiver',
            password='StrongPass12345',
        )
        friend_request = FriendRequest.objects.create(
            sender=sender,
            receiver=receiver,
        )
        self.client.login(username='receiver', password='StrongPass12345')

        response = self.client.post(
            reverse('accept_friend_request', args=[friend_request.id]),
        )

        self.assertRedirects(response, reverse('friends'))
        friend_request.refresh_from_db()
        self.assertEqual(friend_request.status, 'accepted')
        friendship = Friendship.objects.get()
        self.assertLess(friendship.user_low_id, friendship.user_high_id)
        self.assertCountEqual(
            [friendship.user_low_id, friendship.user_high_id],
            [sender.id, receiver.id],
        )

    def test_non_receiver_cannot_accept_friend_request(self):
        sender = User.objects.create_user(
            username='sender',
            password='StrongPass12345',
        )
        receiver = User.objects.create_user(
            username='receiver',
            password='StrongPass12345',
        )
        unrelated = User.objects.create_user(
            username='unrelated',
            password='StrongPass12345',
        )
        friend_request = FriendRequest.objects.create(
            sender=sender,
            receiver=receiver,
        )
        self.client.login(username='unrelated', password='StrongPass12345')

        response = self.client.post(
            reverse('accept_friend_request', args=[friend_request.id]),
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(Friendship.objects.exists())

    def test_sender_can_cancel_friend_request(self):
        sender = User.objects.create_user(
            username='sender',
            password='StrongPass12345',
        )
        receiver = User.objects.create_user(
            username='receiver',
            password='StrongPass12345',
        )
        friend_request = FriendRequest.objects.create(
            sender=sender,
            receiver=receiver,
        )
        self.client.login(username='sender', password='StrongPass12345')

        response = self.client.post(
            reverse('cancel_friend_request', args=[friend_request.id]),
        )

        self.assertRedirects(response, reverse('friends'))
        friend_request.refresh_from_db()
        self.assertEqual(friend_request.status, 'cancelled')

    def test_receiver_can_reject_friend_request(self):
        sender = User.objects.create_user(
            username='sender',
            password='StrongPass12345',
        )
        receiver = User.objects.create_user(
            username='receiver',
            password='StrongPass12345',
        )
        friend_request = FriendRequest.objects.create(
            sender=sender,
            receiver=receiver,
        )
        self.client.login(username='receiver', password='StrongPass12345')

        response = self.client.post(
            reverse('reject_friend_request', args=[friend_request.id]),
        )

        self.assertRedirects(response, reverse('friends'))
        friend_request.refresh_from_db()
        self.assertEqual(friend_request.status, 'rejected')
        self.assertFalse(Friendship.objects.exists())

    def test_friend_can_be_removed(self):
        first_user = User.objects.create_user(
            username='first',
            password='StrongPass12345',
        )
        second_user = User.objects.create_user(
            username='second',
            password='StrongPass12345',
        )
        Friendship.connect(first_user, second_user)
        self.client.login(username='first', password='StrongPass12345')

        response = self.client.post(
            reverse('remove_friend', args=[second_user.id]),
        )

        self.assertRedirects(response, reverse('friends'))
        self.assertFalse(Friendship.objects.exists())

    def test_non_friends_cannot_open_conversation(self):
        first_user = User.objects.create_user(
            username='first',
            password='StrongPass12345',
        )
        second_user = User.objects.create_user(
            username='second',
            password='StrongPass12345',
        )
        self.client.login(username='first', password='StrongPass12345')

        response = self.client.get(
            reverse('conversation', args=[second_user.id]),
        )

        self.assertRedirects(response, reverse('friends'))
        self.assertFalse(PrivateMessage.objects.exists())

    def test_friends_can_send_private_message(self):
        sender = User.objects.create_user(
            username='sender',
            password='StrongPass12345',
        )
        recipient = User.objects.create_user(
            username='recipient',
            password='StrongPass12345',
        )
        Friendship.connect(sender, recipient)
        self.client.login(username='sender', password='StrongPass12345')

        response = self.client.post(
            reverse('conversation', args=[recipient.id]),
            {'content': '你好，这是一条私信。'},
        )

        self.assertRedirects(
            response,
            reverse('conversation', args=[recipient.id]),
        )
        private_message = PrivateMessage.objects.get()
        self.assertEqual(private_message.sender, sender)
        self.assertEqual(private_message.recipient, recipient)
        self.assertEqual(private_message.content, '你好，这是一条私信。')
        self.assertFalse(private_message.is_read)

    def test_conversation_list_handles_friend_without_messages(self):
        first_user = User.objects.create_user(
            username='first',
            password='StrongPass12345',
        )
        second_user = User.objects.create_user(
            username='second',
            password='StrongPass12345',
        )
        Friendship.connect(first_user, second_user)
        self.client.login(username='first', password='StrongPass12345')

        response = self.client.get(reverse('conversations'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'second')
        self.assertContains(response, '还没有消息')

    def test_opening_conversation_marks_received_messages_read(self):
        sender = User.objects.create_user(
            username='sender',
            password='StrongPass12345',
        )
        recipient = User.objects.create_user(
            username='recipient',
            password='StrongPass12345',
        )
        Friendship.connect(sender, recipient)
        private_message = PrivateMessage.objects.create(
            sender=sender,
            recipient=recipient,
            content='未读消息',
        )
        self.client.login(username='recipient', password='StrongPass12345')

        response = self.client.get(
            reverse('conversation', args=[sender.id]),
        )

        self.assertEqual(response.status_code, 200)
        private_message.refresh_from_db()
        self.assertTrue(private_message.is_read)

    def test_navigation_context_contains_social_counts(self):
        sender = User.objects.create_user(
            username='sender',
            password='StrongPass12345',
        )
        receiver = User.objects.create_user(
            username='receiver',
            password='StrongPass12345',
        )
        FriendRequest.objects.create(sender=sender, receiver=receiver)
        Friendship.connect(sender, receiver)
        PrivateMessage.objects.create(
            sender=sender,
            recipient=receiver,
            content='未读消息',
        )
        self.client.login(username='receiver', password='StrongPass12345')

        response = self.client.get(reverse('user_center'))

        self.assertEqual(response.context['pending_friend_request_count'], 1)
        self.assertEqual(response.context['unread_private_message_count'], 1)

    def test_logout_clears_session(self):
        User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')

        response = self.client.get(reverse('logout'))

        self.assertRedirects(response, reverse('index'))
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_user_center_requires_login(self):
        response = self.client.get(reverse('user_center'))

        self.assertRedirects(response, f"{reverse('login')}?next={reverse('user_center')}")

    def test_user_center_creates_profile(self):
        User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')

        response = self.client.get(reverse('user_center'))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(UserProfile.objects.filter(user__username='writer').exists())

    def test_user_center_updates_profile_and_email(self):
        user = User.objects.create_user(username='writer', password='StrongPass12345')
        self.client.login(username='writer', password='StrongPass12345')

        response = self.client.post(reverse('user_center'), {
            'nickname': '写作者',
            'bio': '记录一点生活和技术。',
            'email': 'writer@example.com',
        })

        user.refresh_from_db()
        profile = UserProfile.objects.get(user=user)
        self.assertRedirects(response, reverse('user_center'))
        self.assertEqual(user.email, 'writer@example.com')
        self.assertEqual(profile.nickname, '写作者')
        self.assertEqual(profile.bio, '记录一点生活和技术。')

    def test_footer_social_links_use_root_settings_for_everyone(self):
        root = User.objects.create_superuser(
            username='root',
            password='StrongPass12345',
            email='root@example.com',
        )
        UserProfile.objects.create(
            user=root,
            github_url='https://github.com/root',
            weibo_url='https://weibo.com/root',
        )
        user = User.objects.create_user(
            username='writer',
            password='StrongPass12345',
            email='writer@example.com',
        )
        UserProfile.objects.create(
            user=user,
            github_url='https://github.com/writer',
            weibo_url='https://weibo.com/writer',
        )
        self.client.login(username='writer', password='StrongPass12345')

        response = self.client.get(reverse('index'))

        self.assertContains(response, 'href="https://github.com/root"')
        self.assertContains(response, 'href="https://weibo.com/root"')
        self.assertContains(response, 'href="mailto:root@example.com"')
        self.assertContains(response, f'href="{reverse("rss_feed")}"')
        self.assertNotContains(response, 'https://github.com/writer')
        self.assertNotContains(response, 'https://weibo.com/writer')
        self.assertNotContains(response, 'mailto:writer@example.com')

    def test_only_root_gets_footer_setup_links_when_root_settings_are_missing(self):
        User.objects.create_superuser(username='root', password='StrongPass12345')
        User.objects.create_user(username='writer', password='StrongPass12345')

        self.client.login(username='writer', password='StrongPass12345')
        response = self.client.get(reverse('index'))
        self.assertContains(response, 'title="GitHub（root 未配置）"')
        self.assertContains(response, 'aria-disabled="true"')

        self.client.logout()
        self.client.login(username='root', password='StrongPass12345')
        response = self.client.get(reverse('index'))
        self.assertContains(response, f'href="{reverse("user_center")}" title="GitHub（未配置）"')

    def test_rss_feed_route_returns_xml(self):
        root = User.objects.create_superuser(username='root', password='StrongPass12345')
        writer = User.objects.create_user(username='writer', password='StrongPass12345')
        Post.objects.create(author=root, title='root 的文章', category='life', content='可见', status='published', visibility='public')
        Post.objects.create(author=writer, title='writer 的文章', category='life', content='不可见', status='published')

        response = self.client.get(reverse('rss_feed'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/rss+xml; charset=utf-8')
        self.assertContains(response, '<rss version="2.0">')
        self.assertContains(response, 'root 的文章')
        self.assertNotContains(response, 'writer 的文章')
    
    def test_post_detail_increments_views_count(self):
        owner = User.objects.create_user(username='owner', password='StrongPass12345')
        post = Post.objects.create(
            author=owner,
            title='有浏览量的文章',
            category='life',
            content='测试浏览量',
            status='published',
        )
        self.client.login(username='owner', password='StrongPass12345')

        response = self.client.get(reverse('post_detail', args=[post.id]))

        self.assertEqual(response.status_code, 200)
        post.refresh_from_db()
        self.assertEqual(post.views_count, 1)

    def test_create_post_rejects_invalid_cropped_cover_data(self):
        author = User.objects.create_user(username='invalid-cover-author', password='StrongPass12345')
        self.client.login(username='invalid-cover-author', password='StrongPass12345')

        response = self.client.post(reverse('create_post'), {
            'title': '无效裁剪封面文章',
            'category': 'life',
            'tags': '',
            'content': '正文',
            'visibility': 'public',
            'action': 'publish',
            'cropped_cover': 'not-a-valid-data-url',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Post.objects.filter(title='无效裁剪封面文章').exists())
        self.assertContains(response, '图片数据无效')

    def test_create_post_rejects_non_image_cover_upload(self):
        author = User.objects.create_user(username='non-image-cover-author', password='StrongPass12345')
        self.client.login(username='non-image-cover-author', password='StrongPass12345')
        cover_file = SimpleUploadedFile(
            'cover.txt',
            b'this is not an image',
            content_type='text/plain',
        )

        response = self.client.post(reverse('create_post'), {
            'title': '非图片封面文章',
            'category': 'life',
            'tags': '',
            'content': '正文',
            'visibility': 'public',
            'action': 'publish',
            'cover': cover_file,
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Post.objects.filter(title='非图片封面文章').exists())
        self.assertContains(response, '请上传有效的图片文件')


class AuthorProfileTests(TestCase):
    def test_author_profile_shows_public_posts_to_anonymous_user(self):
        author = User.objects.create_user(username='profile-author', password='StrongPass12345')
        Post.objects.create(
            author=author,
            title='Public profile post',
            category='life',
            content='Readable content',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=author,
            title='Private profile post',
            category='life',
            content='Hidden content',
            status='published',
            visibility='private',
        )

        response = self.client.get(reverse('author_profile', args=[author.username]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Public profile post')
        self.assertNotContains(response, 'Private profile post')
        self.assertEqual(response.context['published_count'], 1)

    def test_author_profile_shows_own_private_published_posts_to_author(self):
        author = User.objects.create_user(username='private-author', password='StrongPass12345')
        Post.objects.create(
            author=author,
            title='Own private published post',
            category='study',
            content='Private but readable by owner',
            status='published',
            visibility='private',
        )
        self.client.login(username='private-author', password='StrongPass12345')

        response = self.client.get(reverse('author_profile', args=[author.username]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Own private published post')
        self.assertEqual(response.context['published_count'], 1)

    def test_author_profile_excludes_drafts(self):
        author = User.objects.create_user(username='draft-profile-author', password='StrongPass12345')
        Post.objects.create(
            author=author,
            title='Draft profile post',
            category='life',
            content='Draft content',
            status='draft',
            visibility='public',
        )
        self.client.login(username='draft-profile-author', password='StrongPass12345')

        response = self.client.get(reverse('author_profile', args=[author.username]))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Draft profile post')
        self.assertEqual(response.context['published_count'], 0)

    def test_index_author_link_points_to_author_profile(self):
        author = User.objects.create_user(username='linked-author', password='StrongPass12345')
        Post.objects.create(
            author=author,
            title='Linked author post',
            category='life',
            content='Post content',
            status='published',
            visibility='public',
        )

        response = self.client.get(reverse('index'))

        self.assertContains(response, reverse('author_profile', args=[author.username]))


class FavoritePostTests(TestCase):
    def get_favorite_model(self):
        return apps.get_model('blog', 'PostFavorite')

    def test_logged_in_user_can_favorite_readable_post(self):
        author = User.objects.create_user(username='favorite-author', password='StrongPass12345')
        reader = User.objects.create_user(username='favorite-reader', password='StrongPass12345')
        post = Post.objects.create(
            author=author,
            title='Readable favorite post',
            category='reading',
            content='Readable content',
            status='published',
            visibility='public',
        )
        self.client.login(username='favorite-reader', password='StrongPass12345')

        response = self.client.post(reverse('toggle_favorite', args=[post.id]), {
            'next': reverse('post_detail', args=[post.id]),
        })

        self.assertRedirects(response, reverse('post_detail', args=[post.id]))
        PostFavorite = self.get_favorite_model()
        self.assertTrue(PostFavorite.objects.filter(user=reader, post=post).exists())

    def test_repeating_favorite_post_removes_favorite(self):
        author = User.objects.create_user(username='toggle-author', password='StrongPass12345')
        reader = User.objects.create_user(username='toggle-reader', password='StrongPass12345')
        post = Post.objects.create(
            author=author,
            title='Toggle favorite post',
            category='life',
            content='Post content',
            status='published',
            visibility='public',
        )
        PostFavorite = self.get_favorite_model()
        PostFavorite.objects.create(user=reader, post=post)
        self.client.login(username='toggle-reader', password='StrongPass12345')

        response = self.client.post(reverse('toggle_favorite', args=[post.id]), {
            'next': reverse('post_detail', args=[post.id]),
        })

        self.assertRedirects(response, reverse('post_detail', args=[post.id]))
        self.assertFalse(PostFavorite.objects.filter(user=reader, post=post).exists())

    def test_anonymous_user_cannot_favorite_post(self):
        author = User.objects.create_user(username='anonymous-favorite-author', password='StrongPass12345')
        post = Post.objects.create(
            author=author,
            title='Anonymous favorite post',
            category='life',
            content='Post content',
            status='published',
            visibility='public',
        )

        response = self.client.post(reverse('toggle_favorite', args=[post.id]))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('toggle_favorite', args=[post.id])}",
        )

    def test_user_cannot_favorite_unreadable_private_post(self):
        author = User.objects.create_user(username='private-favorite-author', password='StrongPass12345')
        reader = User.objects.create_user(username='private-favorite-reader', password='StrongPass12345')
        post = Post.objects.create(
            author=author,
            title='Unreadable private favorite post',
            category='life',
            content='Private content',
            status='published',
            visibility='private',
        )
        self.client.login(username='private-favorite-reader', password='StrongPass12345')

        response = self.client.post(reverse('toggle_favorite', args=[post.id]))

        self.assertEqual(response.status_code, 404)
        PostFavorite = self.get_favorite_model()
        self.assertFalse(PostFavorite.objects.filter(user=reader, post=post).exists())

    def test_favorites_page_only_lists_still_readable_posts(self):
        owner = User.objects.create_user(username='favorite-owner', password='StrongPass12345')
        reader = User.objects.create_user(username='favorite-page-reader', password='StrongPass12345')
        public_post = Post.objects.create(
            author=owner,
            title='Still readable favorite',
            category='life',
            content='Public content',
            status='published',
            visibility='public',
        )
        private_post = Post.objects.create(
            author=owner,
            title='No longer readable favorite',
            category='life',
            content='Private content',
            status='published',
            visibility='private',
        )
        PostFavorite = self.get_favorite_model()
        PostFavorite.objects.create(user=reader, post=public_post)
        PostFavorite.objects.create(user=reader, post=private_post)
        self.client.login(username='favorite-page-reader', password='StrongPass12345')

        response = self.client.get(reverse('favorite_posts'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Still readable favorite')
        self.assertNotContains(response, 'No longer readable favorite')


class NotificationCenterTests(TestCase):
    def get_notification_model(self):
        return apps.get_model('blog', 'Notification')

    def test_commenting_on_post_notifies_post_author(self):
        author = User.objects.create_user(username='comment-notify-author', password='StrongPass12345')
        commenter = User.objects.create_user(username='comment-notify-user', password='StrongPass12345')
        post = Post.objects.create(
            author=author,
            title='Comment notification post',
            category='life',
            content='Post content',
            status='published',
            visibility='public',
        )
        self.client.login(username='comment-notify-user', password='StrongPass12345')

        self.client.post(reverse('add_comment', args=[post.id]), {'content': 'New comment'})

        Notification = self.get_notification_model()
        notification = Notification.objects.get(recipient=author)
        self.assertEqual(notification.actor, commenter)
        self.assertEqual(notification.notification_type, 'comment_on_post')
        self.assertIn(reverse('post_detail', args=[post.id]), notification.target_url)

    def test_replying_to_comment_notifies_parent_comment_author(self):
        author = User.objects.create_user(username='reply-post-author', password='StrongPass12345')
        commenter = User.objects.create_user(username='reply-parent-author', password='StrongPass12345')
        replier = User.objects.create_user(username='reply-user', password='StrongPass12345')
        post = Post.objects.create(
            author=author,
            title='Reply notification post',
            category='life',
            content='Post content',
            status='published',
            visibility='public',
        )
        parent_comment = Comment.objects.create(post=post, author=commenter, content='Parent comment')
        self.client.login(username='reply-user', password='StrongPass12345')

        self.client.post(reverse('add_comment', args=[post.id]), {
            'content': 'Reply comment',
            'parent_id': parent_comment.id,
        })

        Notification = self.get_notification_model()
        notification = Notification.objects.get(recipient=commenter)
        self.assertEqual(notification.actor, replier)
        self.assertEqual(notification.notification_type, 'reply_to_comment')

    def test_user_does_not_receive_notification_for_own_action(self):
        author = User.objects.create_user(username='self-notify-author', password='StrongPass12345')
        post = Post.objects.create(
            author=author,
            title='Self notification post',
            category='life',
            content='Post content',
            status='published',
            visibility='public',
        )
        self.client.login(username='self-notify-author', password='StrongPass12345')

        self.client.post(reverse('add_comment', args=[post.id]), {'content': 'Own comment'})

        Notification = self.get_notification_model()
        self.assertFalse(Notification.objects.filter(recipient=author).exists())

    def test_sending_friend_request_notifies_receiver(self):
        sender = User.objects.create_user(username='notify-request-sender', password='StrongPass12345')
        receiver = User.objects.create_user(username='notify-request-receiver', password='StrongPass12345')
        self.client.login(username='notify-request-sender', password='StrongPass12345')

        self.client.post(reverse('send_friend_request', args=[receiver.id]))

        Notification = self.get_notification_model()
        notification = Notification.objects.get(recipient=receiver)
        self.assertEqual(notification.actor, sender)
        self.assertEqual(notification.notification_type, 'friend_request_received')

    def test_accepting_friend_request_notifies_sender(self):
        sender = User.objects.create_user(username='accepted-request-sender', password='StrongPass12345')
        receiver = User.objects.create_user(username='accepted-request-receiver', password='StrongPass12345')
        friend_request = FriendRequest.objects.create(sender=sender, receiver=receiver)
        self.client.login(username='accepted-request-receiver', password='StrongPass12345')

        self.client.post(reverse('accept_friend_request', args=[friend_request.id]))

        Notification = self.get_notification_model()
        notification = Notification.objects.get(recipient=sender)
        self.assertEqual(notification.actor, receiver)
        self.assertEqual(notification.notification_type, 'friend_request_accepted')

    def test_sending_private_message_notifies_recipient(self):
        sender = User.objects.create_user(username='notify-message-sender', password='StrongPass12345')
        recipient = User.objects.create_user(username='notify-message-recipient', password='StrongPass12345')
        Friendship.connect(sender, recipient)
        self.client.login(username='notify-message-sender', password='StrongPass12345')

        self.client.post(reverse('conversation', args=[recipient.id]), {'content': 'Hello from a friend'})

        Notification = self.get_notification_model()
        notification = Notification.objects.get(recipient=recipient)
        self.assertEqual(notification.actor, sender)
        self.assertEqual(notification.notification_type, 'private_message')

    def test_notifications_page_only_shows_current_users_notifications(self):
        current_user = User.objects.create_user(username='notification-owner', password='StrongPass12345')
        other_user = User.objects.create_user(username='notification-other', password='StrongPass12345')
        Notification = self.get_notification_model()
        Notification.objects.create(
            recipient=current_user,
            actor=other_user,
            notification_type='private_message',
            message='Visible notification',
            target_url=reverse('index'),
        )
        Notification.objects.create(
            recipient=other_user,
            actor=current_user,
            notification_type='private_message',
            message='Hidden notification',
            target_url=reverse('index'),
        )
        self.client.login(username='notification-owner', password='StrongPass12345')

        response = self.client.get(reverse('notifications'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Visible notification')
        self.assertNotContains(response, 'Hidden notification')

    def test_read_notification_marks_it_read_and_redirects_to_target(self):
        recipient = User.objects.create_user(username='read-notification-user', password='StrongPass12345')
        actor = User.objects.create_user(username='read-notification-actor', password='StrongPass12345')
        post = Post.objects.create(
            author=actor,
            title='Notification target post',
            category='life',
            content='Post content',
            status='published',
            visibility='public',
        )
        Notification = self.get_notification_model()
        notification = Notification.objects.create(
            recipient=recipient,
            actor=actor,
            notification_type='comment_on_post',
            message='Read me',
            target_url=reverse('post_detail', args=[post.id]),
        )
        self.client.login(username='read-notification-user', password='StrongPass12345')

        response = self.client.post(reverse('read_notification', args=[notification.id]))

        self.assertRedirects(response, reverse('post_detail', args=[post.id]))
        notification.refresh_from_db()
        self.assertTrue(notification.is_read)

    def test_mark_all_notifications_read_only_updates_current_user(self):
        current_user = User.objects.create_user(username='read-all-current', password='StrongPass12345')
        other_user = User.objects.create_user(username='read-all-other', password='StrongPass12345')
        Notification = self.get_notification_model()
        current_notification = Notification.objects.create(
            recipient=current_user,
            notification_type='private_message',
            message='Current unread',
            target_url=reverse('index'),
        )
        other_notification = Notification.objects.create(
            recipient=other_user,
            notification_type='private_message',
            message='Other unread',
            target_url=reverse('index'),
        )
        self.client.login(username='read-all-current', password='StrongPass12345')

        response = self.client.post(reverse('mark_all_notifications_read'))

        self.assertRedirects(response, reverse('notifications'))
        current_notification.refresh_from_db()
        other_notification.refresh_from_db()
        self.assertTrue(current_notification.is_read)
        self.assertFalse(other_notification.is_read)

    def test_navigation_context_contains_unread_notification_count(self):
        recipient = User.objects.create_user(username='notification-count-user', password='StrongPass12345')
        Notification = self.get_notification_model()
        Notification.objects.create(
            recipient=recipient,
            notification_type='private_message',
            message='Unread notification',
            target_url=reverse('index'),
        )
        self.client.login(username='notification-count-user', password='StrongPass12345')

        response = self.client.get(reverse('index'))

        self.assertEqual(response.context['unread_notification_count'], 1)
        self.assertContains(response, '通知')


class HomepageTemplateIntegrationTests(TestCase):
    def test_index_uses_shared_navigation_and_still_renders_search_and_posts(self):
        author = User.objects.create_user(username='homepage-author', password='StrongPass12345')
        Post.objects.create(
            author=author,
            title='Homepage inherited post',
            category='life',
            content='Homepage content',
            status='published',
            visibility='public',
        )

        response = self.client.get(reverse('index'))

        self.assertTemplateUsed(response, 'index.html')
        self.assertTemplateUsed(response, 'base.html')
        self.assertContains(response, '归档')
        self.assertContains(response, '标签')
        self.assertContains(response, '搜索文章')
        self.assertContains(response, 'Homepage inherited post')

    def test_home_uses_home_template_and_keeps_index_as_article_list(self):
        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'home.html')
        self.assertTemplateUsed(response, 'base.html')
        self.assertContains(response, '开始阅读')
        self.assertContains(response, reverse('index'))

        index_response = self.client.get(reverse('index'))

        self.assertEqual(index_response.status_code, 200)
        self.assertTemplateUsed(index_response, 'index.html')
        self.assertContains(index_response, '搜索文章')

    def test_home_recent_posts_use_public_visibility_for_anonymous_users(self):
        author = User.objects.create_user(username='public-home-author', password='StrongPass12345')
        public_post = Post.objects.create(
            author=author,
            title='Public homepage post',
            category='life',
            content='Public content',
            status='published',
            visibility='public',
        )
        Post.objects.create(
            author=author,
            title='Private homepage post',
            category='life',
            content='Private content',
            status='published',
            visibility='private',
        )
        Post.objects.create(
            author=author,
            title='Draft homepage post',
            category='life',
            content='Draft content',
            status='draft',
            visibility='public',
        )

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['recent_posts']), [public_post])
        self.assertContains(response, 'Public homepage post')
        self.assertNotContains(response, 'Private homepage post')
        self.assertNotContains(response, 'Draft homepage post')

    def test_home_recent_posts_include_logged_in_users_private_published_posts(self):
        current_user = User.objects.create_user(username='home-current', password='StrongPass12345')
        other_user = User.objects.create_user(username='home-other', password='StrongPass12345')
        own_private_post = Post.objects.create(
            author=current_user,
            title='Own private homepage post',
            category='life',
            content='Own private content',
            status='published',
            visibility='private',
        )
        Post.objects.create(
            author=other_user,
            title='Other private homepage post',
            category='life',
            content='Other private content',
            status='published',
            visibility='private',
        )
        self.client.login(username='home-current', password='StrongPass12345')

        response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertIn(own_private_post, list(response.context['recent_posts']))
        self.assertContains(response, 'Own private homepage post')
        self.assertNotContains(response, 'Other private homepage post')

    def test_home_carousel_uses_allowed_images_from_media_index_img(self):
        with tempfile.TemporaryDirectory() as temporary_media_root:
            image_directory = os.path.join(temporary_media_root, 'index_img')
            os.makedirs(image_directory)
            with open(os.path.join(image_directory, 'first image.jpg'), 'wb') as image_file:
                image_file.write(b'fake jpg')
            with open(os.path.join(image_directory, 'second.png'), 'wb') as image_file:
                image_file.write(b'fake png')
            with open(os.path.join(image_directory, 'notes.txt'), 'wb') as text_file:
                text_file.write(b'not an image')

            with self.settings(MEDIA_ROOT=temporary_media_root, MEDIA_URL='/media/'):
                response = self.client.get(reverse('home'))

        carousel_slides = response.context['carousel_slides']
        self.assertEqual(len(carousel_slides), 2)
        self.assertEqual(carousel_slides[0]['file_name'], 'first image.jpg')
        self.assertEqual(
            carousel_slides[0]['image_url'],
            reverse('homepage_carousel_image', args=['first image.jpg']),
        )
        self.assertEqual(carousel_slides[1]['file_name'], 'second.png')
        self.assertNotContains(response, 'notes.txt')

    def test_home_carousel_includes_every_allowed_image_from_media_index_img(self):
        with tempfile.TemporaryDirectory() as temporary_media_root:
            image_directory = os.path.join(temporary_media_root, 'index_img')
            os.makedirs(image_directory)
            for image_index in range(15):
                image_file_name = f'slide-{image_index:02d}.jpg'
                with open(os.path.join(image_directory, image_file_name), 'wb') as image_file:
                    image_file.write(b'fake jpg')

            with self.settings(MEDIA_ROOT=temporary_media_root, MEDIA_URL='/media/'):
                response = self.client.get(reverse('home'))

        carousel_slides = response.context['carousel_slides']
        self.assertEqual(len(carousel_slides), 15)
        self.assertEqual(carousel_slides[0]['file_name'], 'slide-00.jpg')
        self.assertEqual(carousel_slides[-1]['file_name'], 'slide-14.jpg')

    def test_home_carousel_uses_optimized_image_endpoint(self):
        with tempfile.TemporaryDirectory() as temporary_media_root:
            image_directory = os.path.join(temporary_media_root, 'index_img')
            os.makedirs(image_directory)
            with open(os.path.join(image_directory, 'large photo.jpg'), 'wb') as image_file:
                image_file.write(b'fake jpg')

            with self.settings(MEDIA_ROOT=temporary_media_root, MEDIA_URL='/media/'):
                response = self.client.get(reverse('home'))

        carousel_slides = response.context['carousel_slides']
        self.assertEqual(
            carousel_slides[0]['image_url'],
            reverse('homepage_carousel_image', args=['large photo.jpg']),
        )

    def test_homepage_carousel_image_view_creates_optimized_cache_file(self):
        with tempfile.TemporaryDirectory() as temporary_media_root:
            image_directory = os.path.join(temporary_media_root, 'index_img')
            os.makedirs(image_directory)
            source_image_path = os.path.join(image_directory, 'wide photo.jpg')
            Image.new('RGB', (2400, 1200), color=(120, 160, 200)).save(source_image_path, format='JPEG')

            with self.settings(MEDIA_ROOT=temporary_media_root, MEDIA_URL='/media/'):
                response = self.client.get(reverse('homepage_carousel_image', args=['wide photo.jpg']))

                cache_directory = os.path.join(temporary_media_root, 'index_img_cache')
                cached_file_names = os.listdir(cache_directory)
                cached_image_path = os.path.join(cache_directory, cached_file_names[0])
                with Image.open(cached_image_path) as cached_image:
                    cached_image_width = cached_image.width

        self.assertEqual(response.status_code, 302)
        self.assertEqual(len(cached_file_names), 1)
        self.assertTrue(response['Location'].startswith('/media/index_img_cache/'))
        self.assertLessEqual(cached_image_width, 1920)

    def test_home_carousel_handles_missing_media_index_img(self):
        with tempfile.TemporaryDirectory() as temporary_media_root:
            with self.settings(MEDIA_ROOT=temporary_media_root, MEDIA_URL='/media/'):
                response = self.client.get(reverse('home'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['carousel_slides'], [])
        self.assertContains(response, 'no-carousel-images')

    def test_mobile_search_button_keeps_visible_icon_styling(self):
        response = self.client.get(reverse('index'))

        self.assertContains(response, '@media (max-width: 576px)')
        self.assertContains(response, '.hero-search .btn')
        self.assertContains(response, 'background: #2e7d32')
        self.assertContains(response, 'color: #fff')
        self.assertContains(response, 'flex: 0 0 56px')


class PostDeletionTests(TestCase):
    def assert_post_deletion_form(self, response, deletion_url):
        self.assertEqual(response.status_code, 200)

        response_html = response.content.decode()
        deletion_form_parser = DeletionFormParser(deletion_url)
        deletion_form_parser.feed(response_html)

        self.assertTrue(
            deletion_form_parser.deletion_form_found,
            'Deletion POST form was not found.',
        )
        self.assertTrue(
            deletion_form_parser.csrf_token_found,
            'Deletion POST form does not contain a valid CSRF token.',
        )
        self.assertTrue(
            deletion_form_parser.submit_button_found,
            'Deletion POST form does not contain a submit button.',
        )
        self.assertFalse(
            deletion_form_parser.delete_link_found,
            'A GET deletion link is still present.',
        )

    def test_delete_draft_rejects_get_request(self):
        author = User.objects.create_user(
            username='draft-author',
            password='StrongPass12345',
        )
        draft_post = Post.objects.create(
            author=author,
            title='Draft that cannot be deleted with GET',
            category='life',
            content='Draft content',
            status='draft',
        )
        self.client.login(
            username='draft-author',
            password='StrongPass12345',
        )

        response = self.client.get(
            reverse('delete_draft', args=[draft_post.id]),
        )

        self.assertEqual(response.status_code, 405)
        self.assertTrue(Post.objects.filter(id=draft_post.id).exists())

    def test_author_can_delete_draft_with_post_request(self):
        author = User.objects.create_user(
            username='draft-author',
            password='StrongPass12345',
        )
        draft_post = Post.objects.create(
            author=author,
            title='Draft deleted with POST',
            category='life',
            content='Draft content',
            status='draft',
        )
        self.client.login(
            username='draft-author',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('delete_draft', args=[draft_post.id]),
        )

        self.assertRedirects(response, reverse('drafts'))
        self.assertFalse(Post.objects.filter(id=draft_post.id).exists())

    def test_drafts_page_uses_post_form_for_deletion(self):
        author = User.objects.create_user(
            username='draft-author',
            password='StrongPass12345',
        )
        draft_post = Post.objects.create(
            author=author,
            title='Draft with deletion form',
            category='life',
            content='Draft content',
            status='draft',
        )
        self.client.login(
            username='draft-author',
            password='StrongPass12345',
        )

        response = self.client.get(reverse('drafts'))

        delete_draft_url = reverse(
            'delete_draft',
            args=[draft_post.id],
        )
        self.assert_post_deletion_form(response, delete_draft_url)

    def test_delete_published_post_rejects_get_request(self):
        author = User.objects.create_user(
            username='published-author',
            password='StrongPass12345',
        )
        published_post = Post.objects.create(
            author=author,
            title='Published post that cannot be deleted with GET',
            category='life',
            content='Published post content',
            status='published',
            visibility='public',
        )
        self.client.login(
            username='published-author',
            password='StrongPass12345',
        )

        response = self.client.get(
            reverse('delete_post', args=[published_post.id]),
        )

        self.assertEqual(response.status_code, 405)
        self.assertTrue(
            Post.objects.filter(id=published_post.id).exists()
        )

    def test_author_can_delete_published_post_with_post_request(self):
        author = User.objects.create_user(
            username='published-author',
            password='StrongPass12345',
        )
        published_post = Post.objects.create(
            author=author,
            title='Published post deleted with POST',
            category='life',
            content='Published post content',
            status='published',
            visibility='public',
        )
        self.client.login(
            username='published-author',
            password='StrongPass12345',
        )

        response = self.client.post(
            reverse('delete_post', args=[published_post.id]),
        )

        self.assertRedirects(response, reverse('index'))
        self.assertFalse(
            Post.objects.filter(id=published_post.id).exists()
        )

    def test_post_detail_uses_post_form_for_deletion(self):
        author = User.objects.create_user(
            username='published-author',
            password='StrongPass12345',
        )
        published_post = Post.objects.create(
            author=author,
            title='Published post with deletion form',
            category='life',
            content='Published post content',
            status='published',
            visibility='public',
        )
        self.client.login(
            username='published-author',
            password='StrongPass12345',
        )

        response = self.client.get(
            reverse('post_detail', args=[published_post.id]),
        )

        delete_post_url = reverse(
            'delete_post',
            args=[published_post.id],
        )
        self.assert_post_deletion_form(response, delete_post_url)


class StartupPostCommandTests(TestCase):
    def deepseek_response(self):
        response_body = {
            'choices': [
                {
                    'message': {
                        'content': (
                            '{"title": "给早晨留出十分钟的整理时间", '
                            '"category": "life", '
                            '"tags": ["生活技巧", "整理"], '
                            '"content": "早晨的状态往往会影响一整天。可以把起床后的前十分钟留给简单整理：先喝一杯温水，再把桌面上明显不用的物品放回原位，最后写下今天最重要的一件事。这个过程不需要追求完美，重点是让自己从混乱里慢慢进入节奏。整理空间的同时，也是在整理注意力。坚持几天后，你会发现开始工作或学习时，犹豫和拖延会少一点。"}'
                        ),
                    },
                }
            ]
        }
        return FakeDeepSeekResponse(response_body)

    def test_create_startup_post_creates_one_published_daily_article_for_user(self):
        author = User.objects.create_user(username='白车轴草', password='StrongPass12345')
        command_output = StringIO()
        current_date = timezone.localdate()

        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}, clear=True):
            with patch('blog.management.commands.create_startup_post.urlopen', return_value=self.deepseek_response()):
                call_command('create_startup_post', stdout=command_output)

        post = Post.objects.get(author=author)
        self.assertEqual(post.status, 'published')
        self.assertEqual(post.visibility, 'public')
        self.assertEqual(post.category, 'life')
        self.assertIn('自动发布', post.tags)
        self.assertIn('生活技巧', post.tags)
        self.assertIn(f'daily:{current_date.isoformat()}', post.tags)
        self.assertEqual(post.title, '给早晨留出十分钟的整理时间')
        self.assertNotIn(current_date.strftime('%Y-%m-%d'), post.title)
        self.assertIn('Created daily article', command_output.getvalue())

    def test_create_startup_post_can_create_draft_when_requested(self):
        author = User.objects.create_user(username='白车轴草', password='StrongPass12345')

        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}, clear=True):
            with patch('blog.management.commands.create_startup_post.urlopen', return_value=self.deepseek_response()):
                call_command('create_startup_post', draft=True)

        post = Post.objects.get(author=author)
        self.assertEqual(post.status, 'draft')
        self.assertEqual(post.visibility, 'private')

    def test_create_startup_post_skips_duplicate_for_same_day(self):
        author = User.objects.create_user(username='白车轴草', password='StrongPass12345')
        command_output = StringIO()

        with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-key'}, clear=True):
            with patch('blog.management.commands.create_startup_post.urlopen', return_value=self.deepseek_response()):
                call_command('create_startup_post', stdout=command_output)
                call_command('create_startup_post', stdout=command_output)

        self.assertEqual(Post.objects.filter(author=author).count(), 1)
        self.assertIn('Daily article already exists', command_output.getvalue())

    def test_create_startup_post_can_attach_cover_to_existing_daily_post(self):
        author = User.objects.create_user(username='白车轴草', password='StrongPass12345')
        current_date = timezone.localdate()
        post = Post.objects.create(
            author=author,
            title=f'{current_date.strftime("%Y-%m-%d")}｜已有文章',
            category='life',
            tags=f'自动发布,生活技巧,daily:{current_date.isoformat()}',
            content='已有正文',
            status='published',
        )

        with patch.dict(os.environ, {'PEXELS_API_KEY': 'test-key'}, clear=True):
            with patch('blog.management.commands.create_startup_post.Command.attach_cover') as attach_cover:
                call_command('create_startup_post', username='白车轴草', cover_existing=True)

        self.assertEqual(Post.objects.filter(author=author).count(), 1)
        attach_cover.assert_called_once()
        attached_post = attach_cover.call_args.args[0]
        generated_article = attach_cover.call_args.args[1]
        self.assertEqual(attached_post, post)
        self.assertEqual(generated_article['title'], '已有文章')
        self.assertEqual(generated_article['category'], 'life')

    def test_create_startup_post_requires_deepseek_api_key(self):
        User.objects.create_user(username='白车轴草', password='StrongPass12345')

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(CommandError):
                call_command('create_startup_post')

    def test_create_startup_post_requires_existing_user(self):
        with self.assertRaises(CommandError):
            call_command('create_startup_post', username='missing-user')

    def test_attach_cover_saves_pexels_photo_and_attribution(self):
        author = User.objects.create_user(username='白车轴草', password='StrongPass12345')
        post = Post.objects.create(
            author=author,
            title='测试文章',
            category='life',
            tags='自动发布,daily:2026-06-23',
            content='测试正文',
            status='published',
        )
        generated_article = {
            'title': '给早晨留出十分钟的整理时间',
            'category': 'life',
            'tags': ['生活技巧', '整理'],
            'content': '测试正文',
        }
        pexels_photo = {
            'id': 12345,
            'url': 'https://www.pexels.com/photo/test-photo-12345/',
            'photographer': 'Test Photographer',
            'photographer_url': 'https://www.pexels.com/@test',
            'src': {'landscape': 'https://images.pexels.com/photos/12345/test.jpg'},
        }
        command = Command()

        with patch.dict(os.environ, {'PEXELS_API_KEY': 'test-key'}, clear=True):
            with patch.object(command, 'search_pexels_photo', return_value=pexels_photo):
                with patch.object(command, 'download_pexels_image', return_value=b'image-bytes'):
                    with patch.object(FieldFile, 'save') as save_cover:
                        command.attach_cover(post, generated_article, timezone.localdate())

        post.refresh_from_db()
        self.assertIn('Photo by Test Photographer on Pexels', post.content)
        self.assertIn('https://www.pexels.com/photo/test-photo-12345/', post.content)
        save_cover.assert_called_once()


class FakeDeepSeekResponse:
    def __init__(self, response_body):
        self.response_body = response_body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(self.response_body).encode('utf-8')
