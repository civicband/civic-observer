"""Measure the queries Research sets will depend on.

Run against a production-sized database copy:

    python manage.py research_benchmark --limit 100 --with-insert

Every PR touching build_where or the materialization SQL should quote the
before/after numbers this prints.
"""

from datetime import UTC, datetime, timedelta

from django.core.management.base import BaseCommand
from django.db import connection

from searches.queryspec import QuerySpec, build_where


def _benchmark_cases() -> list[tuple[str, QuerySpec]]:
    cutoff = datetime.now(tz=UTC) - timedelta(days=30)
    return [
        ("housing_ca", QuerySpec(text="housing", states=("CA",))),
        ("housing_nationwide", QuerySpec(text="housing")),
        ("five_word_phrase", QuerySpec(text='"public hearing on housing policy"')),
        (
            "housing_ca_modified",
            QuerySpec(text="housing", states=("CA",), modified_after=cutoff),
        ),
    ]


def _plan_json(cursor):
    row = cursor.fetchone()
    plan = row[0]
    if isinstance(plan, list):
        plan = plan[0]
    return plan


class Command(BaseCommand):
    help = "Benchmark the queries and insert path Research sets depend on."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument(
            "--with-insert",
            action="store_true",
            help="Also time INSERT ... SELECT into a throwaway temp table.",
        )

    def handle(self, *args, **options):
        limit = options["limit"]
        rows = []
        for name, spec in _benchmark_cases():
            rows.append(self._run_case(name, spec, limit))

        if options["with_insert"]:
            rows.append(self._run_insert_case())

        self.stdout.write(
            f"{'case':<24} {'exec_ms':>10} {'plan_rows':>10} {'hit':>8} {'read':>8}"
        )
        for row in rows:
            self.stdout.write(
                f"{row['name']:<24} {row['exec_ms']:>10.2f}"
                f" {row['plan_rows']:>10} {row['hit']:>8} {row['read']:>8}"
            )

    def _run_case(self, name, spec, limit):
        where_sql, params = build_where(spec)
        sql = f"""
            EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
            SELECT id FROM meetings_meetingpage mp
            WHERE {where_sql}
            ORDER BY paradedb.score(id) DESC, meeting_date DESC, id
            LIMIT %s
        """
        with connection.cursor() as cursor:
            cursor.execute(sql, params + [limit])
            plan = _plan_json(cursor)

        plan_data = plan["Plan"]
        return {
            "name": name,
            "exec_ms": plan.get("Execution Time", plan_data.get("Execution Time", 0.0)),
            "plan_rows": plan_data.get("Plan Rows", 0),
            "hit": plan_data.get("Shared Hit Blocks", 0),
            "read": plan_data.get("Shared Read Blocks", 0),
        }

    def _run_insert_case(self):
        _, spec = _benchmark_cases()[0]
        where_sql, params = build_where(spec)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                CREATE TEMP TABLE IF NOT EXISTS research_bench_setitem (
                    result_set_id uuid,
                    page_id varchar(255),
                    rank double precision,
                    meeting_date date,
                    municipality_id uuid,
                    state varchar(64),
                    document_type varchar(32),
                    disposition char(1),
                    added_via varchar(16),
                    added_at timestamptz,
                    UNIQUE (result_set_id, page_id)
                )
                """
            )
            cursor.execute(
                f"""
                EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
                INSERT INTO research_bench_setitem
                SELECT '00000000-0000-0000-0000-000000000001'::uuid,
                       mp.id, paradedb.score(id), mp.meeting_date,
                       mp.municipality_id, mp.state, mp.document_type,
                       'u', 'query', now()
                FROM meetings_meetingpage mp
                WHERE {where_sql}
                ON CONFLICT DO NOTHING
                """,
                params,
            )
            plan = _plan_json(cursor)
            cursor.execute("DROP TABLE research_bench_setitem")

        plan_data = plan["Plan"]
        return {
            "name": "housing_ca_insert",
            "exec_ms": plan.get("Execution Time", plan_data.get("Execution Time", 0.0)),
            "plan_rows": plan_data.get("Actual Rows", plan_data.get("Plan Rows", 0)),
            "hit": plan_data.get("Shared Hit Blocks", 0),
            "read": plan_data.get("Shared Read Blocks", 0),
        }
