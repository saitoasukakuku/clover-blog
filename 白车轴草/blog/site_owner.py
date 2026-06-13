from django.contrib.auth.models import User

from blog.models import UserProfile


SITE_OWNER_USERNAME = 'root'


def get_site_owner():
    owner = User.objects.filter(username=SITE_OWNER_USERNAME).first()
    if owner:
        return owner
    return User.objects.filter(is_superuser=True).order_by('id').first()


def get_site_owner_profile():
    owner = get_site_owner()
    if not owner:
        return None, None
    profile, _ = UserProfile.objects.get_or_create(user=owner)
    return owner, profile
