import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import audio_ingestion  # noqa: E402
import config  # noqa: E402
import indexer  # noqa: E402


class AudioChunkingTest(unittest.TestCase):
    def setUp(self):
        # Chunking needs no models or clients, skip the heavy __init__
        self.idx = object.__new__(audio_ingestion.KnowledgeBaseIndexer)
        self.meta = {"filepath": "downloads_audio/a.wav", "extension": "1034", "sentiment_score": 7}

    def test_items_without_timestamps_still_chunk(self):
        rows = self.idx.build_chunks([("hola", None, None)], self.meta)
        self.assertEqual(rows[0]["content"], "hola")
        self.assertNotIn("start_time", rows[0]["metadata"])

    def test_every_chunk_has_call_metadata_and_time_range(self):
        items = [(f"frase numero {i} " * 10, i * 10.0, i * 10.0 + 9) for i in range(30)]
        rows = self.idx.build_chunks(items, self.meta)

        self.assertGreater(len(rows), 1)
        for i, row in enumerate(rows):
            m = row["metadata"]
            self.assertEqual(m["extension"], "1034")  # incl. the last chunk
            self.assertEqual(m["sentiment_score"], 7)
            self.assertEqual((m["chunk_index"], m["chunk_total"]), (i, len(rows)))
            self.assertLess(m["start_time"], m["end_time"])
        self.assertEqual(rows[0]["metadata"]["start"], "00:00")
        self.assertTrue(rows[0]["content"].startswith("[00:00] frase numero 0"))
        self.assertNotIn("[", rows[0]["embedding_text"])  # embeddings get plain speech

    def test_chunk_size_is_bounded(self):
        items = [("x" * 500, 0.0, 1.0)] * 10
        for row in self.idx.build_chunks(items, self.meta):
            self.assertLess(len(row["embedding_text"]), audio_ingestion.CHUNK_CHARS + 600)

    def test_transcript_items_reads_asr_tracks_and_skips_empty(self):
        track = SimpleNamespace(start_time=1.5, end_time=4.0)
        doc = SimpleNamespace(
            texts=[
                SimpleNamespace(text=" hola ", source=[track]),
                SimpleNamespace(text="   ", source=[track]),
                SimpleNamespace(text="sin tiempo", source=[]),
            ]
        )
        self.assertEqual(
            self.idx.transcript_items(doc),
            [("hola", 1.5, 4.0), ("sin tiempo", None, None)],
        )


class DocsChunkingTest(unittest.TestCase):
    def test_markdown_chunks_keep_sections_and_table(self):
        md = (
            "# Politica de vacaciones\n\n"
            "## Solicitud\n\nLos empleados deben solicitar sus vacaciones con 15 dias de anticipacion "
            "a su supervisor directo y a gestion humana.\n\n"
            "## Tabla de dias\n\n| Antiguedad | Dias |\n|---|---|\n| 1 a 5 anos | 14 |\n| mas de 5 anos | 18 |\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "HR").mkdir()
            f = root / "HR" / "vacaciones.md"
            f.write_text(md)

            rows = indexer.KnowledgeBaseIndexer(root).build_chunks(f)

        self.assertTrue(rows)
        sections = [r["metadata"].get("section") for r in rows]
        self.assertTrue(any(s and "Solicitud" in s for s in sections), sections)
        for r in rows:
            self.assertEqual(r["metadata"]["category"], "HR")
            self.assertTrue(r["embedding_text"].startswith("vacaciones\n"))
            self.assertNotIn("page_no", r["metadata"])  # markdown has no pages; no fake page 1
        self.assertTrue(any("14" in r["content"] for r in rows))  # table survives


class FakeEmbeddings:
    def __init__(self, fail_on_call=None):
        self.calls, self.fail_on_call = [], fail_on_call

    def create(self, input, model):
        self.calls.append(list(input))
        if self.fail_on_call == len(self.calls):
            raise RuntimeError("rate limited")
        # return out of order to prove we sort by index
        data = [SimpleNamespace(index=i, embedding=[float(len(t))]) for i, t in enumerate(input)]
        return SimpleNamespace(data=list(reversed(data)))


class EmbeddingBatchTest(unittest.TestCase):
    def service(self, fake):
        svc = object.__new__(config.EmbeddingService)
        svc.service, svc.client = "openai", SimpleNamespace(embeddings=fake)
        return svc

    def test_batches_and_keeps_order(self):
        fake = FakeEmbeddings()
        texts = ["a" * (i + 1) for i in range(config.EMBED_BATCH_SIZE + 5)]
        vectors = self.service(fake).get_embeddings(texts)

        self.assertEqual([len(c) for c in fake.calls], [config.EMBED_BATCH_SIZE, 5])
        self.assertEqual(vectors, [[float(i + 1)] for i in range(len(texts))])

    def test_any_failed_batch_returns_none(self):
        fake = FakeEmbeddings(fail_on_call=2)
        texts = ["x"] * (config.EMBED_BATCH_SIZE + 1)
        self.assertIsNone(self.service(fake).get_embeddings(texts))

    def test_empty_input(self):
        self.assertEqual(self.service(FakeEmbeddings()).get_embeddings([]), [])


if __name__ == "__main__":
    unittest.main()
