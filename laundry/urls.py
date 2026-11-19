from django.contrib.auth import views as auth_views
from django.urls import path

from . import views
from .forms import LoginForm

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
    path(
        "accounts/login/",
        auth_views.LoginView.as_view(authentication_form=LoginForm, redirect_authenticated_user=True),
        name="login",
    ),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/signup/", views.signup, name="signup"),
]
