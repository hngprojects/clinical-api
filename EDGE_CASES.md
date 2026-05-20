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
