def evict_expired_entries(cache, now):
    """Remove cached values that have exceeded their expiration time."""
    return {key: value for key, value in cache.items() if value["expires_at"] > now}
