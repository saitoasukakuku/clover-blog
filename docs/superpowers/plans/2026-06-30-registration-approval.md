# Registration Approval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct public registration with an email request, superuser review, one-time emailed registration code, and final account creation flow.

**Architecture:** Add a `RegistrationRequest` model for the approval state, keep `/register/` as the public email request route, and add `/register/complete/` plus superuser-only review routes. Put code generation, hashing, sender selection, and email sending in a small `blog/registration_approval.py` helper so views stay focused on forms, permissions, messages, and redirects.

**Tech Stack:** Django 4.2 function views, Django ORM migrations, Django auth forms, Django email backends, Django templates, Bootstrap-compatible existing layout, SQLite/MySQL-compatible tests.

---

## File Structure

- Modify `白车轴草/blog/models.py`: add `RegistrationRequest` and small instance helpers for code hashing, expiry checks, reopening, rejection, and usage.
- Create `白车轴草/blog/migrations/0011_registrationrequest.py`: add the new database table.
- Modify `白车轴草/blog/admin.py`: register `RegistrationRequest` for read/search visibility.
- Modify `白车轴草/blog/registration_approval.py`: create this focused helper module for code generation, sender lookup, and email delivery.
- Modify `白车轴草/blog/forms.py`: replace direct public registration use with `RegistrationRequestForm` and `CompleteRegistrationForm`.
- Modify `白车轴草/blog/views.py`: change `register`, add `complete_registration`, add review page and POST-only approve/reject actions.
- Modify `白车轴草/白车轴草/urls.py`: add completion and review routes.
- Modify `白车轴草/白车轴草/settings.py`: add environment-driven Django email settings with console email as the local default.
- Modify `白车轴草/blog/templates/auth_form.html`: make the header copy and optional secondary link configurable.
- Create `白车轴草/blog/templates/registration_requests.html`: render the superuser review page.
- Modify `白车轴草/blog/templates/base.html`: show a superuser-only `注册审核` link in the existing user dropdown.
- Modify `白车轴草/blog/tests.py`: replace old direct-registration tests and add coverage for request, review, email, and completion behavior.

---

### Task 1: Add the RegistrationRequest Model

**Files:**
- Modify: `白车轴草/blog/tests.py`
- Modify: `白车轴草/blog/models.py`
- Create: `白车轴草/blog/migrations/0011_registrationrequest.py`
- Modify: `白车轴草/blog/admin.py`

- [ ] **Step 1: Write failing model tests**

Update imports in `白车轴草/blog/tests.py`.

```python
from datetime import datetime, timedelta
```

```python
from blog.models import (
    Comment,
    FriendRequest,
    Friendship,
    Post,
    PrivateMessage,
    RegistrationRequest,
    UserProfile,
)
```

Add this test class after `DeletionFormParser`.

```python
class RegistrationRequestModelTests(TestCase):
    def test_invite_code_is_hashed_and_checkable(self):
        registration_request = RegistrationRequest.objects.create(
            email='Reader@Example.COM',
        )

        registration_request.set_invite_code('ABC123CODE456')
        registration_request.save()

        registration_request.refresh_from_db()
        self.assertEqual(registration_request.email, 'reader@example.com')
        self.assertNotEqual(registration_request.invite_code_hash, 'ABC123CODE456')
        self.assertTrue(registration_request.check_invite_code('ABC123CODE456'))
        self.assertFalse(registration_request.check_invite_code('WRONGCODE999'))

    def test_reopen_clears_review_and_code_fields(self):
        reviewer = User.objects.create_superuser(
            username='reviewer',
            email='reviewer@example.com',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
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
```

- [ ] **Step 2: Run model tests and verify they fail**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.RegistrationRequestModelTests
```

Expected: FAIL with an import error because `RegistrationRequest` does not exist yet.

- [ ] **Step 3: Add model imports**

Modify the top of `白车轴草/blog/models.py`.

```python
from django.contrib.auth.hashers import check_password, make_password
from django.contrib.auth.models import User
from django.db import models
from django.utils import timezone
import re
```

- [ ] **Step 4: Add the model**

Place this class after `UserProfile`.

```python
class RegistrationRequest(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_APPROVED = 'approved'
    STATUS_REJECTED = 'rejected'
    STATUS_USED = 'used'
    STATUS_CHOICES = (
        (STATUS_PENDING, '待审核'),
        (STATUS_APPROVED, '已通过'),
        (STATUS_REJECTED, '已拒绝'),
        (STATUS_USED, '已使用'),
    )

    email = models.EmailField(unique=True, verbose_name='申请邮箱')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        verbose_name='状态',
    )
    invite_code_hash = models.CharField(
        max_length=128,
        blank=True,
        verbose_name='注册码哈希',
    )
    code_expires_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='注册码过期时间',
    )
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_registration_requests',
        verbose_name='审核人',
    )
    reviewed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='审核时间',
    )
    used_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='注册完成时间',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='申请时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        ordering = ['-updated_at']
        verbose_name = '注册申请'
        verbose_name_plural = '注册申请'

    @staticmethod
    def normalize_email(email):
        normalized_email = User.objects.normalize_email((email or '').strip())
        return normalized_email.casefold()

    @property
    def is_code_expired(self):
        return bool(self.code_expires_at and self.code_expires_at <= timezone.now())

    def set_invite_code(self, raw_invite_code):
        self.invite_code_hash = make_password(raw_invite_code)

    def check_invite_code(self, raw_invite_code):
        if not self.invite_code_hash:
            return False
        return check_password(raw_invite_code, self.invite_code_hash)

    def reopen(self):
        self.status = self.STATUS_PENDING
        self.invite_code_hash = ''
        self.code_expires_at = None
        self.approved_by = None
        self.reviewed_at = None
        self.used_at = None

    def reject(self, reviewer):
        self.status = self.STATUS_REJECTED
        self.invite_code_hash = ''
        self.code_expires_at = None
        self.approved_by = reviewer
        self.reviewed_at = timezone.now()
        self.used_at = None

    def mark_used(self):
        self.status = self.STATUS_USED
        self.used_at = timezone.now()

    def save(self, *args, **kwargs):
        self.email = self.normalize_email(self.email)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.email} ({self.get_status_display()})'
