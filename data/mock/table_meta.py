"""샘플 테이블 메타데이터 — 메타 조회의 단일 진실 원천.

손으로 작성(베이스 코드 mock 패턴). 행수(row_count)는 stats — 실제 대량 행을 넣지
않고도 large-table(>LARGE_TABLE_THRESHOLD) 경로를 데모. redis backend는 seed_redis.py
가 이 dict를 그대로 적재하므로 두 backend가 동일 데이터.
"""
from typing import Dict

TABLE_META: Dict[str, dict] = {
    "users": {
        "name": "users",
        "columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "email", "type": "TEXT"},
            {"name": "name", "type": "TEXT"},
            {"name": "created_at", "type": "TEXT"},
        ],
        "indexes": [
            {"name": "pk_users", "columns": ["id"], "unique": True},
            {"name": "uq_users_email", "columns": ["email"], "unique": True},
        ],
        "row_count": 1_000,
    },
    "orders": {
        "name": "orders",
        "columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "user_id", "type": "INTEGER"},
            {"name": "status", "type": "TEXT"},
            {"name": "total", "type": "REAL"},
            {"name": "created_at", "type": "TEXT"},
        ],
        "indexes": [
            {"name": "pk_orders", "columns": ["id"], "unique": True},
            {"name": "idx_orders_user_id", "columns": ["user_id"], "unique": False},
            {"name": "idx_orders_created_at", "columns": ["created_at"], "unique": False},
        ],
        "row_count": 5_000_000,
    },
    "products": {
        "name": "products",
        "columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "sku", "type": "TEXT"},
            {"name": "name", "type": "TEXT"},
            {"name": "price", "type": "REAL"},
            {"name": "category", "type": "TEXT"},
        ],
        "indexes": [
            {"name": "pk_products", "columns": ["id"], "unique": True},
            {"name": "uq_products_sku", "columns": ["sku"], "unique": True},
            {"name": "idx_products_category", "columns": ["category"], "unique": False},
        ],
        "row_count": 50_000,
    },
    "order_items": {
        "name": "order_items",
        "columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "order_id", "type": "INTEGER"},
            {"name": "product_id", "type": "INTEGER"},
            {"name": "qty", "type": "INTEGER"},
        ],
        "indexes": [
            {"name": "pk_order_items", "columns": ["id"], "unique": True},
            {"name": "idx_order_items_order_id", "columns": ["order_id"], "unique": False},
            {"name": "idx_order_items_product_id", "columns": ["product_id"], "unique": False},
        ],
        "row_count": 12_000_000,
    },
    "audit_log": {
        "name": "audit_log",
        "columns": [
            {"name": "id", "type": "INTEGER"},
            {"name": "actor", "type": "TEXT"},
            {"name": "action", "type": "TEXT"},
            {"name": "payload", "type": "TEXT"},
            {"name": "created_at", "type": "TEXT"},
        ],
        "indexes": [
            {"name": "pk_audit_log", "columns": ["id"], "unique": True},
        ],
        "row_count": 8_000_000,
    },
}
