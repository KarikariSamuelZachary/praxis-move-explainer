import logging
import psycopg2
from psycopg2 import pool
from core.config import DB_NAME, DB_USER, DB_PASSWORD, DB_HOST, DB_PORT

log = logging.getLogger(__name__)

connection_pool = None

def init_db():
    global connection_pool
    try:
        connection_pool = psycopg2.pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
        )
        log.info("Database connection pool created successfully")
    except Exception as e:
        log.error("Failed to connect to database: %s", e)
        raise


def get_db():
    conn = connection_pool.getconn()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        connection_pool.putconn(conn)
