import sys
import unittest
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from indexing_window import IndexingWindow, NightlyScheduler  # noqa: E402

TZ = ZoneInfo("America/Santo_Domingo")


def at(day, hour, minute=0):
    return datetime(2026, 1, day, hour, minute, tzinfo=TZ)


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.window = IndexingWindow("22:00", "05:00", "America/Santo_Domingo")

    def test_crossing_midnight(self):
        self.assertTrue(self.window.is_open(at(10, 22)))
        self.assertTrue(self.window.is_open(at(10, 23, 59)))
        self.assertTrue(self.window.is_open(at(11, 0)))
        self.assertTrue(self.window.is_open(at(11, 4, 59)))
        self.assertFalse(self.window.is_open(at(11, 5)))
        self.assertFalse(self.window.is_open(at(11, 12)))
        self.assertFalse(self.window.is_open(at(10, 21, 59)))

    def test_same_day_window(self):
        window = IndexingWindow("01:00", "05:00", "America/Santo_Domingo")
        self.assertTrue(window.is_open(at(10, 3)))
        self.assertFalse(window.is_open(at(10, 23)))

    def test_converts_other_timezones(self):
        utc = datetime(2026, 1, 11, 3, 0, tzinfo=ZoneInfo("UTC"))  # 23:00 in Santo Domingo (UTC-4)
        self.assertTrue(self.window.is_open(utc))

    def test_night_id_after_midnight_belongs_to_previous_day(self):
        self.assertEqual(self.window.night_id(at(10, 23)), "2026-01-10")
        self.assertEqual(self.window.night_id(at(11, 2)), "2026-01-10")

    def test_invalid_time(self):
        with self.assertRaises(ValueError):
            IndexingWindow("late", "05:00")


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.calls = []
        window = IndexingWindow("22:00", "05:00", "America/Santo_Domingo")
        self.scheduler = NightlyScheduler(window, lambda cont: self.calls.append(cont))

    def test_no_run_outside_window(self):
        self.assertFalse(self.scheduler.tick(at(10, 14)))
        self.assertEqual(self.calls, [])

    def test_runs_once_per_night(self):
        self.assertTrue(self.scheduler.tick(at(10, 22, 1)))
        self.assertFalse(self.scheduler.tick(at(10, 23)))
        self.assertFalse(self.scheduler.tick(at(11, 2)))  # still the same night
        self.assertEqual(len(self.calls), 1)

    def test_runs_again_next_night(self):
        self.scheduler.tick(at(10, 22, 1))
        self.assertTrue(self.scheduler.tick(at(11, 22, 1)))
        self.assertEqual(len(self.calls), 2)

    def test_job_gets_window_check(self):
        self.scheduler.tick(at(10, 22, 1))
        self.assertTrue(callable(self.calls[0]))


class CutoffTest(unittest.TestCase):
    def test_docs_indexer_stops_when_window_closes(self):
        import indexer

        class Fake(indexer.KnowledgeBaseIndexer):
            def __init__(self):
                self.done = []
                self.db_schema, self.db_table = "s", "t"

            def index_file(self, f):
                self.done.append(f)
                return True

        answers = iter([True, True, False])
        fake = Fake()
        original = indexer.INDEXING_PAUSE_SECONDS
        indexer.INDEXING_PAUSE_SECONDS = 0
        try:
            out = fake.run_indexer(["a", "b", "c", "d"], lambda: next(answers))
        finally:
            indexer.INDEXING_PAUSE_SECONDS = original
        self.assertEqual(out, ["a", "b"])
        self.assertEqual(fake.deferred, 2)


if __name__ == "__main__":
    unittest.main()
