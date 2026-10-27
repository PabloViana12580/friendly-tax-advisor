from django.db import migrations

# Same reasoning as 0002: keep Supabase's Data API (anon/authenticated roles)
# away from the chat log; Django connects as the table owner and is unaffected.
TABLES = ['chatbot_conversation', 'chatbot_chatexchange', 'chatbot_exchangecitation']


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0004_chat_log'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;' for table in TABLES],
            reverse_sql=[f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;' for table in TABLES],
        ),
    ]
