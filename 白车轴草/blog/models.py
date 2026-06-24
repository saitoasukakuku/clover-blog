from django.db import models
from django.contrib.auth.models import User
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
    cover = models.ImageField(upload_to='covers/', null=True, blank=True, verbose_name='封面图片')
    content = models.TextField(verbose_name='文章内容')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft', verbose_name='状态')
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
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='评论时间',
    )

    class Meta:
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
        ordering = ['created_at']
        verbose_name = '私信'
        verbose_name_plural = '私信'

    def __str__(self):
        return f'{self.sender.username} -> {self.recipient.username}: {self.content[:20]}'
