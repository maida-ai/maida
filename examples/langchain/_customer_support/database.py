import os
import shutil
import sqlite3

import pandas as pd
import requests

import logging

logger = logging.getLogger(__name__)

# DB and backup live in this package dir so we can gitignore them
_DATA_DIR = os.path.dirname(os.path.abspath(__file__))
_DEFAULT_DB = os.path.join(_DATA_DIR, "travel2.sqlite")


def download_db(local_file: str | None = None):
    local_file = local_file or _DEFAULT_DB
    logger.info(f"Downloading database to {local_file}...")
    db_url = "https://storage.googleapis.com/benchmarks-artifacts/travel-db/travel2.sqlite"
    # The backup lets us restart for each tutorial section
    # backup_file = "travel2.backup.sqlite"
    backup_file = local_file.replace(".sqlite", ".backup.sqlite")
    overwrite = False
    if overwrite or not os.path.exists(local_file):
        response = requests.get(db_url)
        response.raise_for_status()  # Ensure the request was successful
        with open(local_file, "wb") as f:
            f.write(response.content)
        # Backup - we will use this to "reset" our DB in each section
        shutil.copy(local_file, backup_file)
    return local_file


# Convert the flights to present time for our tutorial
def update_dates(file, backup_file: str | None = None):
    logger.info(f"Converting dates in {file}...")
    backup_file = backup_file or file.replace(".sqlite", ".backup.sqlite")
    shutil.copy(backup_file, file)
    conn = sqlite3.connect(file)
    conn.cursor()

    tables = pd.read_sql("SELECT name FROM sqlite_master WHERE type='table';", conn).name.tolist()
    tdf = {}
    for t in tables:
        tdf[t] = pd.read_sql(f"SELECT * from {t}", conn)

    example_time = pd.to_datetime(tdf["flights"]["actual_departure"].replace("\\N", pd.NaT)).max()
    current_time = pd.to_datetime("now").tz_localize(example_time.tz)
    time_diff = current_time - example_time

    tdf["bookings"]["book_date"] = (
        pd.to_datetime(tdf["bookings"]["book_date"].replace("\\N", pd.NaT), utc=True) + time_diff
    )

    datetime_columns = [
        "scheduled_departure",
        "scheduled_arrival",
        "actual_departure",
        "actual_arrival",
    ]
    for column in datetime_columns:
        tdf["flights"][column] = pd.to_datetime(tdf["flights"][column].replace("\\N", pd.NaT)) + time_diff

    for table_name, df in tdf.items():
        df.to_sql(table_name, conn, if_exists="replace", index=False)
    del df
    del tdf
    conn.commit()
    conn.close()

    return file
