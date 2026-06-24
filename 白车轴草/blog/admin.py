from django.contrib import admin

from blog.models import (
    Comment,
    FriendRequest,
    Friendship,
    Post,
    PrivateMessage,
    UserProfile,
)


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'category', 'status', 'visibility', 'views_count', 'created_at', 'updated_at')
    list_filter = ('status', 'category', 'visibility', 'created_at')
    search_fields = ('title', 'content', 'tags')
    ordering = ('-created_at',)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'nickname', 'github_url', 'weibo_url', 'updated_at')
    search_fields = ('user__username', 'nickname', 'bio')
    ordering = ('user__username',)


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('post', 'author', 'parent', 'content_preview', 'created_at')
    list_filter = ('created_at',)
    search_fields = ('content', 'author__username', 'post__title')
    ordering = ('-created_at',)

    @admin.display(description='评论内容')
    def content_preview(self, comment):
        return comment.content[:40]


@admin.register(FriendRequest)
class FriendRequestAdmin(admin.ModelAdmin):
    list_display = ('sender', 'receiver', 'status', 'updated_at')
    list_filter = ('status', 'updated_at')
    search_fields = ('sender__username', 'receiver__username')
    ordering = ('-updated_at',)


@admin.register(Friendship)
class FriendshipAdmin(admin.ModelAdmin):
    list_display = ('user_low', 'user_high', 'created_at')
    search_fields = ('user_low__username', 'user_high__username')
    ordering = ('-created_at',)


@admin.register(PrivateMessage)
class PrivateMessageAdmin(admin.ModelAdmin):
    list_display = ('sender', 'recipient', 'content_preview', 'is_read', 'created_at')
    list_filter = ('is_read', 'created_at')
    search_fields = ('content', 'sender__username', 'recipient__username')
    ordering = ('-created_at',)

    @admin.display(description='消息内容')
    def content_preview(self, private_message):
        return private_message.content[:40]
