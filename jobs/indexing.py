import schedule
import time
from app.indexer import scheduled_indexing

def job():
    print("Starting indexing job...")
    scheduled_indexing()
    print("Indexing job completed.")

schedule.every(1).day.do(job)

while True:
    schedule.run_pending()
    time.sleep(1)