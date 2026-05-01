import psycopg2
from psycopg2 import pool
from core.config import DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT

connection_pool = psycopg2.pool.ThreadedConnectionPool(
    minconn=2,
    maxconn=10,
    dbname=DB_NAME,
    user=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=DB_PORT,
)


def get_db():
    conn = connection_pool.getconn()
    try:
        yield conn
    finally:
        connection_pool.putconn(conn)