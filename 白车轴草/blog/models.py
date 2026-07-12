from django.contrib.auth.models import User
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
import re
import uuid

from blog.protected_storage import protected_media_storage


class Tag(models.Model):
    name = models.CharField(max_length=50, verbose_name='标签名')
    normalized_name = models.CharField(max_length=150, unique=True, verbose_name='规范名称')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')

    class Meta:
        ordering = ['name']
        verbose_name = '标签'
        verbose_name_plural = '标签'

    @staticmethod
    def normalize_name(raw_name):
        return (raw_name or '').strip().casefold()[:150]

    def save(self, *args, **kwargs):
        self.name = (self.name or '').strip()[:50]
        self.normalized_name = self.normalize_name(self.name)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name


class Post(models.Model):
    STATUS_CHOICES = (
        ('draft', '草稿'),
        ('published', '已发布'),
    )
    VISIBILITY_CHOICES = (
        ('private', '仅自己可见'),
        ('public', '公开'),
    )
    CATEGORY_CHOICES = (
        ('tech', '技术'),
        ('life', '生活随笔'),
        ('reading', '读书'),
        ('cycling', '骑行'),
        ('photography', '摄影'),
        ('travel', '旅行'),
        ('movie', '电影'),
        ('music', '音乐'),
        ('food', '美食'),
        ('study', '学习笔记'),
        ('project', '项目记录'),
        ('mood', '心情随记'),
    )
    CATEGORY_LABELS = dict(CATEGORY_CHOICES)

    author = models.ForeignKey(User, on_delete=models.CASCADE, null=True, blank=True, verbose_name='作者')
    title = models.CharField(max_length=200, verbose_name='文章标题')
    category = models.CharField(max_length=50, verbose_name='文章分类')
    tags = models.CharField(max_length=200, blank=True, verbose_name='文章标签')
    tag_objects = models.ManyToManyField(
        Tag,
        through='PostTag',
        related_name='posts',
        blank=True,
        verbose_name='规范标签',
    )
    series_title = models.CharField(
        max_length=100,
        blank=True,
        db_index=True,
        verbose_name='文章系列',
    )
    series_order = models.PositiveIntegerField(null=True, blank=True, verbose_name='系列顺序')
    cover = models.ImageField(
        storage=protected_media_storage,
        upload_to='covers/',
        null=True,
        blank=True,
        verbose_name='封面图片',
    )
    content = models.TextField(verbose_name='文章内容')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', verbose_name='状态')
    scheduled_publish_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        verbose_name='定时发布时间',
    )
    visibility = models.CharField(
        max_length=20,
        choices=VISIBILITY_CHOICES,
        default='private',
        verbose_name='可见范围',
    )
    views_count = models.PositiveIntegerField(default=0, verbose_name='浏览量')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    @property
    def tag_list(self):
        return [tag.strip() for tag in re.split(r'[,，;；\s]+', self.tags or '') if tag.strip()]

    @property
    def category_label(self):
        return self.CATEGORY_LABELS.get(self.category, self.category or '未分类')

    @property
    def cover_access_url(self):
        if not self.cover or not self.pk:
            return ''
        from django.urls import reverse
        return reverse('post_cover', args=[self.pk])

    def __str__(self):
        return self.title

    def save(self, *args, **kwargs):
        should_sync_tags = (
            self._state.adding
            or kwargs.get('update_fields') is None
            or 'tags' in kwargs.get('update_fields', ())
        )
        super().save(*args, **kwargs)
        if should_sync_tags:
            PostTag.sync_for_post(self)

    class Meta:
        indexes = [
            models.Index(
                fields=('status', 'visibility', '-created_at'),
                name='post_status_vis_created_idx',
            ),
            models.Index(
                fields=('author', 'status', '-created_at'),
                name='post_author_status_created_idx',
            ),
            models.Index(
                fields=('status', 'category', '-created_at'),
                name='post_category_created_idx',
            ),
        ]


