from io import StringIO
from unittest.mock import MagicMock, patch

from django.core.management import call_command

from searches.management.commands.research_benchmark import _benchmark_cases


class TestBenchmarkCases:
    def test_cases_cover_the_design_queries(self):
        cases = dict(_benchmark_cases())

        assert cases["housing_ca"].text == "housing"
        assert cases["housing_ca"].states == ("CA",)
        assert cases["housing_nationwide"].text == "housing"
        assert cases["five_word_phrase"].text.startswith('"')
        assert cases["housing_ca_modified"].modified_after is not None


class TestCommand:
    @patch("searches.management.commands.research_benchmark.connection")
    def test_runs_explain_for_each_case(self, mock_connection):
        mock_cursor = MagicMock()
        mock_connection.cursor.return_value.__enter__ = MagicMock(
            return_value=mock_cursor
        )
        mock_connection.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = [
            {
                "Plan": {
                    "Execution Time": 12.5,
                    "Plan Rows": 10,
                    "Shared Hit Blocks": 3,
                    "Shared Read Blocks": 1,
                }
            }
        ]

        out = StringIO()
        call_command("research_benchmark", stdout=out)

        sql = mock_cursor.execute.call_args[0][0]
        assert "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)" in sql
        assert "housing_ca" in out.getvalue()
        assert "housing_nationwide" in out.getvalue()
        assert "12.5" in out.getvalue()

    @patch("searches.management.commands.research_benchmark.connection")
    def test_with_insert_runs_insert_select(self, mock_connection):
        mock_cursor = MagicMock()
        mock_connection.cursor.return_value.__enter__ = MagicMock(
            return_value=mock_cursor
        )
        mock_connection.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cursor.fetchone.return_value = [
            {
                "Plan": {"Execution Time": 1.0, "Plan Rows": 1},
                "Execution Time": 1.0,
            }
        ]

        call_command("research_benchmark", "--with-insert", stdout=StringIO())

        executed = [call.args[0] for call in mock_cursor.execute.call_args_list]
        assert any("INSERT INTO research_bench_setitem" in sql for sql in executed)
