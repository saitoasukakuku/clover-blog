from datetime import timedelta
import secrets

from django.conf import settings
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone

from blog.models import RegistrationRequest
from blog.site_owner import get_site_owner


REGISTRATION_CODE_ALPHABET = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
REGISTRATION_CODE_LENGTH = 12
REGISTRATION_CODE_EXPIRATION_DAYS = 7
PREFERRED_SENDER_USERNAME = '白车轴草'


class RegistrationRequestAlreadyReviewed(Exception):
    pass


def generate_registration_code():
    return ''.join(
        secrets.choice(REGISTRATION_CODE_ALPHABET)
        for _ in range(REGISTRATION_CODE_LENGTH)
    )


def get_registration_sender_email():
    preferred_sender = User.objects.filter(
        username=PREFERRED_SENDER_USERNAME,
        is_superuser=True,
    ).first()
    if preferred_sender and preferred_sender.email:
        return preferred_sender.email

    site_owner = get_site_owner()
    if site_owner and site_owner.email:
        return site_owner.email

    return settings.DEFAULT_FROM_EMAIL


def send_registration_code_email(registration_request, raw_invite_code, completion_url):
    expires_at = timezone.localtime(registration_request.code_expires_at)
    formatted_expires_at = expires_at.strftime('%Y-%m-%d %H:%M')
    subject = '白车轴草注册邀请'
    body = (
        '你的白车轴草注册申请已通过审核。\n\n'
        f'邀请码：{raw_invite_code}\n'
        f'有效期至：{formatted_expires_at}\n'
        f'完成注册链接：{completion_url}\n\n'
        '该邀请码只能使用一次，请不要转发给他人。'
    )

    send_mail(
        subject,
        body,
        get_registration_sender_email(),
        [registration_request.email],
        fail_silently=False,
    )


def approve_registration_request(registration_request, reviewer, completion_url):
    raw_invite_code = generate_registration_code()

    with transaction.atomic():
        locked_request = type(registration_request).objects.select_for_update().get(
            pk=registration_request.pk,
        )
        if locked_request.status != locked_request.STATUS_PENDING:
            raise RegistrationRequestAlreadyReviewed

        locked_request.set_invite_code(raw_invite_code)
        locked_request.code_expires_at = (
            timezone.now() + timedelta(days=REGISTRATION_CODE_EXPIRATION_DAYS)
        )
        locked_request.approved_by = reviewer
        locked_request.reviewed_at = timezone.now()
        locked_request.used_at = None
        locked_request.status = RegistrationRequest.STATUS_APPROVED
        locked_request.save(
            update_fields=[
                'status',
                'invite_code_hash',
                'code_expires_at',
                'approved_by',
                'reviewed_at',
                'used_at',
                'updated_at',
            ]
        )

        send_registration_code_email(
            locked_request,
            raw_invite_code,
            completion_url,
        )

    return raw_invite_code


def reject_registration_request(registration_request, reviewer):
    with transaction.atomic():
        locked_request = type(registration_request).objects.select_for_update().get(
            pk=registration_request.pk,
        )
        if locked_request.status != locked_request.STATUS_PENDING:
            raise RegistrationRequestAlreadyReviewed

        locked_request.reject(reviewer)
        locked_request.save(
            update_fields=[
                'status',
                'invite_code_hash',
                'code_expires_at',
                'approved_by',
                'reviewed_at',
                'used_at',
                'updated_at',
            ]
        )
        return locked_request
