from django.urls import path

from . import views

app_name = 'chatbot'

urlpatterns = [
    path('', views.workspace, name='workspace'),
    path('c/<uuid:conversation_id>/', views.workspace, name='conversation'),
    path('chat/ask/', views.ask, name='ask'),
]
