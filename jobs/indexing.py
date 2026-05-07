import schedule
import time
from app.indexer import scheduled_indexing
from app.audio_ingestion import scheduled_audio_indexing

def job():
    print("Starting indexing job...")
    scheduled_indexing()
    print("Indexing job completed.")

def audio_job():
    print("Starting audio ingestion job...")
    scheduled_audio_indexing()
    print("Audio ingestion completed.")

schedule.every(1).day.do(job)
schedule.every(1).day.do(audio_job)

while True:
    schedule.run_pending()
    time.sleep(1)