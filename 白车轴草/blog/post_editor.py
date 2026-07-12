import re
from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone

from blog.models import Post


CUSTOM_CATEGORY_VALUE = '__custom__'


@dataclass(frozen=True)
class PostSubmission:
    title: str
    category: str
    tags: str
    series_title: str
    series_order: int | None
    content: str
    status: str
    scheduled_publish_at: datetime | None
    visibility: str

    def as_model_values(self):
        return {
            'title': self.title,
            'category': self.category,
            'tags': self.tags,
            'series_title': self.series_title,
            'series_order': self.series_order,
            'content': self.content,
            'status': self.status,
            'scheduled_publish_at': self.scheduled_publish_at,
            'visibility': self.visibility,
        }


def parse_positive_series_order(raw_series_order, errors):
    cleaned_series_order = (raw_series_order or '').strip()
    if not cleaned_series_order:
        return None
    try:
        series_order = int(cleaned_series_order)
    except ValueError:
        errors.append('系列顺序必须是 1 到 9999 之间的整数。')
        return None
    if not 1 <= series_order <= 9999:
        errors.append('系列顺序必须是 1 到 9999 之间的整数。')
        return None
    return series_order


def parse_future_publish_time(raw_publish_time, errors):
    cleaned_publish_time = (raw_publish_time or '').strip()
    if not cleaned_publish_time:
        return None
    try:
        naive_publish_time = datetime.strptime(cleaned_publish_time, '%Y-%m-%dT%H:%M')
    except ValueError:
        errors.append('定时发布时间格式无效。')
        return None
    return timezone.make_aware(
        naive_publish_time,
        timezone.get_current_timezone(),
    )


def parse_post_submission(post_data):
    errors = []
    title = (post_data.get('title') or '').strip()
    if not title:
        errors.append('文章标题不能为空。')
    elif len(title) > 200:
        errors.append('文章标题不能超过 200 个字符。')

    selected_category = (post_data.get('category') or '').strip()
    if selected_category == CUSTOM_CATEGORY_VALUE:
        category = (post_data.get('custom_category') or '').strip()
        if not category:
            errors.append('请输入自定义分类。')
        elif len(category) > 50:
            errors.append('文章分类不能超过 50 个字符。')
    elif selected_category in Post.CATEGORY_LABELS:
        category = selected_category
    else:
        category = ''
        errors.append('请选择有效的文章分类。')

    tags = (post_data.get('tags') or '').strip()
    if len(tags) > 200:
        errors.append('文章标签总长度不能超过 200 个字符。')
    if any(
        len(tag) > 50
        for tag in re.split(r'[,，;；\s]+', tags)
        if tag
    ):
        errors.append('单个文章标签不能超过 50 个字符。')

    series_title = (post_data.get('series_title') or '').strip()
    if len(series_title) > 100:
        errors.append('文章系列名称不能超过 100 个字符。')
    series_order = parse_positive_series_order(post_data.get('series_order'), errors)

    action = (post_data.get('action') or 'draft').strip()
    if action not in {'draft', 'publish'}:
        errors.append('文章操作无效。')
        action = 'draft'

    visibility = (post_data.get('visibility') or 'private').strip()
    if visibility not in dict(Post.VISIBILITY_CHOICES):
        errors.append('请选择有效的可见范围。')
        visibility = 'private'

    content = post_data.get('content') or ''
    if action == 'publish' and not content.strip():
        errors.append('发布文章前请填写正文内容。')

    scheduled_publish_at = None
    if action == 'publish':
        requested_publish_at = parse_future_publish_time(
            post_data.get('scheduled_publish_at'),
            errors,
        )
        if requested_publish_at and requested_publish_at > timezone.now():
            status = 'draft'
            scheduled_publish_at = requested_publish_at
        else:
            status = 'published'
    else:
        status = 'draft'

    submission = PostSubmission(
        title=title,
        category=category,
        tags=tags,
        series_title=series_title,
        series_order=series_order,
        content=content,
        status=status,
        scheduled_publish_at=scheduled_publish_at,
        visibility=visibility,
    )
    return submission, errors