class PostTag(models.Model):
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name='tag_links',
    )
    tag = models.ForeignKey(
        Tag,
        on_delete=models.CASCADE,
        related_name='post_links',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('post', 'tag'),
                name='unique_post_tag',
            ),
        ]
        indexes = [
            models.Index(fields=('tag', 'post'), name='posttag_tag_post_idx'),
        ]

    @classmethod
    def sync_for_post(cls, post):
        normalized_tags = {}
        for raw_tag in post.tag_list:
            tag_name = raw_tag[:50]
            normalized_name = Tag.normalize_name(tag_name)
            if normalized_name and normalized_name not in normalized_tags:
                normalized_tags[normalized_name] = tag_name

        tags = []
        for normalized_name, tag_name in normalized_tags.items():
            tag, _ = Tag.objects.get_or_create(
                normalized_name=normalized_name,
                defaults={'name': tag_name},
            )
            tags.append(tag)

        cls.objects.filter(post=post).exclude(tag__in=tags).delete()
        cls.objects.bulk_create(
            [cls(post=post, tag=tag) for tag in tags],
            ignore_conflicts=True,
        )


class PostRevision(models.Model):
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name='revisions',
        verbose_name='文章',
    )
    editor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='post_revisions',
        verbose_name='编辑者',
    )
    title = models.CharField(max_length=200, verbose_name='文章标题')
    category = models.CharField(max_length=50, verbose_name='文章分类')
    tags = models.CharField(max_length=200, blank=True, verbose_name='文章标签')
    series_title = models.CharField(max_length=100, blank=True, verbose_name='文章系列')
    series_order = models.PositiveIntegerField(null=True, blank=True, verbose_name='系列顺序')
    content = models.TextField(verbose_name='文章内容')
    status = models.CharField(
        max_length=20,
        choices=Post.STATUS_CHOICES,
        verbose_name='状态',
    )
    scheduled_publish_at = models.DateTimeField(null=True, blank=True, verbose_name='定时发布时间')
    visibility = models.CharField(
        max_length=20,
        choices=Post.VISIBILITY_CHOICES,
        verbose_name='可见范围',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='保存时间')

    class Meta:
        indexes = [
            models.Index(
                fields=('post', '-created_at'),
                name='postrev_post_created_idx',
            ),
        ]
        ordering = ['-created_at']
        verbose_name = '文章版本'
        verbose_name_plural = '文章版本'

    @classmethod
    def create_from_post(cls, post, editor):
        return cls.objects.create(
            post=post,
            editor=editor,
            title=post.title,
            category=post.category,
            tags=post.tags,
            series_title=post.series_title,
            series_order=post.series_order,
            content=post.content,
            status=post.status,
            scheduled_publish_at=post.scheduled_publish_at,
            visibility=post.visibility,
        )

    def __str__(self):
        return f'{self.post.title} 的历史版本：{self.title}'


def post_image_upload_path(post_image, file_name):
    file_extension = re.sub(r'[^a-z0-9.]', '', file_name.lower().rsplit('.', 1)[-1])
    safe_extension = file_extension if file_extension in {'jpg', 'jpeg', 'png', 'webp'} else 'jpg'
    return f'post_images/{post_image.owner_id}/{uuid.uuid4().hex}.{safe_extension}'


class PostImage(models.Model):
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='post_images',
        verbose_name='上传者',
    )
    image = models.ImageField(
        storage=protected_media_storage,
        upload_to=post_image_upload_path,
        verbose_name='图片',
    )
    original_name = models.CharField(max_length=255, blank=True, verbose_name='原文件名')
    posts = models.ManyToManyField(
        Post,
        related_name='body_images',
        blank=True,
        verbose_name='关联文章',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='上传时间')

    class Meta:
        indexes = [
            models.Index(fields=('owner', '-created_at'), name='postimage_owner_created_idx'),
        ]
        ordering = ['-created_at']
        verbose_name = '文章正文图片'
        verbose_name_plural = '文章正文图片'

    def __str__(self):
        return self.original_name or str(self.public_id)


