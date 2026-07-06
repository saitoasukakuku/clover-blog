from django.contrib.auth.models import User
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone
import re

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
    series_title = models.CharField(max_length=100, blank=True, verbose_name='文章系列')
    series_order = models.PositiveIntegerField(null=True, blank=True, verbose_name='系列顺序')
    cover = models.ImageField(upload_to='covers/', null=True, blank=True, verbose_name='封面图片')
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

    def __str__(self):
        return self.title

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
