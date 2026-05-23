EventBus + ConnectionRegistry — Important Edge Cases

PROBLEM: Redis is down when the app starts
HOW WE HANDLE:
  - EventBus.connect() raises immediately
  - Startup fails fast instead of hiding the problem
  - The app does not pretend real-time delivery is available

PROBLEM: Redis drops during publish or subscribe
HOW WE HANDLE:
  - Publish errors are logged and the call returns
  - Subscribe errors end the generator cleanly
  - Existing WebSocket connections are not taken down by one failed Redis call

PROBLEM: A WebSocket disconnects during broadcast
HOW WE HANDLE:
  - Each socket send is isolated
  - One failed socket does not stop delivery to the others
  - Dead sockets are removed after the broadcast finishes

PROBLEM: One user has multiple active devices or tabs
HOW WE HANDLE:
  - ConnectionRegistry stores a list per user_id
  - Every active socket for that user gets the same message
  - No extra coordination is needed in the endpoint

PROBLEM: A user disconnects while a broadcast is in progress
HOW WE HANDLE:
  - broadcast() works from a snapshot of the socket list
  - Disconnects do not mutate the active send loop
  - The next broadcast sees the updated state

PROBLEM: Two subscriptions exist for the same user
HOW WE HANDLE:
  - Each subscribe() call creates its own pubsub listener
  - Both listeners receive the same user-scoped event stream
  - This matches a multi-tab or multi-device workflow

PROBLEM: No event arrives for a while
HOW WE HANDLE:
  - subscribe() yields None on idle timeout
  - The caller can treat that as a heartbeat or stale connection signal
  - The generator stays safe to keep polling

PROBLEM: broadcast() is called for a user with no sockets
HOW WE HANDLE:
  - The call returns without error
  - Empty users do not create noise in logs
  - The rest of the system keeps moving

PROBLEM: The app shuts down while subscriptions are active
HOW WE HANDLE:
  - Lifespan shutdown closes the Redis connection
  - Active pubsub listeners exit through cleanup
  - No manual cleanup is needed in endpoint code

PROBLEM: Payloads are empty, large, or contain unicode
HOW WE HANDLE:
  - JSON serialization handles normal dict payloads
  - Empty payloads pass through unchanged
  - Unicode and large strings are left to Redis and WebSocket transport limits

SSE Notifications — Edge Cases 

This document lists the important edge cases discovered while implementing the SSE notifications stream, the expected behavior, and how the code handles each.

1) Malformed `Last-Event-ID`
- Problem: Clients may send invalid/non-UUID values in `Last-Event-ID`.
- Risk: Parsing errors causing the stream to fail or crash.
- Handling: Server validates and attempts to parse as UUID. On parse failure, the header is ignored and normal live-stream is served (no replay). This keeps the connection robust.

2) Replay ownership
- Problem: `Last-Event-ID` could reference notifications that belong to another user.
- Risk: Unauthorized data disclosure if owner checks are not enforced.
- Handling: When replaying, the server verifies notification ownership against the authenticated user and only replays notifications that belong to them.

3) Duplicate delivery / idempotency
- Problem: Pipeline might attempt to create the same logical notification multiple times (retries, race conditions
- Handling: Database-level uniqueness constraint (user, medical_case, type) plus repository idempotent lookup `get_by_user_case_and_type`. Pipeline persists the notification before publishing; repository returns existing record if present.
).
- Risk: Users seeing duplicate notifications; event bus receiving multiple publishes for the same logical event.
4) Preference toggling (notify_on_complete)
- Problem: Users may disable notifications; pipeline should respect this.
- Risk: Sending unwanted notifications and creating DB rows for disabled users.
- Handling: Pipeline checks `User.notify_on_complete` before creating/persisting notification and before publishing. If disabled, it returns early.

5) Live vs Replay race
- Problem: A notification might be replayed from DB at connection time while a live event arrives via EventBus almost simultaneously.
- Risk: Duplicate delivery to client.
- Handling: Replay uses chronological ordering and marks `delivered_at` when replayed. Live event handler checks whether notification has already been marked `delivered_at` (or uses seen set based on UUID) to avoid re-sending the same notification.

6) Keepalive and proxy timeouts
- Problem: Proxies and load balancers may close idle connections.
- Handling: Server sends SSE comment `: ping` every 30 seconds to keep the connection alive.

7) EventBus reliability and cleanup
- Problem: Redis/subscribe resources could leak if the client disconnects unexpectedly.
- Handling: Generators and subscriber tasks are cancellation-safe; on disconnect, consumer tasks are cancelled and any `unsubscribe` cleanup is invoked. No background infinite loops are left running.

8) Large payloads and memory
- Problem: Large notifications or bursts might cause memory pressure when buffering.
- Handling: SSE writes events directly via streaming generator without buffering large batches. Event payloads are kept reasonably small; if a notification has large `data`, it is truncated or summarized before publishing (pipeline/service level guidance).

9) Invalid event types from EventBus
- Problem: EventBus payloads may be malformed or contain unknown event types.
- Handling: Event consumer validates event payloads (presence of `notification_id`, valid UUID) and ignores invalid events; errors are logged but do not crash the stream.

10) Concurrent writes and DB transaction ordering
- Problem: Two concurrent pipeline tasks could race to create the same notification.
- Handling: Repository implements get-or-create semantics within the same DB session and relies on the DB unique constraint as the final guard. Code catches unique-constraint collisions gracefully.