class RateLimitState(models.Model):
    action = models.CharField(max_length=64, verbose_name='操作')
    key_hash = models.CharField(max_length=64, verbose_name='匿名键')
    window_started_at = models.DateTimeField(default=timezone.now, verbose_name='窗口开始时间')
    request_count = models.PositiveIntegerField(default=0, verbose_name='请求次数')
    blocked_until = models.DateTimeField(null=True, blank=True, verbose_name='阻止截止时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('action', 'key_hash'),
                name='unique_rate_limit_action_key',
            ),
        ]
        indexes = [
            models.Index(fields=('-updated_at',), name='ratelimit_updated_idx'),
        ]
        verbose_name = '请求限流状态'
        verbose_name_plural = '请求限流状态'

    def __str__(self):
        return f'{self.action}:{self.key_hash[:10]}'


class BackgroundTask(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_RUNNING = 'running'
    STATUS_SUCCEEDED = 'succeeded'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = (
        (STATUS_PENDING, '等待中'),
        (STATUS_RUNNING, '执行中'),
        (STATUS_SUCCEEDED, '已完成'),
        (STATUS_FAILED, '失败'),
    )
    TYPE_PREPARE_MUSIC = 'prepare_music_playback'
    TYPE_GENERATE_HOMEPAGE_COPY = 'generate_homepage_copy'
    TYPE_CHOICES = (
        (TYPE_PREPARE_MUSIC, '生成音乐播放版'),
        (TYPE_GENERATE_HOMEPAGE_COPY, '生成首页文案'),
    )

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    task_type = models.CharField(max_length=64, choices=TYPE_CHOICES, verbose_name='任务类型')
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING,
        verbose_name='状态',
    )
    requested_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='background_tasks',
        verbose_name='发起人',
    )
    output = models.TextField(blank=True, verbose_name='输出')
    error_message = models.TextField(blank=True, verbose_name='错误')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    started_at = models.DateTimeField(null=True, blank=True, verbose_name='开始时间')
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name='结束时间')

    class Meta:
        indexes = [
            models.Index(fields=('status', 'created_at'), name='bgtask_status_created_idx'),
        ]
        ordering = ['-created_at']
        verbose_name = '后台任务'
        verbose_name_plural = '后台任务'

    def __str__(self):
        return f'{self.get_task_type_display()} - {self.get_status_display()}'


class Comment(models.Model):
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name='comments',
        verbose_name='文章',
    )
    author = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='comments',
        verbose_name='评论者',
    )
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        related_name='replies',
        null=True,
        blank=True,
        verbose_name='回复的评论',
    )
    content = models.TextField(
        max_length=1000,
        verbose_name='评论内容',
    )
    is_hidden = models.BooleanField(default=False, verbose_name='已隐藏')
    moderated_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        related_name='moderated_comments',
        null=True,
        blank=True,
        verbose_name='审核人',
    )
    moderated_at = models.DateTimeField(null=True, blank=True, verbose_name='审核时间')
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='评论时间',
    )

    class Meta:
        indexes = [
            models.Index(
                fields=('post', 'is_hidden', '-created_at'),
                name='comment_post_hidden_idx',
            ),
        ]
        ordering = ['-created_at']
        verbose_name = '评论'
        verbose_name_plural = '评论'

    def __str__(self):
        return f'{self.author.username}：{self.content[:20]}'

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile', verbose_name='用户')
    avatar = models.ImageField(upload_to='avatars/', null=True, blank=True, verbose_name='头像')
    nickname = models.CharField(max_length=50, blank=True, verbose_name='昵称')
    bio = models.CharField(max_length=160, blank=True, verbose_name='个人简介')
    github_url = models.URLField(max_length=200, blank=True, verbose_name='GitHub 链接')
    weibo_url = models.URLField(max_length=200, blank=True, verbose_name='微博链接')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='创建时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    @property
    def display_name(self):
        return self.nickname or self.user.username

    def __str__(self):
        return self.display_name


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
    invite_code_hash = models.CharField(max_length=128, blank=True, verbose_name='邀请码哈希')
    code_expires_at = models.DateTimeField(null=True, blank=True, verbose_name='邀请码过期时间')
    approved_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='approved_registration_requests',
        verbose_name='审核人',
    )
    reviewed_at = models.DateTimeField(null=True, blank=True, verbose_name='审核时间')
    used_at = models.DateTimeField(null=True, blank=True, verbose_name='使用时间')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='申请时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        indexes = [
            models.Index(
                fields=('status', '-updated_at'),
                name='regreq_status_updated_idx',
            ),
        ]
        ordering = ['-updated_at']
        verbose_name = '注册申请'
        verbose_name_plural = '注册申请'

    @staticmethod
    def normalize_email(email):
        stripped_email = (email or '').strip()
        normalized_email = User.objects.normalize_email(stripped_email)
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

    def can_use_invite_code(self, raw_invite_code):
        return (
            self.status == self.STATUS_APPROVED
            and self.used_at is None
            and not self.is_code_expired
            and self.check_invite_code(raw_invite_code)
        )

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


