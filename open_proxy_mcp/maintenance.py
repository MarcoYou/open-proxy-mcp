"""Short, authenticated drain leases; expiry reopens admission if the operator disappears."""
import time


class Maintenance:
    def __init__(self):
        self.until = 0.0
        self.active_posts = 0

    @property
    def draining(self):
        return time.monotonic() < self.until

    def set_drain(self, seconds):
        self.until = time.monotonic() + seconds if seconds else 0.0

    def stats(self):
        return {"draining": self.draining, "active_posts": self.active_posts,
                "lease_seconds": max(0, round(self.until - time.monotonic(), 1))}


maintenance = Maintenance()


class AdmissionMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        is_mcp = scope["type"] == "http" and scope.get("path", "").startswith("/mcp")
        if is_mcp and maintenance.draining:
            from starlette.responses import JSONResponse
            await JSONResponse({"error": "temporarily draining; retry shortly"},
                               status_code=503, headers={"Retry-After": "5"})(scope, receive, send)
            return
        tracked = is_mcp and scope.get("method") == "POST"
        if tracked:
            maintenance.active_posts += 1
        try:
            await self.app(scope, receive, send)
        finally:
            if tracked:
                maintenance.active_posts -= 1
