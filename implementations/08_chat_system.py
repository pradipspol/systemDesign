"""
=============================================================
  8. Chat System — WebSocket Server with Rooms & Presence
  Run: python 08_chat_system.py
  Then open multiple terminals:
    python -c "import asyncio, websockets; asyncio.run(client())"
  Or use the built-in test client (runs automatically).
=============================================================
"""
import asyncio
import json
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional


# ===================================================================
# Data Models
# ===================================================================
@dataclass
class ChatMessage:
    message_id: str
    room_id: str
    sender_id: str
    content: str
    timestamp: float = field(default_factory=time.time)
    message_type: str = "text"  # text, image, system
    read_by: set = field(default_factory=set)


@dataclass
class ChatRoom:
    room_id: str
    name: str
    room_type: str = "group"  # group, direct
    members: set = field(default_factory=set)
    messages: list = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    max_history: int = 1000


@dataclass
class UserPresence:
    user_id: str
    status: str = "offline"  # online, offline, away
    last_seen: float = field(default_factory=time.time)


# ===================================================================
# Chat Server Engine (protocol-agnostic)
# ===================================================================
class ChatEngine:
    def __init__(self):
        self.rooms: dict[str, ChatRoom] = {}
        self.users: dict[str, UserPresence] = {}
        self.user_rooms: dict[str, set] = defaultdict(set)
        self._connections: dict[str, set] = defaultdict(set)  # room_id -> {user_ids}
        self._stats = {"messages_sent": 0, "rooms_created": 0, "users_online": 0}

    def register_user(self, user_id: str) -> UserPresence:
        if user_id not in self.users:
            self.users[user_id] = UserPresence(user_id=user_id)
        return self.users[user_id]

    def set_online(self, user_id: str):
        self.register_user(user_id)
        self.users[user_id].status = "online"
        self.users[user_id].last_seen = time.time()
        self._stats["users_online"] = sum(1 for u in self.users.values() if u.status == "online")

    def set_offline(self, user_id: str):
        if user_id in self.users:
            self.users[user_id].status = "offline"
            self.users[user_id].last_seen = time.time()
            self._stats["users_online"] = sum(1 for u in self.users.values() if u.status == "online")

    def create_room(self, name: str, room_type: str = "group", members: list[str] = None) -> ChatRoom:
        room = ChatRoom(
            room_id=str(uuid.uuid4())[:8],
            name=name,
            room_type=room_type,
            members=set(members or []),
        )
        self.rooms[room.room_id] = room
        for uid in room.members:
            self.user_rooms[uid].add(room.room_id)
        self._stats["rooms_created"] += 1
        return room

    def join_room(self, user_id: str, room_id: str) -> Optional[ChatRoom]:
        room = self.rooms.get(room_id)
        if not room:
            return None
        room.members.add(user_id)
        self.user_rooms[user_id].add(room_id)
        self._connections[room_id].add(user_id)
        return room

    def leave_room(self, user_id: str, room_id: str):
        room = self.rooms.get(room_id)
        if room:
            room.members.discard(user_id)
            self.user_rooms[user_id].discard(room_id)
            self._connections[room_id].discard(user_id)

    def send_message(self, room_id: str, sender_id: str, content: str,
                     message_type: str = "text") -> Optional[ChatMessage]:
        room = self.rooms.get(room_id)
        if not room or sender_id not in room.members:
            return None

        msg = ChatMessage(
            message_id=str(uuid.uuid4())[:8],
            room_id=room_id,
            sender_id=sender_id,
            content=content,
            message_type=message_type,
        )
        room.messages.append(msg)
        if len(room.messages) > room.max_history:
            room.messages = room.messages[-room.max_history:]
        self._stats["messages_sent"] += 1
        return msg

    def get_history(self, room_id: str, limit: int = 50, before_ts: float = None) -> list[ChatMessage]:
        room = self.rooms.get(room_id)
        if not room:
            return []
        msgs = room.messages
        if before_ts:
            msgs = [m for m in msgs if m.timestamp < before_ts]
        return msgs[-limit:]

    def mark_read(self, user_id: str, room_id: str, message_id: str):
        room = self.rooms.get(room_id)
        if room:
            for msg in reversed(room.messages):
                if msg.message_id == message_id:
                    msg.read_by.add(user_id)
                    break

    def get_unread_count(self, user_id: str, room_id: str) -> int:
        room = self.rooms.get(room_id)
        if not room:
            return 0
        return sum(1 for m in room.messages if user_id not in m.read_by and m.sender_id != user_id)

    def search_messages(self, room_id: str, query: str, limit: int = 20) -> list[ChatMessage]:
        room = self.rooms.get(room_id)
        if not room:
            return []
        query_lower = query.lower()
        results = [m for m in room.messages if query_lower in m.content.lower()]
        return results[-limit:]

    def get_presence(self, user_ids: list[str]) -> dict[str, dict]:
        return {
            uid: {
                "status": self.users[uid].status,
                "last_seen": self.users[uid].last_seen,
            }
            for uid in user_ids if uid in self.users
        }

    def stats(self) -> dict:
        return {
            **self._stats,
            "total_rooms": len(self.rooms),
            "total_users": len(self.users),
        }


