"""Transactional decisions and undo; retrying a request never applies it twice."""

import json
import re

from ..gallery import PIPELINES, Gallery


class ReviewStore(Gallery):
    def validate(self, name, pipeline):
        if not re.fullmatch(r"[\w-]{1,80}", name, re.UNICODE):
            raise ValueError("Use a name containing letters, numbers, underscores or hyphens")
        if pipeline not in PIPELINES:
            raise ValueError("Unknown pipeline")

    def collection(self, name, pipeline, category="pending"):
        self.validate(name, pipeline)
        clauses = {
            "pending": "r.status IS NULL",
            "accepted": "r.status='accepted'",
            "rejected": "r.status='rejected'",
            "favorites": "r.favorite=1",
            "tshirt": "r.tshirt=1",
            "all": "1=1",
        }
        if category not in clauses:
            raise ValueError("Unknown category")
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO reviewers(name) VALUES(?)", (name,))
            rows = db.execute(
                "SELECT i.*,r.status,COALESCE(r.favorite,0) favorite,COALESCE(r.tshirt,0) tshirt "
                "FROM images i LEFT JOIN reviews r ON r.pipeline=i.pipeline AND r.hash=i.hash "
                "AND r.name=? WHERE i.pipeline=? AND "
                + clauses[category]
                + " ORDER BY i.created_at,i.hash",
                (name, pipeline),
            ).fetchall()
            counts = dict(
                db.execute(
                    "SELECT status,COUNT(*) FROM reviews WHERE name=? AND pipeline=? "
                    "GROUP BY status",
                    (name, pipeline),
                ).fetchall()
            )
            extras = db.execute(
                "SELECT COALESCE(SUM(favorite),0),COALESCE(SUM(tshirt),0) "
                "FROM reviews WHERE name=? AND pipeline=?",
                (name, pipeline),
            ).fetchone()
            counts.update(favorites=extras[0], tshirt=extras[1])
            total = db.execute(
                "SELECT COUNT(*) FROM images WHERE pipeline=?", (pipeline,)
            ).fetchone()[0]
        return {"images": [dict(row) for row in rows], "counts": counts, "total": total}

    def decide(self, name, pipeline, key, action, token):
        self.validate(name, pipeline)
        if action not in ("accept", "reject", "favorite", "tshirt", "undo", "reset"):
            raise ValueError("Unknown action")
        if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9-]{16,80}", token):
            raise ValueError("A request ID is required")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute(
                "SELECT 1 FROM requests WHERE name=? AND token=?", (name, token)
            ).fetchone():
                return
            db.execute("INSERT OR IGNORE INTO reviewers(name) VALUES(?)", (name,))
            if action == "reset":
                db.execute("DELETE FROM reviews WHERE name=? AND pipeline=?", (name, pipeline))
                db.execute("DELETE FROM events WHERE name=? AND pipeline=?", (name, pipeline))
            elif action == "undo":
                event = db.execute(
                    "SELECT * FROM events WHERE name=? AND pipeline=? AND undone=0 "
                    "ORDER BY id DESC LIMIT 1",
                    (name, pipeline),
                ).fetchone()
                if event:
                    db.execute(
                        "DELETE FROM reviews WHERE name=? AND pipeline=? AND hash=?",
                        (name, pipeline, event["hash"]),
                    )
                    previous = json.loads(event["previous"]) if event["previous"] else None
                    if previous:
                        db.execute(
                            "INSERT INTO reviews VALUES(?,?,?,?,?,?,?)", tuple(previous.values())
                        )
                    db.execute("UPDATE events SET undone=1 WHERE id=?", (event["id"],))
            else:
                if (
                    not isinstance(key, str)
                    or not db.execute(
                        "SELECT 1 FROM images WHERE pipeline=? AND hash=?", (pipeline, key)
                    ).fetchone()
                ):
                    raise ValueError("Unknown image")
                old = db.execute(
                    "SELECT * FROM reviews WHERE name=? AND pipeline=? AND hash=?",
                    (name, pipeline, key),
                ).fetchone()
                favorite, tshirt = (old["favorite"], old["tshirt"]) if old else (0, 0)
                if action == "reject":
                    favorite = tshirt = 0
                elif action == "favorite":
                    favorite = 1 - favorite
                elif action == "tshirt":
                    tshirt = 1 - tshirt
                db.execute(
                    "INSERT INTO events(name,pipeline,hash,previous) VALUES(?,?,?,?)",
                    (name, pipeline, key, json.dumps(dict(old)) if old else None),
                )
                db.execute(
                    "INSERT OR REPLACE INTO reviews VALUES(?,?,?,?,?,?,CURRENT_TIMESTAMP)",
                    (
                        name,
                        pipeline,
                        key,
                        "rejected" if action == "reject" else "accepted",
                        favorite,
                        tshirt,
                    ),
                )
            db.execute("INSERT INTO requests VALUES(?,?)", (name, token))
            db.execute("UPDATE reviewers SET updated_at=CURRENT_TIMESTAMP WHERE name=?", (name,))

    def sessions(self, pipeline):
        self.validate("valid", pipeline)
        with self.connect() as db:
            return [
                dict(row)
                for row in db.execute(
                    "SELECT u.name,u.updated_at,COUNT(r.hash) reviewed,"
                    "COALESCE(SUM(r.status='accepted'),0) accepted,"
                    "COALESCE(SUM(r.status='rejected'),0) rejected,"
                    "COALESCE(SUM(r.favorite),0) favorites,COALESCE(SUM(r.tshirt),0) tshirt "
                    "FROM reviewers u LEFT JOIN reviews r ON r.name=u.name AND r.pipeline=? "
                    "GROUP BY u.name ORDER BY u.updated_at DESC",
                    (pipeline,),
                )
            ]