```

- [ ] **Step 5: Create the migration**

Run:

```powershell
python .\白车轴草\manage.py makemigrations blog
```

Expected: creates `白车轴草/blog/migrations/0011_registrationrequest.py`.

Check that the generated migration has dependency `0010_postfavorite_notification_and_more` and creates the fields from Step 4.

- [ ] **Step 6: Register the model in admin**

Update `白车轴草/blog/admin.py` imports.

```python
from blog.models import (
    Comment,
    FriendRequest,
    Friendship,
    Notification,
    Post,
    PostFavorite,
    PrivateMessage,
    RegistrationRequest,
    UserProfile,
)
```

Add this admin class near the other model admin classes.

```python
@admin.register(RegistrationRequest)
class RegistrationRequestAdmin(admin.ModelAdmin):
    list_display = ('email', 'status', 'approved_by', 'code_expires_at', 'created_at', 'updated_at')
    list_filter = ('status', 'created_at', 'updated_at')
    search_fields = ('email', 'approved_by__username')
    readonly_fields = ('invite_code_hash', 'created_at', 'updated_at')
    ordering = ('-updated_at',)
```

- [ ] **Step 7: Run model tests and migration check**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.RegistrationRequestModelTests
python .\白车轴草\manage.py makemigrations --check --dry-run
```

Expected: model tests PASS, migration check reports no model changes.

- [ ] **Step 8: Commit**

Run:

```powershell
git add 白车轴草/blog/tests.py 白车轴草/blog/models.py 白车轴草/blog/admin.py 白车轴草/blog/migrations/0011_registrationrequest.py
git commit -m "feat: add registration request model"
```

---

### Task 2: Add Email Configuration and Registration Approval Helper

**Files:**
- Modify: `白车轴草/blog/tests.py`
- Create: `白车轴草/blog/registration_approval.py`
- Modify: `白车轴草/白车轴草/settings.py`

- [ ] **Step 1: Write failing helper tests**

Update imports in `白车轴草/blog/tests.py`.

```python
from django.core import mail, signing
from django.test import TestCase, override_settings
```

Add this test class after `RegistrationRequestModelTests`.

```python
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
        from blog.registration_approval import send_registration_code_email

        send_registration_code_email(
            registration_request=registration_request,
            raw_invite_code='ABC123CODE456',
            completion_url='http://testserver/register/complete/',
        )

        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].from_email, 'owner@example.com')
        self.assertEqual(mail.outbox[0].to, ['reader@example.com'])
        self.assertIn('ABC123CODE456', mail.outbox[0].body)
        self.assertIn('http://testserver/register/complete/', mail.outbox[0].body)

    def test_generated_registration_code_has_expected_shape(self):
        from blog.registration_approval import generate_registration_code

        raw_invite_code = generate_registration_code()

        self.assertEqual(len(raw_invite_code), 12)
        self.assertTrue(raw_invite_code.isalnum())
        self.assertEqual(raw_invite_code, raw_invite_code.upper())
```

- [ ] **Step 2: Run helper tests and verify they fail**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.RegistrationApprovalEmailTests
```

Expected: FAIL because `blog.registration_approval` does not exist yet.

- [ ] **Step 3: Add email settings**

Append this block after login redirect settings in `白车轴草/白车轴草/settings.py`.

```python
EMAIL_BACKEND = os.getenv(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend'
    if DEBUG
    else 'django.core.mail.backends.smtp.EmailBackend',
)
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '25'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'False').lower() in {'1', 'true', 'yes', 'on'}
EMAIL_USE_SSL = os.getenv('EMAIL_USE_SSL', 'False').lower() in {'1', 'true', 'yes', 'on'}
DEFAULT_FROM_EMAIL = os.getenv(
    'DEFAULT_FROM_EMAIL',
    EMAIL_HOST_USER or 'webmaster@localhost',
)
```

- [ ] **Step 4: Create the helper module**

Create `白车轴草/blog/registration_approval.py`.

```python
from datetime import timedelta
import secrets

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.utils import timezone

from blog.site_owner import get_site_owner


REGISTRATION_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
REGISTRATION_CODE_LENGTH = 12
REGISTRATION_CODE_EXPIRATION_DAYS = 7
PREFERRED_SENDER_USERNAME = '白车轴草'


def generate_registration_code():
    return ''.join(
        secrets.choice(REGISTRATION_CODE_ALPHABET)
        for _ in range(REGISTRATION_CODE_LENGTH)
    )


def get_registration_sender_email():
    preferred_owner = User.objects.filter(
        username=PREFERRED_SENDER_USERNAME,
        is_superuser=True,
    ).exclude(email='').first()
    if preferred_owner:
        return preferred_owner.email

    site_owner = get_site_owner()
    if site_owner and site_owner.email:
        return site_owner.email

    return settings.DEFAULT_FROM_EMAIL


