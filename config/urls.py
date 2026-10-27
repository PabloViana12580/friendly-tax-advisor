"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.2/topics/http/urls/
"""
from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

urlpatterns = [
    path('', include('chatbot.urls')),
    path(
        'accounts/login/',
        auth_views.LoginView.as_view(redirect_authenticated_user=True),
        name='login',
    ),
    # LogoutView only accepts POST, so logout is CSRF-protected.
    path('accounts/logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('admin/', admin.site.urls),
]
