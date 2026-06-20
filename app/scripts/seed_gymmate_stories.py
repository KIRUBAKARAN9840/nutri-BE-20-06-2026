"""Seed GymMate test data: friendships + stories for a target client.

Usage (from repo root):
    python -m app.scripts.seed_gymmate_stories \
        --target 37 --friends 10 --public 20

Defaults to client_id=37, 10 friends, 20 public stories.

What it inserts:
    1. `friendship` rows linking the target client to N random other clients
       (canonical pair: smaller id, larger id). INSERT IGNORE.
    2. `story` rows from those N friends with `audience='friends'`.
    3. `story` rows from M *other* random clients with `audience='public'`.
    Each story uses a Lorem-Picsum portrait image URL as the s3_key —
    `build_cdn_url` now passes such URLs through unchanged.

Idempotent on friendships (UNIQUE constraint). Story rows are always
inserted fresh (no dedup by content).
"""

import argparse
import random
import uuid
from datetime import datetime, timedelta

import pymysql

MYSQL_URL = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "fittbot_local",
    "charset": "utf8mb4",
}


def _portrait_url(seed: str) -> str:
    return f"https://picsum.photos/seed/{seed}/720/1280"


def _captions():
    return [
        None, None, None,
        "Push day complete",
        "Morning grind",
        "PR attempt",
        "Recovery walk",
        "New gear",
        "Form check",
        "Active rest",
        "Cardio finish",
    ]


def fetch_random_client_ids(conn, exclude_id: int, count: int) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT client_id FROM clients WHERE client_id <> %s "
            "ORDER BY RAND() LIMIT %s",
            (exclude_id, count),
        )
        return [r[0] for r in cur.fetchall()]


def upsert_friendships(conn, target_id: int, friend_ids: list[int]) -> int:
    inserted = 0
    with conn.cursor() as cur:
        for fid in friend_ids:
            a = min(target_id, fid)
            b = max(target_id, fid)
            cur.execute(
                "INSERT IGNORE INTO gym_mate.friendship (client_a_id, client_b_id) "
                "VALUES (%s, %s)",
                (a, b),
            )
            inserted += cur.rowcount
    conn.commit()
    return inserted


def insert_stories(
    conn,
    author_ids: list[int],
    audience: str,
    captions_pool: list,
) -> int:
    """For each author, insert 1-2 stories within the last 23h."""
    inserted = 0
    now = datetime.now()
    with conn.cursor() as cur:
        for author in author_ids:
            count_for_this_author = random.choice([1, 1, 1, 2])
            for _ in range(count_for_this_author):
                hours_ago = random.uniform(0, 22)
                created = now - timedelta(hours=hours_ago)
                expires = created + timedelta(hours=24)
                s3_key = _portrait_url(uuid.uuid4().hex)
                caption = random.choice(captions_pool)
                cur.execute(
                    """
                    INSERT INTO gym_mate.story
                      (client_id, media_type, s3_key, thumbnail_key,
                       caption, audience, created_at, expires_at,
                       is_deleted, deleted_at)
                    VALUES (%s, 'image', %s, NULL, %s, %s, %s, %s, FALSE, NULL)
                    """,
                    (author, s3_key, caption, audience, created, expires),
                )
                inserted += 1
    conn.commit()
    return inserted


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=37)
    parser.add_argument("--friends", type=int, default=10)
    parser.add_argument("--public", type=int, default=20)
    args = parser.parse_args()

    conn = pymysql.connect(**MYSQL_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM clients WHERE client_id = %s LIMIT 1",
                (args.target,),
            )
            if cur.fetchone() is None:
                raise SystemExit(f"client_id={args.target} not found in clients table")

        captions_pool = _captions()

        print(f"Picking {args.friends} clients to friend client_id={args.target}...")
        friend_ids = fetch_random_client_ids(conn, args.target, args.friends)
        if len(friend_ids) < args.friends:
            print(f"  ! only {len(friend_ids)} other clients available, using all")
        new_friendships = upsert_friendships(conn, args.target, friend_ids)
        print(f"  inserted {new_friendships} new friendship rows (dupes ignored)")

        print(f"Inserting stories for {len(friend_ids)} friends...")
        n_friend_stories = insert_stories(conn, friend_ids, "friends", captions_pool)
        print(f"  inserted {n_friend_stories} 'friends' stories")

        print(f"Picking {args.public} other random clients for public stories...")
        all_exclude = set(friend_ids) | {args.target}
        public_ids = []
        attempts = 0
        while len(public_ids) < args.public and attempts < 10:
            need = args.public - len(public_ids)
            batch = fetch_random_client_ids(conn, args.target, need * 2)
            for cid in batch:
                if cid not in all_exclude and cid not in public_ids:
                    public_ids.append(cid)
                    if len(public_ids) == args.public:
                        break
            attempts += 1

        if len(public_ids) < args.public:
            print(f"  ! only {len(public_ids)} eligible public authors found")
        n_public_stories = insert_stories(conn, public_ids, "public", captions_pool)
        print(f"  inserted {n_public_stories} 'public' stories")

        print()
        print("Done.")
        print(f"  target client_id: {args.target}")
        print(f"  friends added:    {len(friend_ids)} (new rows: {new_friendships})")
        print(f"  friend stories:   {n_friend_stories}")
        print(f"  public authors:   {len(public_ids)}")
        print(f"  public stories:   {n_public_stories}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
