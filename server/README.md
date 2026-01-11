# RCord Server

## Configuration

The server uses environment variables:

- `RCORD_HOST` (default: `0.0.0.0`)
- `RCORD_PORT` (default: `8765`)
- `RCORD_DB_PATH` (default: `DB.dat`)
- `RCORD_HEARTBEAT_TIMEOUT` (seconds, default: `60`)
- `RCORD_CHECK_INTERVAL` (seconds, default: `10`)

## Running

```bash
cd server
python main.py
```

The server will create `DB.dat` on first start in the current working directory.

`DB.dat` is a JSON file with an integrity checksum and metadata. The file uses a
top-level object with `format`, `version`, `data`, and `checksum` fields. The
`data` object stores users, rooms, chats, messages, invites, and status entries.
Older files that only contain the `data` object are still supported.

## Protocol

All messages are newline-delimited JSON objects (`\n` terminated). Example:

```json
{"action": "register", "username": "alice", "password": "secret"}
```

Supported actions:

- `register` `{username, password}`
- `login` `{username, password}`
- `heartbeat`
- `list_users`
- `list_rooms`
- `list_chats`
- `list_invites`
- `create_room` `{room}`
- `join_room` `{room}`
- `invite_room` `{room, username}`
- `create_chat` `{username}`
- `accept_chat` `{chat}`
- `send_message` `{room|chat, kind, text? filename? content?}`
- `list_messages` `{room|chat, limit?}`
- `logout`

Message kinds:

- `text` uses `{text}`
- `file` uses `{filename, content}` (base64)
- `image` uses `{filename, content}` (base64)

## Invite expiration

Room and chat invites expire 5 minutes after `invited_at` (UTC). Expired invites
are removed automatically during invite listing and invite validation.

### Manual test scenario

1. Start the server and register two users (e.g., `alice`, `bob`).
2. Create a room as `alice` and invite `bob`.
3. Verify `bob` sees the invite via `list_invites`.
4. Edit `DB.dat` and set the invite's `invited_at` to more than 5 minutes ago
   (e.g., `2020-01-01T00:00:00+00:00`).
5. Call `list_invites` or attempt `join_room`/`accept_chat` as `bob` and confirm
   the invite is removed and the response indicates `invite_expired`.
