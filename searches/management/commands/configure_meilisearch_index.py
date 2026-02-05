"""
Management command to configure Meilisearch indexes.

This command applies the settings from MEILISEARCH_INDEX_SETTINGS to the
actual Meilisearch indexes, including searchable attributes, filters, sorting, etc.

Run this after:
- Initial setup
- Changing index settings in settings.py
- Enabling new features (synonyms, stop words, etc.)
"""

from django.core.management.base import BaseCommand

from searches.meilisearch_client import configure_index, get_meilisearch_client


class Command(BaseCommand):
    help = "Configure Meilisearch indexes with settings from Django settings"

    def add_arguments(self, parser):
        parser.add_argument(
            "--index",
            type=str,
            help="Specific index to configure (default: all indexes)",
        )

    def handle(self, *args, **options):
        from django.conf import settings

        index_name = options.get("index")

        self.stdout.write(
            self.style.WARNING(
                "=" * 80 + "\nConfigure Meilisearch Indexes\n" + "=" * 80
            )
        )

        # Test connection
        try:
            client = get_meilisearch_client()
            health = client.health()
            self.stdout.write(
                self.style.SUCCESS(f"✓ Connected to Meilisearch: {health}\n")
            )
        except Exception as e:
            self.stdout.write(
                self.style.ERROR(f"✗ Failed to connect to Meilisearch: {e}\n")
            )
            return

        # Determine which indexes to configure
        if index_name:
            if index_name not in settings.MEILISEARCH_INDEX_SETTINGS:
                self.stdout.write(
                    self.style.ERROR(
                        f'✗ Index "{index_name}" not found in MEILISEARCH_INDEX_SETTINGS\n'
                    )
                )
                return
            indexes_to_configure = {
                index_name: settings.MEILISEARCH_INDEX_SETTINGS[index_name]
            }
        else:
            indexes_to_configure = settings.MEILISEARCH_INDEX_SETTINGS

        # Configure each index
        for idx_key, idx_settings in indexes_to_configure.items():
            self.stdout.write(f"\nConfiguring index: {idx_key}")

            try:
                task = configure_index(idx_key)
                task_uid = getattr(
                    task, "task_uid", getattr(task, "taskUid", "unknown")
                )
                self.stdout.write(
                    self.style.SUCCESS(f"  ✓ Configuration queued (task {task_uid})")
                )

                # Show what was configured
                self.stdout.write("  Settings applied:")
                if "searchableAttributes" in idx_settings:
                    self.stdout.write(
                        f"    • Searchable: {', '.join(idx_settings['searchableAttributes'])}"
                    )
                if "filterableAttributes" in idx_settings:
                    self.stdout.write(
                        f"    • Filterable: {', '.join(idx_settings['filterableAttributes'])}"
                    )
                if "sortableAttributes" in idx_settings:
                    self.stdout.write(
                        f"    • Sortable: {', '.join(idx_settings['sortableAttributes'])}"
                    )
                if "typoTolerance" in idx_settings:
                    typo = idx_settings["typoTolerance"]
                    enabled = typo.get("enabled") if isinstance(typo, dict) else False
                    self.stdout.write(
                        f"    • Typo tolerance: {'enabled' if enabled else 'disabled'}"
                    )

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"  ✗ Failed to configure {idx_key}: {e}")
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n{'=' * 80}\nConfiguration complete!\n"
                "Note: Meilisearch processes settings asynchronously.\n"
                "Large indexes may take a few minutes to update.\n"
                f"{'=' * 80}\n"
            )
        )
