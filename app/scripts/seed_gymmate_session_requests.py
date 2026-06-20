"""Seed GymMate test data: random join requests against existing sessions.

Usage (from repo root):
    python -m app.scripts.seed_gymmate_session_requests \
        --target 37 --requests 8 --auto-accept 2

What it does:
    1. Finds the most-recent open future session(s) hosted by `--target`.
       If none exist, exits with a hint.
    2. Picks N distinct random clients (excluding the host) and inserts a
       pending row in gym_mate.session_request for each, with a random
       short message. UNIQUE(session_id, requester_client_id) makes the
       insert idempotent (duplicates are skipped).
    3. Optionally flips `--auto-accept` of those requests to 'accepted'
       and inserts the matching row in gym_mate.session_member so the
       host's matches list has data to show.

All inserts are INSERT IGNORE on the unique pair so re-running the
script is safe.
"""

import argparse
import random
from datetime import datetime

import pymysql


MYSQL_URL = {
    "host": "localhost",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "fittbot_local",
    "charset": "utf8mb4",
}


def _messages():
    return [
        "Up for a session?",
        "Looking for a leg day partner",
        "Same vibe — count me in",
        "Free tomorrow same time?",
        "Push day twin?",
        None, None,
        "Beginner-friendly please",
        "Will bring my own straps",
    ]


def fetch_open_future_sessions(conn, host_id: int, limit: int) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM gym_mate.session
            WHERE host_client_id = %s
              AND status = 'open'
              AND session_date >= CURDATE()
            ORDER BY session_date ASC, session_time ASC
            LIMIT %s
            """,
            (host_id, limit),
        )
        return [r[0] for r in cur.fetchall()]


def fetch_random_client_ids(conn, exclude_id: int, count: int) -> list[int]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT client_id FROM clients WHERE client_id <> %s "
            "ORDER BY RAND() LIMIT %s",
            (exclude_id, count),
        )
        return [r[0] for r in cur.fetchall()]


def insert_requests(
    conn, session_id: int, host_id: int, requester_ids: list[int], messages: list
) -> list[int]:
    """Returns the request_ids actually created (skips duplicates)."""
    created = []
    with conn.cursor() as cur:
        for rid in requester_ids:
            msg = random.choice(messages)
            cur.execute(
                """
                INSERT IGNORE INTO gym_mate.session_request
                  (session_id, requester_client_id, host_client_id,
                   message, status, created_at)
                VALUES (%s, %s, %s, %s, 'pending', NOW())
                """,
                (session_id, rid, host_id, msg),
            )
            if cur.rowcount > 0:
                created.append(cur.lastrowid)
    conn.commit()
    return created


def insert_host_member(conn, session_id: int, host_id: int) -> None:
    """Ensure the host is represented in session_member."""
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT IGNORE INTO gym_mate.session_member
              (session_id, client_id, role, joined_at)
            VALUES (%s, %s, 'host', NOW())
            """,
            (session_id, host_id),
        )
    conn.commit()


def accept_some(conn, request_ids: list[int], n: int) -> int:
    """Flip n requests to accepted + add session_member rows."""
    if n <= 0 or not request_ids:
        return 0
    pick = random.sample(request_ids, min(n, len(request_ids)))
    accepted = 0
    with conn.cursor() as cur:
        for rid in pick:
            cur.execute(
                "SELECT session_id, requester_client_id "
                "FROM gym_mate.session_request WHERE id = %s",
                (rid,),
            )
            row = cur.fetchone()
            if row is None:
                continue
            session_id, requester_client_id = row

            cur.execute(
                "UPDATE gym_mate.session_request "
                "SET status = 'accepted', responded_at = NOW() "
                "WHERE id = %s AND status = 'pending'",
                (rid,),
            )
            if cur.rowcount == 0:
                continue

            cur.execute(
                """
                INSERT IGNORE INTO gym_mate.session_member
                  (session_id, client_id, role, joined_at)
                VALUES (%s, %s, 'member', NOW())
                """,
                (session_id, requester_client_id),
            )
            accepted += 1
    conn.commit()
    return accepted


def fetch_session_owner(conn, session_id: int):
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, host_client_id, status, session_date "
            "FROM gym_mate.session WHERE id = %s",
            (session_id,),
        )
        return cur.fetchone()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", type=int, default=37,
                        help="host client_id whose sessions get requests")
    parser.add_argument("--sessions", type=int, default=1,
                        help="how many of the host's most-recent open sessions to seed")
    parser.add_argument("--session-id", type=int, default=None,
                        help="If set, seed against this specific session_id "
                             "instead of picking the host's most-recent.")
    parser.add_argument("--requests", type=int, default=8,
                        help="pending requests to add per session")
    parser.add_argument("--auto-accept", type=int, default=2,
                        help="of the new pending requests, how many to flip to accepted")
    args = parser.parse_args()

    conn = pymysql.connect(**MYSQL_URL)
    try:
        if args.session_id is not None:
            owner = fetch_session_owner(conn, args.session_id)
            if owner is None:
                raise SystemExit(f"session_id={args.session_id} not found.")
            _, host_id, status, sdate = owner
            if host_id != args.target:
                print(f"  ! session {args.session_id} is hosted by client {host_id}, "
                      f"using that as target (ignoring --target={args.target})")
            args.target = host_id
            print(f"Using session_id={args.session_id} "
                  f"(host={host_id}, status={status}, date={sdate})")
            sessions = [args.session_id]
        else:
            sessions = fetch_open_future_sessions(conn, args.target, args.sessions)
            if not sessions:
                raise SystemExit(
                    f"No open future sessions hosted by client_id={args.target}. "
                    "Create one via POST /api/v2/gym_mate/sessions first."
                )

        messages = _messages()
        for sid in sessions:
            print(f"Seeding requests for session_id={sid} (host={args.target})...")
            insert_host_member(conn, sid, args.target)

            requesters = fetch_random_client_ids(conn, args.target, args.requests * 2)
            requesters = [r for r in requesters if r != args.target][: args.requests]
            if len(requesters) < args.requests:
                print(f"  ! only {len(requesters)} eligible requesters available")

            created = insert_requests(conn, sid, args.target, requesters, messages)
            print(f"  inserted {len(created)} new pending requests")

            accepted = accept_some(conn, created, args.auto_accept)
            print(f"  flipped {accepted} to accepted (added to session_member)")

        print()
        print("Done. Now hit GET /api/v2/gym_mate/home (as client_id "
              f"{args.target}) to see the sessions block populate.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
