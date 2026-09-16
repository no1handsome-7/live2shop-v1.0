from __future__ import annotations
import psycopg

class Catalog:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def candidates(self, room_id: int) -> list[dict]:
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT i.item_id, i.title, i.category_id, i.category_path,
                           i.brand, i.style_tags, i.attributes, i.price_cent
                      FROM room_items ri
                      JOIN items i ON i.item_id=ri.item_id
                     WHERE ri.room_id=%s
                       AND i.status='ACTIVE'
                       AND (ri.active_from IS NULL OR ri.active_from <= now())
                       AND (ri.active_until IS NULL OR ri.active_until > now())
                     ORDER BY ri.sort_order, i.item_id
                     LIMIT 100
                    """,
                    (room_id,),
                )
                cols = [d.name for d in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]
