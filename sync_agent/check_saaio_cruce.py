#!/usr/bin/env python3
"""
DDL inspection script for SAAIO_CRUCE, SAAIO_CRUCED, SAAIO_CRUCEDVE tables.
Run with: python sync_agent/check_saaio_cruce.py
"""
import sys
from sync_agent import connect_fb, log


def check():
    conn = connect_fb()
    cur = conn.cursor()
    tables = ['SAAIO_CRUCE', 'SAAIO_CRUCED', 'SAAIO_CRUCEDVE']
    for t in tables:
        print(f"--- Table: {t} ---")
        cur.execute(f"SELECT FIRST 1 * FROM {t}")
        cols = [desc[0] for desc in cur.description]
        print("Columns:", cols)
    conn.close()


if __name__ == '__main__':
    check()