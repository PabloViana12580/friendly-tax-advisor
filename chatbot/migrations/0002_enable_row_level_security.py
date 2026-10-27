from django.db import migrations

# Supabase exposes the public schema through its Data API to the anon and
# authenticated roles. Enabling RLS without policies denies those roles all
# access; Django connects as the table owner and is unaffected.
TABLES = ['chatbot_document', 'chatbot_documentpassage']


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql=[f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY;' for table in TABLES],
            reverse_sql=[f'ALTER TABLE {table} DISABLE ROW LEVEL SECURITY;' for table in TABLES],
        ),
    ]