def send_registration_code_email(registration_request, raw_invite_code, completion_url):
    subject = '白车轴草注册邀请'
    body = (
        '你的白车轴草注册申请已经通过。\\n\\n'
        f'注册码：{raw_invite_code}\\n'
        f'有效期至：{registration_request.code_expires_at:%Y-%m-%d %H:%M}\\n\\n'
        f'请打开这个链接完成注册：{completion_url}\\n\\n'
        '这个注册码只能使用一次。'
    )
    send_mail(
        subject=subject,
        message=body,
        from_email=get_registration_sender_email(),
        recipient_list=[registration_request.email],
        fail_silently=False,
    )


def approve_registration_request(registration_request, reviewer, completion_url):
    raw_invite_code = generate_registration_code()
    registration_request.set_invite_code(raw_invite_code)
    registration_request.code_expires_at = timezone.now() + timedelta(
        days=REGISTRATION_CODE_EXPIRATION_DAYS,
    )
    registration_request.approved_by = reviewer
    registration_request.reviewed_at = timezone.now()
    registration_request.used_at = None
    registration_request.status = registration_request.STATUS_APPROVED

    send_registration_code_email(
        registration_request=registration_request,
        raw_invite_code=raw_invite_code,
        completion_url=completion_url,
    )

    registration_request.save(update_fields=[
        'status',
        'invite_code_hash',
        'code_expires_at',
        'approved_by',
        'reviewed_at',
        'used_at',
        'updated_at',
    ])
    return raw_invite_code
```

- [ ] **Step 5: Run helper tests**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.RegistrationApprovalEmailTests
```

Expected: PASS.

- [ ] **Step 6: Commit**

Run:

```powershell
git add 白车轴草/blog/tests.py 白车轴草/blog/registration_approval.py 白车轴草/白车轴草/settings.py
git commit -m "feat: add registration approval email helper"
```

---

### Task 3: Convert /register/ Into Email Request Flow

**Files:**
- Modify: `白车轴草/blog/tests.py`
- Modify: `白车轴草/blog/forms.py`
- Modify: `白车轴草/blog/views.py`
- Modify: `白车轴草/blog/templates/auth_form.html`

- [ ] **Step 1: Replace old direct registration tests**

In `AuthViewsTests`, replace `test_register_creates_and_logs_in_user`, `test_register_saves_email_and_nickname`, and `test_register_rejects_duplicate_email` with these tests.

```python
    def test_register_requires_email(self):
        response = self.client.post(reverse('register'), {'email': ''})

        self.assertEqual(response.status_code, 200)
        self.assertFalse(RegistrationRequest.objects.exists())
        self.assertContains(response, '请输入邮箱。')

    def test_register_creates_request_without_creating_user(self):
        response = self.client.post(reverse('register'), {
            'email': 'NewReader@Example.COM',
        }, follow=True)

        self.assertRedirects(response, reverse('register'))
        self.assertTrue(
            RegistrationRequest.objects.filter(
                email='newreader@example.com',
                status=RegistrationRequest.STATUS_PENDING,
            ).exists()
        )
        self.assertFalse(User.objects.filter(email__iexact='NewReader@Example.COM').exists())
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
        self.assertFalse(RegistrationRequest.objects.filter(email='used@example.com').exists())
        self.assertContains(response, '这个邮箱已经被注册。')

    def test_register_does_not_duplicate_pending_request(self):
        RegistrationRequest.objects.create(email='reader@example.com')

        response = self.client.post(reverse('register'), {
            'email': 'reader@example.com',
        }, follow=True)

        self.assertRedirects(response, reverse('register'))
        self.assertEqual(RegistrationRequest.objects.filter(email='reader@example.com').count(), 1)
        self.assertContains(response, '这个邮箱的注册申请正在等待审核。')

    def test_register_reopens_expired_approved_request(self):
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
            status=RegistrationRequest.STATUS_APPROVED,
            code_expires_at=timezone.now() - timedelta(days=1),
        )
        registration_request.set_invite_code('ABC123CODE456')
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
```

- [ ] **Step 2: Run request-flow tests and verify they fail**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.AuthViewsTests.test_register_requires_email blog.tests.AuthViewsTests.test_register_creates_request_without_creating_user blog.tests.AuthViewsTests.test_register_rejects_duplicate_registered_email blog.tests.AuthViewsTests.test_register_does_not_duplicate_pending_request blog.tests.AuthViewsTests.test_register_reopens_expired_approved_request
```

Expected: FAIL because `/register/` still uses `ChineseUserCreationForm`.

- [ ] **Step 3: Add request form**

Update imports in `白车轴草/blog/forms.py`.

```python
from blog.models import Comment, PrivateMessage, RegistrationRequest, UserProfile
```

Add this form before `ChineseUserCreationForm`.

```python
class RegistrationRequestForm(forms.Form):
    email = forms.EmailField(
        label='邮箱',
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入邮箱',
            'autocomplete': 'email',
        }),
        error_messages={
            'required': '请输入邮箱。',
            'invalid': '请输入有效的邮箱地址。',
        },
    )

    def clean_email(self):
        email = RegistrationRequest.normalize_email(
            self.cleaned_data.get('email', ''),
        )
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('这个邮箱已经被注册。')
        return email
