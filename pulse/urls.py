from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

urlpatterns = [
    # Guest app
    path("", views.identity_picker, name="identity-picker"),
    path("identities/new/", views.create_identity, name="create-identity"),
    path("r/<uuid:respondent_id>/", views.questionnaire, name="questionnaire"),
    path("r/<uuid:respondent_id>/answer/<int:question_id>/", views.answer_question, name="answer-question"),
    path("r/<uuid:respondent_id>/suggest/", views.suggest_question, name="suggest-question"),
    # Host console
    path("host/login/", auth_views.LoginView.as_view(template_name="pulse/host_login.html"), name="host-login"),
    path("host/logout/", auth_views.LogoutView.as_view(next_page="host-login"), name="host-logout"),
    path("host/", views.host_home, name="host-home"),
    path("host/screen/", views.screen_control, name="host-screen-control"),
    # Big screen
    path("screen/", views.screen_display, name="screen-display"),
    path("screen/state/", views.screen_state, name="screen-state"),
]