# ===================================================================
# WebSocket Server (async)
# ===================================================================
class WebSocketChatServer:
    def __init__(self, host: str = "localhost", port: int = 8765):
        self.host = host
        self.port = port
        self.engine = ChatEngine()
        self.connections: dict[str, set] = {}  # user_id -> {websocket}

    async def handler(self, websocket):
        user_id = None
        try:
            async for raw in websocket:
                data = json.loads(raw)
                action = data.get("action")
                response = {"status": "error", "message": "Unknown action"}

                if action == "login":
                    user_id = data["user_id"]
                    self.engine.set_online(user_id)
                    self.connections.setdefault(user_id, set()).add(websocket)
                    response = {"status": "ok", "action": "login", "user_id": user_id}

                elif action == "join":
                    room = self.engine.join_room(user_id, data["room_id"])
                    response = {"status": "ok", "action": "joined", "room_id": data["room_id"]}

                elif action == "send":
                    msg = self.engine.send_message(data["room_id"], user_id, data["content"])
                    if msg:
                        # Broadcast to room members
                        room = self.engine.rooms[data["room_id"]]
                        broadcast = json.dumps({
                            "action": "message",
                            "room_id": msg.room_id,
                            "sender": msg.sender_id,
                            "content": msg.content,
                            "timestamp": msg.timestamp,
                            "message_id": msg.message_id,
                        })
                        for member in room.members:
                            for ws in self.connections.get(member, set()):
                                if ws != websocket:
                                    try:
                                        await ws.send(broadcast)
                                    except Exception:
                                        pass
                        response = {"status": "ok", "action": "sent", "message_id": msg.message_id}

                elif action == "history":
                    msgs = self.engine.get_history(data["room_id"], limit=data.get("limit", 50))
                    response = {
                        "status": "ok",
                        "action": "history",
                        "messages": [
                            {"sender": m.sender_id, "content": m.content, "timestamp": m.timestamp}
                            for m in msgs
                        ],
                    }

                elif action == "presence":
                    presence = self.engine.get_presence(data.get("user_ids", []))
                    response = {"status": "ok", "action": "presence", "data": presence}

                await websocket.send(json.dumps(response))

        except Exception:
            pass
        finally:
            if user_id:
                self.engine.set_offline(user_id)
                self.connections.get(user_id, set()).discard(websocket)


# ===================================================================
# Demo (non-WebSocket, uses engine directly)
# ===================================================================
def run_demo():
    print("=" * 65)
    print("  Chat System — WebSocket Chat Engine Demo")
    print("=" * 65)

    engine = ChatEngine()

    # Register users
    for uid in ["alice", "bob", "charlie", "diana"]:
        engine.register_user(uid)
        engine.set_online(uid)

    # Create rooms
    general = engine.create_room("General", "group", ["alice", "bob", "charlie", "diana"])
    dm = engine.create_room("alice-bob-dm", "direct", ["alice", "bob"])

    print(f"\n  Created rooms: {general.name} ({general.room_id}), {dm.name} ({dm.room_id})")

    # Send messages
    msgs = [
        engine.send_message(general.room_id, "alice", "Hey everyone! 👋"),
        engine.send_message(general.room_id, "bob", "Hi Alice! How's the system design going?"),
        engine.send_message(general.room_id, "charlie", "I just finished the cache chapter"),
        engine.send_message(general.room_id, "alice", "Nice! I'm working on the chat system now 😄"),
        engine.send_message(general.room_id, "diana", "Can someone explain consistent hashing?"),
        engine.send_message(dm.room_id, "alice", "Bob, can you review my PR?"),
        engine.send_message(dm.room_id, "bob", "Sure, I'll look at it after lunch"),
    ]

    # Display conversation
    print(f"\n  #{general.name} conversation:")
    for m in engine.get_history(general.room_id):
        ts = time.strftime("%H:%M", time.localtime(m.timestamp))
        print(f"    [{ts}] {m.sender_id}: {m.content}")

    print(f"\n  DM {dm.name}:")
    for m in engine.get_history(dm.room_id):
        ts = time.strftime("%H:%M", time.localtime(m.timestamp))
        print(f"    [{ts}] {m.sender_id}: {m.content}")

    # Read receipts
    engine.mark_read("bob", general.room_id, msgs[3].message_id)
    print(f"\n  Unread count for diana in #{general.name}: "
          f"{engine.get_unread_count('diana', general.room_id)}")
    print(f"  Unread count for bob in #{general.name}: "
          f"{engine.get_unread_count('bob', general.room_id)}")

    # Search
    results = engine.search_messages(general.room_id, "system design")
    print(f"\n  Search 'system design' in #{general.name}: {len(results)} results")
    for m in results:
        print(f"    {m.sender_id}: {m.content}")

    # Presence
    engine.set_offline("charlie")
    presence = engine.get_presence(["alice", "bob", "charlie"])
    print(f"\n  Presence: ", end="")
    for uid, p in presence.items():
        print(f"{uid}={p['status']}  ", end="")
    print()

    # Stats
    print(f"\n  Stats: {engine.stats()}")
    print("\nDone.")
    print("\n  To run WebSocket server: uncomment the server block below and")
    print("  connect with a WebSocket client on ws://localhost:8765")


if __name__ == "__main__":
    run_demo()

    # Uncomment to run actual WebSocket server:
    # import websockets
    # server = WebSocketChatServer()
    # print("\n  Starting WebSocket server on ws://localhost:8765 ...")
    # asyncio.run(websockets.serve(server.handler, server.host, server.port))
