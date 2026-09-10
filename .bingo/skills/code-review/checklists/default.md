# Code review checklist

- Does the new behavior preserve existing public interfaces?
- Are stale cache entries, retries, and partial failures handled?
- Can a path, tool, or user-controlled value cross its intended boundary?
- Does the implementation fail clearly when optional dependencies are absent?
- Do tests exercise the behavior rather than mirror implementation details?
- Do trace and report artifacts explain the route and outcome?
