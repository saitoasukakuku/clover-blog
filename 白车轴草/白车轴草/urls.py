from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView
from blog import views
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('', RedirectView.as_view(pattern_name='index', permanent=False), name='home'),
    path('admin/', admin.site.urls),
    path('index/', views.index, name='index'),
    path('register/', views.register, name='register'),
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('user_center/', views.user_center, name='user_center'),
    path('index/create_post/', views.create_post, name='create_post'),
    path('drafts/', views.drafts_list, name='drafts'),
    path('edit_post/<int:post_id>/', views.edit_post, name='edit_post'),
    path('delete_draft/<int:post_id>/', views.delete_draft, name='delete_draft'),
    path('post/<int:post_id>/', views.post_detail, name='post_detail'),
    path('post/<int:post_id>/delete/', views.delete_post, name='delete_post'),
    path('rss.xml', views.rss_feed, name='rss_feed'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