class FriendRequest(models.Model):
    STATUS_CHOICES = (
        ('pending', '待处理'),
        ('accepted', '已接受'),
        ('rejected', '已拒绝'),
        ('cancelled', '已取消'),
    )

    sender = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='sent_friend_requests',
        verbose_name='申请人',
    )
    receiver = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='received_friend_requests',
        verbose_name='接收人',
    )
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='pending',
        verbose_name='状态',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='申请时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('sender', 'receiver'),
                name='unique_friend_request_direction',
            ),
            models.CheckConstraint(
                check=~models.Q(sender=models.F('receiver')),
                name='friend_request_users_differ',
            ),
        ]
        indexes = [
            models.Index(
                fields=('receiver', 'status', '-updated_at'),
                name='friendreq_recv_status_idx',
            ),
        ]
        ordering = ['-updated_at']
        verbose_name = '好友申请'
        verbose_name_plural = '好友申请'

    def __str__(self):
        return f'{self.sender.username} -> {self.receiver.username}'


class Friendship(models.Model):
    user_low = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='friendships_as_low',
        verbose_name='用户一',
    )
    user_high = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='friendships_as_high',
        verbose_name='用户二',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='成为好友时间')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('user_low', 'user_high'),
                name='unique_friendship_pair',
            ),
            models.CheckConstraint(
                check=models.Q(user_low__lt=models.F('user_high')),
                name='friendship_users_ordered',
            ),
        ]
        ordering = ['-created_at']
        verbose_name = '好友关系'
        verbose_name_plural = '好友关系'

    @classmethod
    def connect(cls, first_user, second_user):
        if first_user.id == second_user.id:
            raise ValueError('A user cannot befriend themselves.')
        user_low, user_high = sorted(
            (first_user, second_user),
            key=lambda user: user.id,
        )
        friendship, _ = cls.objects.get_or_create(
            user_low=user_low,
            user_high=user_high,
        )
        return friendship

    def __str__(self):
        return f'{self.user_low.username} ↔ {self.user_high.username}'

    def save(self, *args, **kwargs):
        if self.user_low_id and self.user_high_id and self.user_low_id > self.user_high_id:
            self.user_low_id, self.user_high_id = self.user_high_id, self.user_low_id
            self._state.fields_cache.pop('user_low', None)
            self._state.fields_cache.pop('user_high', None)
        super().save(*args, **kwargs)


class UserBlock(models.Model):
    blocker = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='blocking_users',
        verbose_name='屏蔽人',
    )
    blocked = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='blocked_by_users',
        verbose_name='被屏蔽人',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='屏蔽时间')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('blocker', 'blocked'),
                name='unique_user_block_direction',
            ),
            models.CheckConstraint(
                check=~models.Q(blocker=models.F('blocked')),
                name='user_block_users_differ',
            ),
        ]
        ordering = ['-created_at']
        verbose_name = '用户屏蔽'
        verbose_name_plural = '用户屏蔽'

    def __str__(self):
        return f'{self.blocker.username} 屏蔽 {self.blocked.username}'


