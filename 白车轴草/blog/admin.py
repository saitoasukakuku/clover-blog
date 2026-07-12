from django.contrib import admin

from blog.models import (
    Comment,
    BackgroundTask,
    FriendRequest,
    Friendship,
    Notification,
    Post,
    PostFavorite,
    PostLike,
    PostImage,
    PostReaction,
    PostRevision,
    PrivateMessage,
    RegistrationRequest,
    RateLimitState,
    UserBlock,
    UserProfile,
    Tag,
)


@admin.register(BackgroundTask)
class BackgroundTaskAdmin(admin.ModelAdmin):
    list_display = ('task_type', 'status', 'requested_by', 'created_at', 'started_at', 'finished_at')
    list_filter = ('task_type', 'status', 'created_at')
    search_fields = ('requested_by__username', 'output', 'error_message')
    readonly_fields = (
        'public_id',
        'task_type',
        'status',
        'requested_by',
        'output',
        'error_message',
        'created_at',
        'started_at',
        'finished_at',
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Tag)
class TagAdmin(admin.ModelAdmin):
    list_display = ('name', 'normalized_name', 'created_at')
    search_fields = ('name', 'normalized_name')
    readonly_fields = ('name', 'normalized_name', 'created_at')

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PostImage)
class PostImageAdmin(admin.ModelAdmin):
    list_display = ('original_name', 'owner', 'public_id', 'created_at')
    search_fields = ('original_name', 'owner__username', 'public_id')
    readonly_fields = ('public_id', 'created_at')
    ordering = ('-created_at',)


@admin.register(RateLimitState)
class RateLimitStateAdmin(admin.ModelAdmin):
    list_display = ('action', 'key_hash_preview', 'request_count', 'blocked_until', 'updated_at')
    list_filter = ('action', 'blocked_until')
    readonly_fields = ('action', 'key_hash', 'window_started_at', 'request_count', 'blocked_until', 'updated_at')
    ordering = ('-updated_at',)

    @admin.display(description='匿名键')
    def key_hash_preview(self, rate_state):
        return rate_state.key_hash[:12]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ('title', 'author', 'category', 'series_title', 'series_order', 'status', 'scheduled_publish_at', 'visibility', 'views_count', 'created_at', 'updated_at')
    list_filter = ('status', 'category', 'visibility', 'series_title', 'scheduled_publish_at', 'created_at')
    search_fields = ('title', 'content', 'tags', 'series_title')
    ordering = ('-created_at',)


@admin.register(PostRevision)
class PostRevisionAdmin(admin.ModelAdmin):
    list_display = ('post', 'title', 'editor', 'series_title', 'series_order', 'status', 'scheduled_publish_at', 'visibility', 'created_at')
    list_filter = ('status', 'visibility', 'series_title', 'scheduled_publish_at', 'created_at')
    search_fields = ('post__title', 'title', 'content', 'tags', 'series_title', 'editor__username')
    ordering = ('-created_at',)
    readonly_fields = (
        'post',
        'editor',
        'title',
        'category',
        'tags',
        'series_title',
        'series_order',
        'content',
        'status',
        'scheduled_publish_at',
        'visibility',
        'created_at',
    )


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'nickname', 'github_url', 'weibo_url', 'updated_at')
    search_fields = ('user__username', 'nickname', 'bio')
    ordering = ('user__username',)


@admin.register(RegistrationRequest)
class RegistrationRequestAdmin(admin.ModelAdmin):
    list_display = ('email', 'status', 'approved_by', 'code_expires_at', 'created_at', 'updated_at')
    list_filter = ('status', 'created_at', 'updated_at')
    search_fields = ('email', 'approved_by__username')
    readonly_fields = ('invite_code_hash', 'created_at', 'updated_at')
    ordering = ('-updated_at',)


@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('post', 'author', 'parent', 'is_hidden', 'moderated_by', 'content_preview', 'created_at')
    list_filter = ('is_hidden', 'created_at')
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


@admin.register(UserBlock)
class UserBlockAdmin(admin.ModelAdmin):
    list_display = ('blocker', 'blocked', 'created_at')
    search_fields = ('blocker__username', 'blocked__username')
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


@admin.register(PostFavorite)
class PostFavoriteAdmin(admin.ModelAdmin):
    list_display = ('user', 'post', 'created_at')
    search_fields = ('user__username', 'post__title')
    ordering = ('-created_at',)


@admin.register(PostLike)
class PostLikeAdmin(admin.ModelAdmin):
    list_display = ('user', 'post', 'created_at')
    search_fields = ('user__username', 'post__title')
    ordering = ('-created_at',)


@admin.register(PostReaction)
class PostReactionAdmin(admin.ModelAdmin):
    list_display = ('user', 'post', 'reaction_type', 'updated_at')
    list_filter = ('reaction_type', 'updated_at')
    search_fields = ('user__username', 'post__title')
    ordering = ('-updated_at',)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ('recipient', 'actor', 'notification_type', 'is_read', 'created_at')
    list_filter = ('notification_type', 'is_read', 'created_at')
    search_fields = ('recipient__username', 'actor__username', 'message')
    ordering = ('-created_at',)
