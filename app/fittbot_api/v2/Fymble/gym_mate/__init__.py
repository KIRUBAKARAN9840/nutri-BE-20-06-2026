"""gym_mate — the social / matching feature set for Fymble.

Sub-modules (each a self-contained bounded context):
    profile       — onboarding form, profile read, settings  (this iteration)
    friends       — friend requests, mutual graph, suggestions
    sessions      — GymMate sessions, join requests, matching
    stories       — 24h ephemeral stories
    chat          — DMs + group chat for matched sessions
    notifications — bell feed + push triggers

Inter-module communication is via each sub-module's `api.py` Protocol
(for queries) and `_events.py` events (for side effects). Sub-modules
must never import each other's `_service`, `_repository`, or `_domain`.
"""
