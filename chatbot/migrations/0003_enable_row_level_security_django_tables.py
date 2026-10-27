from django.db import migrations

# Django's built-in tables also live in Supabase's API-exposed public schema
# (auth_user holds password hashes, django_session holds session data).
# Enable RLS without policies so the anon and authenticated roles are denied;
# Django connects as the table owner and is unaffected.
TABLES = [
    'django_migrations',
    'django_content_type',
    'django_admin_log',
    'django_session',
    'auth_permission',
    'auth_group',
    'auth_group_permissions',
    'auth_user',
    'auth_user_groups',
    'auth_user_user_permissions',
]


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0002_enable_row_level_security'),
        ('admin', '0003_logentry_add_action_flag_choices'),
        ('auth', '0012_alter_user_first_name_max_length'),
        ('contenttypes', '0002_remove_content_type_name'),
        ('sessions', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;' for table in TABLES],
            reverse_sql=[f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;' for table in TABLES],
        ),
    ]
