#!/usr/bin/env python3
"""
Script de diagnóstico — inspecciona SAAIO_PROCES en las tres CASA.GDB.

Uso:
    python check_saaio_proces.py
    python check_saaio_proces.py --patente 1627
"""
import os
import sys
import argparse
import configparser

BASE_DIR    = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(BASE_DIR, 'config.ini')

_cfg = configparser.ConfigParser()
for _enc in ('utf-8-sig', 'utf-8', 'latin-1', 'cp1252'):
    try:
        _cfg.read(CONFIG_FILE, encoding=_enc)
        break
    except UnicodeDecodeError:
        continue

def _get(section, key, fallback=''):
    return _cfg.get(section, key, fallback=fallback).strip()

FB_HOST     = _get('firebird', 'host',     'localhost')
FB_PORT     = int(_get('firebird', 'port') or '3050')
FB_USER     = _get('firebird', 'user',     'SYSDBA')
FB_PASSWORD = _get('firebird', 'password', 'masterkey')
FB_CHARSET  = 'WIN1252'

import fdb

fb_client = _get('firebird', 'fb_client_path')
if fb_client and os.path.exists(fb_client):
    fdb.load_api(fb_client)

def connect(db_path):
    return fdb.connect(
        host=FB_HOST, port=FB_PORT,
        database=db_path,
        user=FB_USER, password=FB_PASSWORD,
        charset=FB_CHARSET,
    )

def inspect(db_path, label):
    print(f'\n{"="*60}')
    print(f'  {label}  —  {db_path}')
    print(f'{"="*60}')
    try:
        con = connect(db_path)
        cur = con.cursor()
    except Exception as e:
        print(f'  ERROR al conectar: {e}')
        return

    # 1) Columnas de SAAIO_PROCES
    print('\n[1] Columnas de SAAIO_PROCES:')
    try:
        cur.execute("""
            SELECT rf.RDB$FIELD_NAME, tp.RDB$TYPE_NAME
            FROM RDB$RELATION_FIELDS rf
            JOIN RDB$FIELDS f   ON f.RDB$FIELD_NAME   = rf.RDB$FIELD_SOURCE
            JOIN RDB$TYPES  tp  ON tp.RDB$TYPE         = f.RDB$FIELD_TYPE
                               AND tp.RDB$FIELD_NAME   = 'RDB$FIELD_TYPE'
            WHERE rf.RDB$RELATION_NAME = 'SAAIO_PROCES'
            ORDER BY rf.RDB$FIELD_POSITION
        """)
        rows = cur.fetchall()
        if rows:
            for col, typ in rows:
                print(f'    {col.strip():<30} {typ.strip()}')
        else:
            print('    (sin resultados — ¿existe la tabla?)')
    except Exception as e:
        print(f'    ERROR: {e}')

    # 2) Muestra de 3 filas para ver valores reales
    print('\n[2] Primeras 3 filas de SAAIO_PROCES:')
    try:
        cur.execute("SELECT FIRST 3 * FROM SAAIO_PROCES")
        cols = [d[0].strip() for d in cur.description]
        rows = cur.fetchall()
        print('    Columnas:', cols)
        for r in rows:
            print('   ', dict(zip(cols, r)))
    except Exception as e:
        print(f'    ERROR: {e}')

    # 3) Muestra de filas recientes por FEC_MOD (si existe)
    print('\n[3] 3 filas más recientes por FEC_MOD (si existe):')
    try:
        cur.execute("""
            SELECT FIRST 3 NUM_REFE, FEC_MOD
            FROM SAAIO_PROCES
            WHERE FEC_MOD IS NOT NULL
            ORDER BY FEC_MOD DESC
        """)
        for r in cur.fetchall():
            print(f'    NUM_REFE={r[0]}  FEC_MOD={r[1]}')
    except Exception as e:
        print(f'    ERROR: {e}')

    con.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--patente', choices=['1627','1656','1927'],
                        help='Inspeccionar solo esta patente')
    args = parser.parse_args()

    db_path_single = _get('firebird', 'db_path')

    if db_path_single:
        patente = _get('servidor', 'patente') or '???'
        inspect(db_path_single, f'Patente {patente}')
    else:
        print('No se encontró db_path en config.ini')
        sys.exit(1)