```

- [ ] **Step 4: Import the new form and model in views**

Modify imports in `白车轴草/blog/views.py`.

```python
from blog.forms import (
    ChineseAuthenticationForm,
    ChineseUserCreationForm,
    CommentForm,
    PrivateMessageForm,
    RegistrationRequestForm,
    UserCenterForm,
)
```

```python
from blog.models import (
    Comment,
    FriendRequest,
    Friendship,
    Notification,
    Post,
    PostFavorite,
    PrivateMessage,
    RegistrationRequest,
    UserProfile,
)
```

- [ ] **Step 5: Replace the register view**

Replace the existing `register` view in `白车轴草/blog/views.py`.

```python
def register(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        form = RegistrationRequestForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            registration_request = RegistrationRequest.objects.filter(
                email__iexact=email,
            ).first()

            if registration_request:
                if registration_request.status == RegistrationRequest.STATUS_PENDING:
                    messages.info(request, '这个邮箱的注册申请正在等待审核。')
                    return redirect('register')
                if (
                    registration_request.status == RegistrationRequest.STATUS_APPROVED
                    and not registration_request.is_code_expired
                ):
                    messages.info(request, '这个邮箱已经通过审核，请查看邮件里的注册码。')
                    return redirect('complete_registration')

                registration_request.reopen()
                registration_request.save()
                messages.success(request, '注册申请已重新提交，请等待审核。')
                return redirect('register')

            RegistrationRequest.objects.create(email=email)
            messages.success(request, '注册申请已提交，请等待审核。')
            return redirect('register')
    else:
        form = RegistrationRequestForm()

    return render(request, 'auth_form.html', {
        'form': form,
        'page_title': '申请注册',
        'page_description': '先提交邮箱，审核通过后会收到一次性注册码。',
        'submit_text': '提交申请',
        'submit_icon': 'fas fa-paper-plane',
        'switch_text': '已经收到注册码？',
        'switch_url_name': 'complete_registration',
        'switch_link_text': '去完成注册',
    })
```

- [ ] **Step 6: Make auth template copy configurable**

In `白车轴草/blog/templates/auth_form.html`, replace the fixed paragraph in `.auth-page-header`.

```html
        <p>{{ page_description|default:'登录后可以创建文章、管理草稿和编辑内容' }}</p>
```

Replace the submit button icon line.

```html
                        <i class="{{ submit_icon|default:'fas fa-arrow-right-to-bracket' }} me-1"></i>{{ submit_text }}
```

- [ ] **Step 7: Run request-flow tests**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.AuthViewsTests.test_register_requires_email blog.tests.AuthViewsTests.test_register_creates_request_without_creating_user blog.tests.AuthViewsTests.test_register_rejects_duplicate_registered_email blog.tests.AuthViewsTests.test_register_does_not_duplicate_pending_request blog.tests.AuthViewsTests.test_register_reopens_expired_approved_request
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```powershell
git add 白车轴草/blog/tests.py 白车轴草/blog/forms.py 白车轴草/blog/views.py 白车轴草/blog/templates/auth_form.html
git commit -m "feat: request registration by email"
```

---

### Task 4: Add Superuser Review Page and Approve/Reject Actions

**Files:**
- Modify: `白车轴草/blog/tests.py`
- Modify: `白车轴草/blog/views.py`
- Modify: `白车轴草/白车轴草/urls.py`
- Create: `白车轴草/blog/templates/registration_requests.html`
- Modify: `白车轴草/blog/templates/base.html`

- [ ] **Step 1: Add review tests**

Add these methods to `AuthViewsTests`.

```python
    def test_registration_requests_requires_superuser(self):
        normal_user = User.objects.create_user(
            username='normal',
            password='StrongPass12345',
        )
        self.client.login(username='normal', password='StrongPass12345')

        response = self.client.get(reverse('registration_requests'))

        self.assertEqual(response.status_code, 403)

    def test_superuser_can_view_registration_requests(self):
        superuser = User.objects.create_superuser(
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
        reviewer = User.objects.create_superuser(
            username='reviewer',
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

        response = self.client.post(
            reverse('approve_registration_request', args=[registration_request.id]),
            follow=True,
        )

        self.assertRedirects(response, reverse('registration_requests'))
        registration_request.refresh_from_db()
        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_APPROVED)
        self.assertEqual(registration_request.approved_by, reviewer)
        self.assertTrue(registration_request.invite_code_hash)
        self.assertNotIn(registration_request.invite_code_hash, mail.outbox[0].body)
        self.assertEqual(mail.outbox[0].to, ['reader@example.com'])
        self.assertContains(response, '已通过并发送注册码。')

    def test_email_failure_keeps_registration_request_pending(self):
        reviewer = User.objects.create_superuser(
            username='reviewer',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
        )
        self.client.login(username='reviewer', password='StrongPass12345')

        with patch('blog.views.approve_registration_request_service', side_effect=RuntimeError('smtp failed')):
            response = self.client.post(
                reverse('approve_registration_request', args=[registration_request.id]),
                follow=True,
            )

        registration_request.refresh_from_db()
        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_PENDING)
        self.assertEqual(registration_request.invite_code_hash, '')
        self.assertContains(response, '邮件发送失败，申请仍保持待审核。')

    def test_reject_registration_request_rejects_get(self):
        reviewer = User.objects.create_superuser(
            username='reviewer',
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
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
        )
        self.client.login(username='reviewer', password='StrongPass12345')

        response = self.client.post(
            reverse('reject_registration_request', args=[registration_request.id]),
            follow=True,
        )

        self.assertRedirects(response, reverse('registration_requests'))
        registration_request.refresh_from_db()
        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_REJECTED)
        self.assertEqual(registration_request.approved_by, reviewer)
        self.assertContains(response, '已拒绝这个注册申请。')
