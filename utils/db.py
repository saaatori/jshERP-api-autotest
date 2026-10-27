"""MySQL 只读客户端:仅用于断言读取(需求分析 3.4-#11,清理一律走接口)。"""
import pymysql
from pymysql.cursors import DictCursor

from utils.logger import get_logger


class DB:
    def __init__(self, host, port, user, password, database):
        self.log = get_logger()
        self.conn = pymysql.connect(host=host, port=int(port), user=user, password=password,
                                    database=database, charset="utf8mb4",
                                    cursorclass=DictCursor, connect_timeout=10,
                                    autocommit=True)  # 不开 autocommit 会停在旧快照,读不到被测系统新写入的数据

    def query(self, sql, args=None):
        self.log.info("DB> %s %s", " ".join(sql.split()), args or "")
        with self.conn.cursor() as cur:
            cur.execute(sql, args)
            rows = cur.fetchall()
        self.log.info("DB< %d 行", len(rows))
        return rows

    def query_one(self, sql, args=None):
        rows = self.query(sql, args)
        return rows[0] if rows else None

    def close(self):
        self.conn.close()
