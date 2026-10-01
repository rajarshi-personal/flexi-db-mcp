"""MongoDB adapter; no arbitrary database commands or server-side JavaScript."""

from .policy import PolicyError, validate_mongo


class MongoAdapter:
    def __init__(self, config, limits):
        from pymongo import MongoClient

        self.config, self.limits = config, limits
        self.client = MongoClient(
            config.url.get_secret_value(),
            maxPoolSize=config.pool_size,
            serverSelectionTimeoutMS=limits.timeout_seconds * 1000,
            timeoutMS=limits.timeout_seconds * 1000,
        )
        self.db = self.client[config.database]

    def close(self):
        self.client.close()

    def health(self):
        self.db.command("ping")
        return {"healthy": True, "dialect": "mongodb"}

    def collections(self):
        return self.db.list_collection_names()

    def describe(self, collection):
        return {"collection": collection, "indexes": list(self.db[collection].list_indexes())}

    def find(self, collection, filter=None, projection=None, limit=None):
        limit = self.limits.max_rows if limit is None else limit
        if not 1 <= limit <= self.limits.max_rows:
            raise PolicyError("Requested limit is outside configured bounds")
        validate_mongo([filter, projection])
        with (
            self.db[collection]
            .find(filter or {}, projection)
            .limit(limit + 1)
            .max_time_ms(self.limits.timeout_seconds * 1000) as cursor
        ):
            rows = list(cursor)
        return {"documents": rows[:limit], "truncated": len(rows) > limit}

    def aggregate(self, collection, pipeline):
        validate_mongo(pipeline)
        # A deliberately small stage vocabulary excludes nested collection access and writes.
        allowed = {
            "$match",
            "$project",
            "$group",
            "$sort",
            "$limit",
            "$skip",
            "$unwind",
            "$count",
            "$addFields",
            "$set",
            "$unset",
            "$replaceRoot",
        }
        if len(pipeline) > 30 or any(
            len(stage) != 1 or next(iter(stage)) not in allowed for stage in pipeline
        ):
            raise PolicyError("Unsupported aggregation stage or too many stages")
        with self.db[collection].aggregate(
            pipeline + [{"$limit": self.limits.max_rows + 1}],
            maxTimeMS=self.limits.timeout_seconds * 1000,
            allowDiskUse=False,
        ) as cursor:
            rows = list(cursor)
        return {
            "documents": rows[: self.limits.max_rows],
            "truncated": len(rows) > self.limits.max_rows,
        }

    def write(self, collection, operation, document=None, filter=None):
        if self.config.read_only:
            raise PolicyError("Writes are disabled for this database")
        validate_mongo([document, filter])
        target = self.db[collection]
        if operation == "insert_one":
            if not document:
                raise PolicyError("A nonempty document is required")
            return {"inserted_id": target.insert_one(document).inserted_id}
        if not filter:
            raise PolicyError("A nonempty filter is required")
        if operation == "update_one":
            if not document or any(key != "$set" for key in document):
                raise PolicyError("Updates require a $set document")
            result = target.update_one(filter, document)
            return {"matched": result.matched_count, "modified": result.modified_count}
        if operation == "delete_one":
            return {"deleted": target.delete_one(filter).deleted_count}
        raise PolicyError("Unsupported write operation")