```

- [ ] **Step 2: Run review tests and verify they fail**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.AuthViewsTests.test_registration_requests_requires_superuser blog.tests.AuthViewsTests.test_superuser_can_view_registration_requests blog.tests.AuthViewsTests.test_approve_registration_request_rejects_get blog.tests.AuthViewsTests.test_superuser_can_approve_registration_request_and_send_email blog.tests.AuthViewsTests.test_email_failure_keeps_registration_request_pending blog.tests.AuthViewsTests.test_reject_registration_request_rejects_get blog.tests.AuthViewsTests.test_superuser_can_reject_registration_request
```

Expected: FAIL because the review URLs and views do not exist yet.

- [ ] **Step 3: Add view imports**

Update imports in `白车轴草/blog/views.py`.

```python
from django.http import HttpResponse, HttpResponseForbidden, JsonResponse
```

```python
from blog.registration_approval import (
    approve_registration_request as approve_registration_request_service,
)
```

- [ ] **Step 4: Add review views**

Place these views after `register`.

```python
def require_superuser(request):
    if request.user.is_authenticated and request.user.is_superuser:
        return None
    return HttpResponseForbidden('只有超级用户可以访问注册审核。')


@login_required
def registration_requests(request):
    forbidden_response = require_superuser(request)
    if forbidden_response:
        return forbidden_response

    requests_by_status = {
        status_value: RegistrationRequest.objects.filter(
            status=status_value,
        ).select_related('approved_by')
        for status_value, _ in RegistrationRequest.STATUS_CHOICES
    }
    pending_count = requests_by_status[RegistrationRequest.STATUS_PENDING].count()

    return render(request, 'registration_requests.html', {
        'requests_by_status': requests_by_status,
        'pending_count': pending_count,
        'status_choices': RegistrationRequest.STATUS_CHOICES,
    })


@login_required
@require_POST
def approve_registration_request(request, request_id):
    forbidden_response = require_superuser(request)
    if forbidden_response:
        return forbidden_response

    registration_request = get_object_or_404(RegistrationRequest, id=request_id)
    if registration_request.status != RegistrationRequest.STATUS_PENDING:
        messages.info(request, '只有待审核申请可以通过。')
        return redirect('registration_requests')

    completion_url = request.build_absolute_uri(reverse('complete_registration'))
    try:
        approve_registration_request_service(
            registration_request=registration_request,
            reviewer=request.user,
            completion_url=completion_url,
        )
    except Exception:
        messages.error(request, '邮件发送失败，申请仍保持待审核。')
        return redirect('registration_requests')

    messages.success(request, '已通过并发送注册码。')
    return redirect('registration_requests')


@login_required
@require_POST
def reject_registration_request(request, request_id):
    forbidden_response = require_superuser(request)
    if forbidden_response:
        return forbidden_response

    registration_request = get_object_or_404(RegistrationRequest, id=request_id)
    if registration_request.status != RegistrationRequest.STATUS_PENDING:
        messages.info(request, '只有待审核申请可以拒绝。')
        return redirect('registration_requests')

    registration_request.reject(request.user)
    registration_request.save(update_fields=[
        'status',
        'invite_code_hash',
        'code_expires_at',
        'approved_by',
        'reviewed_at',
        'used_at',
        'updated_at',
    ])
    messages.success(request, '已拒绝这个注册申请。')
    return redirect('registration_requests')
```

- [ ] **Step 5: Add routes**

Add these routes after the existing `register/` route in `白车轴草/白车轴草/urls.py`.

```python
    path('register/complete/', views.complete_registration, name='complete_registration'),
    path('registration-requests/', views.registration_requests, name='registration_requests'),
    path(
        'registration-requests/<int:request_id>/approve/',
        views.approve_registration_request,
        name='approve_registration_request',
    ),
    path(
        'registration-requests/<int:request_id>/reject/',
        views.reject_registration_request,
        name='reject_registration_request',
    ),
```

- [ ] **Step 6: Create the review template**

Create `白车轴草/blog/templates/registration_requests.html`.