class PrivateMessage(models.Model):
    sender = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='sent_private_messages',
        verbose_name='发送者',
    )
    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='received_private_messages',
        verbose_name='接收者',
    )
    content = models.TextField(max_length=2000, verbose_name='消息内容')
    is_read = models.BooleanField(default=False, verbose_name='已读')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='发送时间')

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=~models.Q(sender=models.F('recipient')),
                name='private_message_users_differ',
            ),
        ]
        indexes = [
            models.Index(
                fields=('recipient', 'is_read', 'created_at'),
                name='pm_rec_read_created_idx',
            ),
            models.Index(
                fields=('sender', 'recipient', 'created_at'),
                name='pm_pair_created_idx',
            ),
        ]
        ordering = ['created_at']
        verbose_name = '私信'
        verbose_name_plural = '私信'

    def __str__(self):
        return f'{self.sender.username} -> {self.recipient.username}: {self.content[:20]}'


class PostFavorite(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='post_favorites',
        verbose_name='收藏用户',
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name='favorites',
        verbose_name='收藏文章',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='收藏时间')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'post'),
                name='unique_post_favorite',
            ),
        ]
        indexes = [
            models.Index(
                fields=('user', '-created_at'),
                name='postfav_user_created_idx',
            ),
        ]
        ordering = ['-created_at']
        verbose_name = '文章收藏'
        verbose_name_plural = '文章收藏'

    def __str__(self):
        return f'{self.user.username} 收藏 {self.post.title}'


class PostLike(models.Model):
    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='post_likes',
        verbose_name='点赞用户',
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name='likes',
        verbose_name='点赞文章',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='点赞时间')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'post'),
                name='unique_post_like',
            ),
        ]
        ordering = ['-created_at']
        verbose_name = '文章点赞'
        verbose_name_plural = '文章点赞'

    def __str__(self):
        return f'{self.user.username} 点赞 {self.post.title}'


class PostReaction(models.Model):
    REACTION_CHOICES = (
        ('useful', '有用'),
        ('resonate', '共鸣'),
        ('inspired', '启发'),
        ('fun', '有趣'),
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='post_reactions',
        verbose_name='反应用户',
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.CASCADE,
        related_name='reactions',
        verbose_name='反应文章',
    )
    reaction_type = models.CharField(
        max_length=20,
        choices=REACTION_CHOICES,
        verbose_name='反应类型',
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='反应时间')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='更新时间')

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=('user', 'post'),
                name='unique_post_reaction',
            ),
        ]
        ordering = ['-updated_at']
        verbose_name = '文章表情反应'
        verbose_name_plural = '文章表情反应'

    def __str__(self):
        return f'{self.user.username} 对 {self.post.title} 标记 {self.get_reaction_type_display()}'


class Notification(models.Model):
    TYPE_CHOICES = (
        ('comment_on_post', '文章评论'),
        ('reply_to_comment', '评论回复'),
        ('mention', '提到我'),
        ('friend_request_received', '收到好友申请'),
        ('friend_request_accepted', '好友申请通过'),
        ('private_message', '私信'),
    )

    recipient = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name='notifications',
        verbose_name='接收者',
    )
    actor = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='sent_notifications',
        verbose_name='触发用户',
    )
    notification_type = models.CharField(
        max_length=40,
        choices=TYPE_CHOICES,
        verbose_name='通知类型',
    )
    post = models.ForeignKey(
        Post,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name='相关文章',
    )
    comment = models.ForeignKey(
        Comment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name='相关评论',
    )
    private_message = models.ForeignKey(
        PrivateMessage,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name='相关私信',
    )
    friend_request = models.ForeignKey(
        FriendRequest,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='notifications',
        verbose_name='相关好友申请',
    )
    message = models.CharField(max_length=255, verbose_name='通知内容')
    target_url = models.CharField(max_length=255, blank=True, verbose_name='跳转地址')
    is_read = models.BooleanField(default=False, verbose_name='已读')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='通知时间')

    class Meta:
        indexes = [
            models.Index(
                fields=('recipient', 'is_read', '-created_at'),
                name='notif_rec_read_created_idx',
            ),
            models.Index(
                fields=('recipient', 'notification_type', '-created_at'),
                name='notif_rec_type_created_idx',
            ),
        ]
        ordering = ['-created_at']
        verbose_name = '通知'
        verbose_name_plural = '通知'

    def __str__(self):
        return self.message
