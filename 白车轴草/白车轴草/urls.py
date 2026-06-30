from django.contrib import admin
from django.urls import path
from blog import views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', views.home, name='home'),
    path('admin/', admin.site.urls),
    path('index/', views.index, name='index'),
    path('archive/', views.archive_view, name='archive'),
    path('tags/', views.tags_view, name='tags'),
    path('users/<str:username>/', views.author_profile, name='author_profile'),
    path('register/', views.register, name='register'),
    path('registration-requests/', views.registration_requests, name='registration_requests'),
    path('registration-requests/<int:request_id>/approve/', views.approve_registration_request, name='approve_registration_request'),
    path('registration-requests/<int:request_id>/reject/', views.reject_registration_request, name='reject_registration_request'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('user_center/', views.user_center, name='user_center'),
    path('friends/', views.friends_view, name='friends'),
    path('friends/request/<int:user_id>/', views.send_friend_request, name='send_friend_request'),
    path('friends/request/<int:request_id>/accept/', views.accept_friend_request, name='accept_friend_request'),
    path('friends/request/<int:request_id>/reject/', views.reject_friend_request, name='reject_friend_request'),
    path('friends/request/<int:request_id>/cancel/', views.cancel_friend_request, name='cancel_friend_request'),
    path('friends/<int:user_id>/remove/', views.remove_friend, name='remove_friend'),
    path('messages/', views.conversations_view, name='conversations'),
    path('messages/<int:user_id>/', views.conversation_view, name='conversation'),
    path('favorites/', views.favorite_posts, name='favorite_posts'),
    path('notifications/', views.notifications_view, name='notifications'),
    path('notifications/<int:notification_id>/read/', views.read_notification, name='read_notification'),
    path('notifications/read_all/', views.mark_all_notifications_read, name='mark_all_notifications_read'),
    path('index/create_post/', views.create_post, name='create_post'),
    path('index/create_post/ai/', views.generate_ai_post, name='generate_ai_post'),
    path('drafts/', views.drafts_list, name='drafts'),
    path('edit_post/<int:post_id>/', views.edit_post, name='edit_post'),
    path('delete_draft/<int:post_id>/', views.delete_draft, name='delete_draft'),
    path('post/<int:post_id>/', views.post_detail, name='post_detail'),
    path('post/<int:post_id>/favorite/', views.toggle_favorite, name='toggle_favorite'),
    path('post/<int:post_id>/comment/', views.add_comment, name='add_comment'),
    path('comment/<int:comment_id>/delete/', views.delete_comment, name='delete_comment'),
    path('post/<int:post_id>/delete/', views.delete_post, name='delete_post'),
    path('rss.xml', views.rss_feed, name='rss_feed'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