```html
{% extends 'base.html' %}

{% block title %}注册审核 - 白车轴草{% endblock %}

{% block extra_css %}
<style>
    .review-page-header {
        background: linear-gradient(135deg, #e8f5e9 0%, #c8e6c9 100%);
        padding: 34px 0;
    }

    .review-page-header h1 {
        color: #1b5e20;
        font-size: 1.6rem;
        font-weight: 700;
        margin: 0;
    }

    .review-page-header p {
        color: #4a7c59;
        margin: 6px 0 0;
    }

    .review-table-card {
        background: #fff;
        border-radius: 12px;
        box-shadow: 0 2px 16px rgba(0, 0, 0, 0.06);
        overflow: hidden;
    }

    .review-table-card .table {
        margin-bottom: 0;
    }

    .review-status-title {
        color: #2e7d32;
        font-size: 1rem;
        font-weight: 700;
        margin: 28px 0 12px;
    }

    .review-actions {
        display: flex;
        gap: 8px;
        justify-content: flex-end;
    }
</style>
{% endblock %}

{% block content %}
<section class="review-page-header">
    <div class="container">
        <h1><i class="fas fa-user-check me-2"></i>注册审核</h1>
        <p>当前有 {{ pending_count }} 个待审核申请。</p>
    </div>
</section>

<main class="container py-4">
    {% for status_value, status_label in status_choices %}
    <h2 class="review-status-title">{{ status_label }}</h2>
    <div class="review-table-card">
        <div class="table-responsive">
            <table class="table align-middle">
                <thead>
                    <tr>
                        <th>邮箱</th>
                        <th>申请时间</th>
                        <th>审核人</th>
                        <th>审核时间</th>
                        <th>过期时间</th>
                        <th class="text-end">操作</th>
                    </tr>
                </thead>
                <tbody>
                    {% for registration_request in requests_by_status|get_item:status_value %}
                    <tr>
                        <td>{{ registration_request.email }}</td>
                        <td>{{ registration_request.created_at|date:'Y-m-d H:i' }}</td>
                        <td>{{ registration_request.approved_by.username|default:'-' }}</td>
                        <td>{{ registration_request.reviewed_at|date:'Y-m-d H:i'|default:'-' }}</td>
                        <td>{{ registration_request.code_expires_at|date:'Y-m-d H:i'|default:'-' }}</td>
                        <td>
                            {% if status_value == 'pending' %}
                            <div class="review-actions">
                                <form method="post" action="{% url 'approve_registration_request' registration_request.id %}">
                                    {% csrf_token %}
                                    <button type="submit" class="btn btn-sm btn-success">通过</button>
                                </form>
                                <form method="post" action="{% url 'reject_registration_request' registration_request.id %}">
                                    {% csrf_token %}
                                    <button type="submit" class="btn btn-sm btn-outline-danger">拒绝</button>
                                </form>
                            </div>
                            {% else %}
                            <span class="text-muted d-block text-end">-</span>
                            {% endif %}
                        </td>
                    </tr>
                    {% empty %}
                    <tr>
                        <td colspan="6" class="text-muted text-center py-4">暂无申请</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
    </div>
    {% endfor %}
</main>
{% endblock %}
```

- [ ] **Step 7: Add a template filter for dictionary lookup**

Create `白车轴草/blog/templatetags/blog_extras.py` if it does not already exist. If `白车轴草/blog/templatetags/` does not exist, create the directory and add an empty `__init__.py`.

```python
from django import template


register = template.Library()


@register.filter
def get_item(dictionary, key):
    return dictionary.get(key)
```

At the top of `registration_requests.html`, add this load line after `{% extends 'base.html' %}`.

```html
{% load blog_extras %}
```

- [ ] **Step 8: Add the superuser navigation link**

In `白车轴草/blog/templates/base.html`, add this item inside the authenticated dropdown before the logout link.

```html
                            {% if user.is_superuser %}
                            <li><a class="dropdown-item py-2" href="{% url 'registration_requests' %}"><i class="fas fa-user-check me-2"></i>注册审核</a></li>
                            {% endif %}
```

