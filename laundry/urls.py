from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("upload/", views.upload, name="upload"),
    path("settings/", views.profile_settings, name="settings"),
    path("cron/send-reminders/", views.cron_send_reminders, name="cron_send_reminders"),
    path("slips/<int:pk>/", views.slip_detail, name="slip_detail"),
    path("slips/<int:pk>/review/", views.review, name="review"),
    path("slips/<int:pk>/collect/", views.collect, name="collect"),
    path("slips/<int:pk>/resend/", views.resend_email, name="resend_email"),
    path("slips/<int:pk>/remind/", views.send_reminder, name="send_reminder"),
    path("slips/<int:pk>/delete/", views.slip_delete, name="slip_delete"),
    path("accounts/login/", views.login_page, name="login"),
    path("accounts/google/", views.google_login, name="google_login"),
    path("accounts/google/callback/", views.google_callback, name="google_callback"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
]
