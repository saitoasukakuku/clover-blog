from django.db import models
from django.contrib.auth.models import User
import re

class Post(models.Model):
    STATUS_CHOICES = (
        ('draft', '草稿'),
        ('published', '已发布'),
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
