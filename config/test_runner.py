from django.test.runner import DiscoverRunner


class PoolerSafeTestRunner(DiscoverRunner):
    """Drop PostgreSQL test databases even when reached through a pooler.

    Supabase's session pooler (Supavisor) keeps a server connection open to
    the test database and reopens it immediately if terminated, so Django's
    plain DROP DATABASE fails with "being accessed by other users". Drop with
    FORCE (PostgreSQL 13+), which ends those sessions and drops atomically.
    """

    def teardown_databases(self, old_config, **kwargs):
        remaining = []
        for connection, old_name, destroy in old_config:
            if destroy and not self.keepdb and self.parallel <= 1 and connection.vendor == 'postgresql':
                test_name = connection.settings_dict['NAME']
                if self.verbosity >= 1:
                    print(f"Destroying test database for alias '{connection.alias}' ('{test_name}')...")
                connection.close()
                connection.settings_dict['NAME'] = old_name
                with connection._nodb_cursor() as cursor:
                    cursor.execute(
                        'DROP DATABASE IF EXISTS %s WITH (FORCE)' % connection.ops.quote_name(test_name)
                    )
            else:
                remaining.append((connection, old_name, destroy))
        super().teardown_databases(remaining, **kwargs)