- [ ] **Step 9: Run review tests**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.AuthViewsTests.test_registration_requests_requires_superuser blog.tests.AuthViewsTests.test_superuser_can_view_registration_requests blog.tests.AuthViewsTests.test_approve_registration_request_rejects_get blog.tests.AuthViewsTests.test_superuser_can_approve_registration_request_and_send_email blog.tests.AuthViewsTests.test_email_failure_keeps_registration_request_pending blog.tests.AuthViewsTests.test_reject_registration_request_rejects_get blog.tests.AuthViewsTests.test_superuser_can_reject_registration_request
```

Expected: PASS.

- [ ] **Step 10: Commit**

Run:

```powershell
git add 白车轴草/blog/tests.py 白车轴草/blog/views.py 白车轴草/白车轴草/urls.py 白车轴草/blog/templates/registration_requests.html 白车轴草/blog/templates/base.html 白车轴草/blog/templatetags/__init__.py 白车轴草/blog/templatetags/blog_extras.py
git commit -m "feat: add registration review page"
```

---

### Task 5: Add Code-Based Registration Completion

**Files:**
- Modify: `白车轴草/blog/tests.py`
- Modify: `白车轴草/blog/forms.py`
- Modify: `白车轴草/blog/views.py`
- Modify: `白车轴草/白车轴草/urls.py`

- [ ] **Step 1: Add completion tests**

Add these methods to `AuthViewsTests`.

```python
    def make_approved_registration_request(self, email='reader@example.com', raw_invite_code='ABC123CODE456'):
        reviewer = User.objects.create_superuser(
            username='reviewer',
            password='StrongPass12345',
        )
        registration_request = RegistrationRequest.objects.create(
            email=email,
            status=RegistrationRequest.STATUS_APPROVED,
            approved_by=reviewer,
            reviewed_at=timezone.now(),
            code_expires_at=timezone.now() + timedelta(days=7),
        )
        registration_request.set_invite_code(raw_invite_code)
        registration_request.save()
        return registration_request

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
        user = User.objects.get(username='newreader')
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(user.email, 'reader@example.com')
        self.assertEqual(profile.nickname, '小草')
        self.assertEqual(self.client.session['_auth_user_id'], str(user.id))
        registration_request.refresh_from_db()
        self.assertEqual(registration_request.status, RegistrationRequest.STATUS_USED)
        self.assertIsNotNone(registration_request.used_at)

    def test_complete_registration_rejects_wrong_code(self):
        self.make_approved_registration_request()

        response = self.client.post(reverse('complete_registration'), {
            'email': 'reader@example.com',
            'invite_code': 'WRONGCODE999',
            'username': 'newreader',
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
            'password1': 'StrongPass12345',
            'password2': 'StrongPass12345',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newreader').exists())
        self.assertContains(response, '注册码已经过期。')

    def test_complete_registration_rejects_used_request(self):
        registration_request = self.make_approved_registration_request()
        registration_request.mark_used()
        registration_request.save(update_fields=['status', 'used_at', 'updated_at'])

        response = self.client.post(reverse('complete_registration'), {
            'email': 'reader@example.com',
            'invite_code': 'ABC123CODE456',
            'username': 'newreader',
            'password1': 'StrongPass12345',
            'password2': 'StrongPass12345',
        })

        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(username='newreader').exists())
        self.assertContains(response, '这个注册码不能使用。')

    def test_complete_registration_rejects_rejected_request(self):
        registration_request = RegistrationRequest.objects.create(
            email='reader@example.com',
            status=RegistrationRequest.STATUS_REJECTED,
            code_expires_at=timezone.now() + timedelta(days=7),
        )
        registration_request.set_invite_code('ABC123CODE456')
        registration_request.save()

        response = self.client.post(reverse('complete_registration'), {
            'email': 'reader@example.com',
            'invite_code': 'ABC123CODE456',
            'username': 'newreader',
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
            password='StrongPass12345',
        )

        response = self.client.post(reverse('complete_registration'), {
            'email': 'reader@example.com',
            'invite_code': 'ABC123CODE456',
            'username': 'newreader',
            'password1': 'StrongPass12345',
            'password2': 'StrongPass12345',
        })

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '这个用户名已经被注册。')
```

- [ ] **Step 2: Run completion tests and verify they fail**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.AuthViewsTests.test_complete_registration_with_valid_code_creates_and_logs_in_user blog.tests.AuthViewsTests.test_complete_registration_rejects_wrong_code blog.tests.AuthViewsTests.test_complete_registration_rejects_expired_code blog.tests.AuthViewsTests.test_complete_registration_rejects_used_request blog.tests.AuthViewsTests.test_complete_registration_rejects_rejected_request blog.tests.AuthViewsTests.test_complete_registration_rejects_duplicate_username
```

Expected: FAIL because `complete_registration` and `CompleteRegistrationForm` do not exist yet.

- [ ] **Step 3: Add completion form**

Add this form after `RegistrationRequestForm` in `白车轴草/blog/forms.py`.

```python
class CompleteRegistrationForm(UserCreationForm):
    email = forms.EmailField(
        label='邮箱',
        required=True,
        widget=forms.EmailInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入收到注册码的邮箱',
            'autocomplete': 'email',
        }),
        error_messages={
            'required': '请输入邮箱。',
            'invalid': '请输入有效的邮箱地址。',
        },
    )
    invite_code = forms.CharField(
        label='注册码',
        required=True,
        max_length=32,
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入邮件里的注册码',
            'autocomplete': 'one-time-code',
        }),
        error_messages={'required': '请输入注册码。'},
    )
    nickname = forms.CharField(
        label='昵称',
        required=False,
        max_length=50,
        help_text='可选。昵称会优先作为展示名称，留空则显示用户名。',
        widget=forms.TextInput(attrs={
            'class': 'form-control',
            'placeholder': '请输入昵称',
            'autocomplete': 'nickname',
            'maxlength': 50,
        }),
    )

    class Meta(UserCreationForm.Meta):
        model = User
        fields = ('email', 'invite_code', 'username', 'nickname', 'password1', 'password2')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.registration_request = None
        self.fields['username'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': '请输入用户名',
            'autocomplete': 'username',
        })
        self.fields['password1'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': '请输入密码',
            'autocomplete': 'new-password',
        })
        self.fields['password2'].widget.attrs.update({
            'class': 'form-control',
            'placeholder': '请再次输入密码',
            'autocomplete': 'new-password',
        })
        self.fields['username'].error_messages.update({
            'unique': '这个用户名已经被注册。',
        })

    def clean_email(self):
        email = RegistrationRequest.normalize_email(
            self.cleaned_data.get('email', ''),
        )
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('这个邮箱已经被注册。')
        return email

    def clean_invite_code(self):
        return (self.cleaned_data.get('invite_code') or '').strip().upper()

    def clean(self):
        cleaned_data = super().clean()
        email = cleaned_data.get('email')
        raw_invite_code = cleaned_data.get('invite_code')
        if not email or not raw_invite_code:
            return cleaned_data

        registration_request = RegistrationRequest.objects.filter(
            email__iexact=email,
        ).first()
        if not registration_request:
            raise forms.ValidationError('这个注册码不能使用。')
        if registration_request.status != RegistrationRequest.STATUS_APPROVED:
            raise forms.ValidationError('这个注册码不能使用。')
        if registration_request.is_code_expired:
            raise forms.ValidationError('注册码已经过期。')
        if not registration_request.check_invite_code(raw_invite_code):
            self.add_error('invite_code', '注册码不正确。')
            return cleaned_data

        self.registration_request = registration_request
        return cleaned_data

    def save(self, commit=True):
        user = super().save(commit=False)
        user.email = self.cleaned_data.get('email', '')
        if commit:
            user.save()
            UserProfile.objects.get_or_create(
                user=user,
                defaults={'nickname': self.cleaned_data.get('nickname', '')},
            )
            self.registration_request.mark_used()
            self.registration_request.save(update_fields=[
                'status',
                'used_at',
                'updated_at',
            ])
        return user
```

- [ ] **Step 4: Import completion form in views**

Update the forms import in `白车轴草/blog/views.py`.

```python
from blog.forms import (
    ChineseAuthenticationForm,
    ChineseUserCreationForm,
    CommentForm,
    CompleteRegistrationForm,
    PrivateMessageForm,
    RegistrationRequestForm,
    UserCenterForm,
)
```

- [ ] **Step 5: Add completion view**

Add this view after `register`.

```python
def complete_registration(request):
    if request.user.is_authenticated:
        return redirect('index')

    if request.method == 'POST':
        form = CompleteRegistrationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                user = form.save()
            login(request, user)
            messages.success(request, '注册成功，欢迎来到白车轴草。')
            return redirect('index')
    else:
        form = CompleteRegistrationForm()

    return render(request, 'auth_form.html', {
        'form': form,
        'page_title': '完成注册',
        'page_description': '输入邮件里的注册码，再设置账号信息。',
        'submit_text': '完成注册',
        'submit_icon': 'fas fa-user-check',
        'switch_text': '还没有注册码？',
        'switch_url_name': 'register',
        'switch_link_text': '先申请注册',
    })
```

- [ ] **Step 6: Ensure the completion route exists**

If Task 4 already added the route, do not add it again. If it is missing, add this after `register/` in `白车轴草/白车轴草/urls.py`.

```python
    path('register/complete/', views.complete_registration, name='complete_registration'),
```

- [ ] **Step 7: Run completion tests**

Run:

```powershell
python .\白车轴草\manage.py test blog.tests.AuthViewsTests.test_complete_registration_with_valid_code_creates_and_logs_in_user blog.tests.AuthViewsTests.test_complete_registration_rejects_wrong_code blog.tests.AuthViewsTests.test_complete_registration_rejects_expired_code blog.tests.AuthViewsTests.test_complete_registration_rejects_used_request blog.tests.AuthViewsTests.test_complete_registration_rejects_rejected_request blog.tests.AuthViewsTests.test_complete_registration_rejects_duplicate_username
```

Expected: PASS.

- [ ] **Step 8: Commit**

Run:

```powershell
git add 白车轴草/blog/tests.py 白车轴草/blog/forms.py 白车轴草/blog/views.py 白车轴草/白车轴草/urls.py
git commit -m "feat: complete registration with invite code"
```

---

### Task 6: Final Verification and Polish

**Files:**
- Modify only files already changed in Tasks 1-5 if verification exposes a concrete issue.

- [ ] **Step 1: Run Django checks**

Run:

```powershell
python .\白车轴草\manage.py check
```

Expected: `System check identified no issues`.

- [ ] **Step 2: Run all blog tests**

Run:

```powershell
python .\白车轴草\manage.py test blog
```

Expected: all tests PASS.

- [ ] **Step 3: Run migration consistency check**

Run:

```powershell
python .\白车轴草\manage.py makemigrations --check --dry-run
```

Expected: no changes detected.

- [ ] **Step 4: Browser-verify the registration request page**

Open:

```text
http://127.0.0.1:8000/register/
```

Expected:

- the page title is `申请注册`;
- the form has only the email field;
- submitting a valid email shows the waiting-for-approval message;
- no user is logged in.

- [ ] **Step 5: Browser-verify the completion page**

Open:

```text
http://127.0.0.1:8000/register/complete/
```

Expected:

- the page title is `完成注册`;
- the form has email, registration code, username, nickname, password, and confirmation fields.

- [ ] **Step 6: Browser-verify the review page as a superuser**

Log in as a superuser and open:

```text
http://127.0.0.1:8000/registration-requests/
```

Expected:

- pending requests are visible;
- approve and reject buttons are POST forms;
- approving prints an email to the runserver terminal when the console email backend is active;
- the request moves to approved after email output succeeds.

- [ ] **Step 7: Inspect git status**

Run:

```powershell
git status --short
```

Expected:

- only intentional files from this feature are modified;
- pre-existing untracked directories such as `docs/superpowers/mockups/` and `promo-video/` remain untracked and unstaged.

- [ ] **Step 8: Commit verification polish if any file changed**

If Step 1-7 required code or template fixes, commit only those files.

```powershell
git add 白车轴草/blog/tests.py 白车轴草/blog/models.py 白车轴草/blog/admin.py 白车轴草/blog/registration_approval.py 白车轴草/blog/forms.py 白车轴草/blog/views.py 白车轴草/白车轴草/urls.py 白车轴草/白车轴草/settings.py 白车轴草/blog/templates/auth_form.html 白车轴草/blog/templates/registration_requests.html 白车轴草/blog/templates/base.html 白车轴草/blog/templatetags/__init__.py 白车轴草/blog/templatetags/blog_extras.py
git commit -m "fix: polish registration approval flow"
```

If no files changed during verification, skip this commit.

- [ ] **Step 9: Push the branch**

Run:

```powershell
git push origin main
```

Expected: GitHub `main` receives all registration approval commits.

---

## Self-Review

- Spec coverage: the plan covers email-only request, no early `User`, superuser review page, one-time hashed code, 7-day expiry, console email default, environment SMTP settings, sender preference, POST-only review actions, completion registration, templates, tests, and migration checks.
- Scope check: this remains one feature because the model, email helper, public forms, review page, and completion flow are one registration pipeline.
- Type consistency: status constants, route names, helper names, form names, and test names are consistent across tasks.
