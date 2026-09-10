def restore_checkpoint(saved_state, current_hash):
    """Resume a saved conversation only if the workspace content has not changed."""
    if saved_state["hash"] != current_hash:
        return {"status": "stale"}
    return saved_state
